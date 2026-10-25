"""Deterministic in-process PayPal sandbox (``sandbox_simulation`` mode).

An ``httpx`` transport that answers the Orders v2, Payouts v1, OAuth2 and webhook-verification
endpoints with PayPal-shaped JSON. It honours ``PayPal-Request-Id`` replay semantics like the
real API, so idempotency bugs surface here exactly as they would against PayPal.

Ids are derived from request ids (``SIM-ORDER-...``, ``SIM-CAPTURE-...``, ``SIM-PAYOUT-...``),
so identical inputs always yield identical ids. Simulated orders are treated as buyer-approved.

``inject()`` queues faults for tests and demos, including ``lost_response`` (PayPal performs
the operation but the HTTP response never arrives), the hardest case for payment idempotency.
"""

import hashlib
import hmac
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Literal

import httpx

from app.core.config import Settings
from app.services.paypal_client import format_cents_for_paypal, parse_paypal_amount

SIM_WEBHOOK_ID: Final = "SIM-WEBHOOK-ID"
SIM_TOKEN: Final = "SIM-ACCESS-TOKEN"
SIM_CERT_URL: Final = "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-SIM"
_EMAIL_RE: Final = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

Operation = Literal["token", "create_order", "capture", "get_order", "payout", "get_payout"]


def _digest(*parts: str, length: int = 16) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:length].upper()


def _canonical(event: dict[str, Any]) -> str:
    return json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def simulated_webhook_signature(
    secret: str, *, transmission_id: str, transmission_time: str, webhook_id: str, event: dict
) -> str:
    message = "|".join(
        [
            transmission_id,
            transmission_time,
            webhook_id,
            hashlib.sha256(_canonical(event).encode()).hexdigest(),
        ]
    )
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def simulate_webhook_headers(
    settings: Settings, event: dict[str, Any], *, transmission_id: str | None = None
) -> dict[str, str]:
    """Headers a simulated PayPal delivery of ``event`` would carry (for demos and tests)."""
    tid = transmission_id or f"SIM-TX-{_digest(str(event.get('id', '')))}"
    ttime = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    sig = simulated_webhook_signature(
        settings.paypal_simulation_secret,
        transmission_id=tid,
        transmission_time=ttime,
        webhook_id=settings.paypal_webhook_id.strip() or SIM_WEBHOOK_ID,
        event=event,
    )
    return {
        "paypal-auth-algo": "SHA256withRSA",
        "paypal-cert-url": SIM_CERT_URL,
        "paypal-transmission-id": tid,
        "paypal-transmission-sig": sig,
        "paypal-transmission-time": ttime,
    }


@dataclass
class _Fault:
    status: int | None = None
    body: dict[str, Any] | None = None
    exc: type[Exception] | None = None
    lost_response: bool = False


@dataclass
class _SimOrder:
    order_id: str
    amount_cents: int
    currency: str
    status: str = "PAYER_ACTION_REQUIRED"
    capture_id: str | None = None
    raw_request: dict[str, Any] = field(default_factory=dict)


@dataclass
class _SimBatch:
    batch_id: str
    sender_batch_id: str
    status: str
    items: list[dict[str, Any]]


class PayPalSimulator:
    def __init__(self, settings: Settings, *, instant_payouts: bool = True) -> None:
        self.settings = settings
        self.instant_payouts = instant_payouts
        self.orders: dict[str, _SimOrder] = {}
        self.batches: dict[str, _SimBatch] = {}
        self.batches_by_sender_id: dict[str, str] = {}
        #: Every payout item that actually "moved money": (receiver, cents, sender_batch_id).
        self.disbursements: list[tuple[str, int, str]] = []
        self.requests: list[httpx.Request] = []
        self.calls: defaultdict[str, int] = defaultdict(int)
        self._replay: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
        self._faults: defaultdict[str, list[_Fault]] = defaultdict(list)
        self.transport = httpx.MockTransport(self.handle)

    # --- test / demo controls -------------------------------------------------------------

    def inject(
        self,
        operation: Operation,
        *,
        status: int | None = None,
        body: dict[str, Any] | None = None,
        exc: type[Exception] | None = None,
        lost_response: bool = False,
        times: int = 1,
    ) -> None:
        for _ in range(times):
            self._faults[operation].append(_Fault(status, body, exc, lost_response))

    def settle_batch(self, batch_id: str, status: str = "SUCCESS") -> None:
        batch = self.batches[batch_id]
        batch.status = status
        for item in batch.items:
            item["transaction_status"] = "SUCCESS" if status == "SUCCESS" else "FAILED"

    def mark_order_unapproved(self, order_id: str) -> None:
        self.orders[order_id].status = "PAYER_ACTION_REQUIRED_STRICT"

    # --- routing --------------------------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, method = request.url.path, request.method
        if path == "/v1/oauth2/token" and method == "POST":
            return self._dispatch("token", request, self._token)
        if not request.headers.get("authorization", "").startswith("Bearer "):
            return self._error(401, "AUTHENTICATION_FAILURE", "Missing bearer token")
        if path == "/v2/checkout/orders" and method == "POST":
            return self._dispatch("create_order", request, self._create_order)
        if m := re.fullmatch(r"/v2/checkout/orders/([A-Za-z0-9-]+)/capture", path):
            return self._dispatch("capture", request, lambda r: self._capture(r, m.group(1)))
        if (m := re.fullmatch(r"/v2/checkout/orders/([A-Za-z0-9-]+)", path)) and method == "GET":
            return self._dispatch("get_order", request, lambda r: self._get_order(m.group(1)))
        if path == "/v1/payments/payouts" and method == "POST":
            return self._dispatch("payout", request, self._create_payout)
        if (m := re.fullmatch(r"/v1/payments/payouts/([A-Za-z0-9-]+)", path)) and method == "GET":
            return self._dispatch("get_payout", request, lambda r: self._get_payout(m.group(1)))
        if path == "/v1/notifications/verify-webhook-signature" and method == "POST":
            return self._verify_webhook(request)
        return self._error(404, "RESOURCE_NOT_FOUND", f"No route {method} {path}")

    def _dispatch(self, operation: str, request: httpx.Request, handler: Any) -> httpx.Response:
        self.calls[operation] += 1
        request_id = request.headers.get("paypal-request-id")
        replay_key = (request.url.path, request_id) if request_id else None
        fault = self._faults[operation].pop(0) if self._faults[operation] else None
        if fault and not fault.lost_response:
            if fault.exc is not None:
                raise fault.exc("simulated network failure", request=request)
            return httpx.Response(fault.status or 500, json=fault.body or {"name": "INTERNAL"})
        if replay_key and replay_key in self._replay:
            status, body = self._replay[replay_key]
        else:
            response = handler(request)
            status, body = response.status_code, response.json()
            if replay_key and status < 500:
                self._replay[replay_key] = (status, body)
        if fault and fault.lost_response:
            raise httpx.ReadTimeout("simulated lost response", request=request)
        return httpx.Response(status, json=body)

    @staticmethod
    def _error(status: int, name: str, message: str, issue: str | None = None) -> httpx.Response:
        body: dict[str, Any] = {"name": name, "message": message, "debug_id": "SIMDEBUG"}
        if issue:
            body["details"] = [{"issue": issue, "description": message}]
        return httpx.Response(status, json=body)

    # --- handlers -------------------------------------------------------------------------

    def _token(self, request: httpx.Request) -> httpx.Response:
        if not request.headers.get("authorization", "").startswith("Basic "):
            return httpx.Response(401, json={"error": "invalid_client"})
        return httpx.Response(
            200,
            json={
                "scope": "https://uri.paypal.com/services/payments/payouts",
                "access_token": SIM_TOKEN,
                "token_type": "Bearer",
                "app_id": "APP-SIMULATION",
                "expires_in": 32400,
                "nonce": "sim",
            },
        )

    def _create_order(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        unit = body["purchase_units"][0]
        amount = unit["amount"]
        total = parse_paypal_amount(amount["value"])
        item_sum = sum(
            parse_paypal_amount(i["unit_amount"]["value"]) * int(i["quantity"])
            for i in unit.get("items", [])
        )
        item_total = parse_paypal_amount(amount["breakdown"]["item_total"]["value"])
        if not (item_sum == item_total == total):
            return self._error(
                422,
                "UNPROCESSABLE_ENTITY",
                "Item amounts do not sum to total",
                "ITEM_TOTAL_MISMATCH",
            )
        seed = request.headers.get("paypal-request-id") or unit["reference_id"]
        order_id = f"SIM-ORDER-{_digest(seed, length=17)}"
        self.orders[order_id] = _SimOrder(
            order_id, total, amount["currency_code"], raw_request=body
        )
        approve = f"https://www.sandbox.paypal.com/checkoutnow?token={order_id}"
        return httpx.Response(
            201,
            json={
                "id": order_id,
                "status": "PAYER_ACTION_REQUIRED",
                "payment_source": {"paypal": {}},
                "links": [
                    {
                        "href": f"{self.settings.paypal_base_url}/v2/checkout/orders/{order_id}",
                        "rel": "self",
                        "method": "GET",
                    },
                    {"href": approve, "rel": "payer-action", "method": "GET"},
                ],
            },
        )

    def _order_body(self, order: _SimOrder) -> dict[str, Any]:
        captures = []
        if order.capture_id:
            captures.append(
                {
                    "id": order.capture_id,
                    "status": "COMPLETED",
                    "amount": {
                        "currency_code": order.currency,
                        "value": format_cents_for_paypal(order.amount_cents),
                    },
                    "final_capture": True,
                }
            )
        return {
            "id": order.order_id,
            "status": order.status,
            "purchase_units": [{"reference_id": "default", "payments": {"captures": captures}}],
        }

    def _capture(self, request: httpx.Request, order_id: str) -> httpx.Response:
        order = self.orders.get(order_id)
        if order is None:
            # Unknown (e.g. the simulator restarted): like PayPal, refuse rather than invent a
            # capture whose amount could not be verified against the ledger.
            return self._error(404, "RESOURCE_NOT_FOUND", "Order not found", "INVALID_RESOURCE_ID")
        if order.status == "PAYER_ACTION_REQUIRED_STRICT":
            return self._error(
                422, "UNPROCESSABLE_ENTITY", "Payer has not approved", "ORDER_NOT_APPROVED"
            )
        if order.status == "COMPLETED":
            return self._error(
                422, "UNPROCESSABLE_ENTITY", "Order already captured", "ORDER_ALREADY_CAPTURED"
            )
        order.status = "COMPLETED"
        order.capture_id = f"SIM-CAPTURE-{_digest(order_id)}"
        return httpx.Response(201, json=self._order_body(order))

    def _get_order(self, order_id: str) -> httpx.Response:
        order = self.orders.get(order_id)
        if order is None:
            return self._error(404, "RESOURCE_NOT_FOUND", "Order not found", "INVALID_RESOURCE_ID")
        return httpx.Response(200, json=self._order_body(order))

    def _batch_body(self, batch: _SimBatch) -> dict[str, Any]:
        return {
            "batch_header": {
                "payout_batch_id": batch.batch_id,
                "batch_status": batch.status,
                "sender_batch_header": {"sender_batch_id": batch.sender_batch_id},
            },
            "items": batch.items,
            "links": [],
        }

    def _create_payout(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sender_batch_id = body["sender_batch_header"]["sender_batch_id"]
        if sender_batch_id in self.batches_by_sender_id:
            return self._error(
                400,
                "USER_BUSINESS_ERROR",
                "Batch with given sender_batch_id already exists",
                "DUPLICATE_REQUEST_ID",
            )
        items = []
        for raw in body["items"]:
            if not _EMAIL_RE.match(raw.get("receiver", "")):
                return self._error(
                    422, "VALIDATION_ERROR", "Invalid receiver", "RECEIVER_UNREGISTERED"
                )
            cents = parse_paypal_amount(raw["amount"]["value"])
            if cents <= 0:
                return self._error(422, "VALIDATION_ERROR", "Amount must be > 0", "AMOUNT_INVALID")
            items.append(
                {
                    "payout_item_id": f"SIM-ITEM-{_digest(sender_batch_id, raw['sender_item_id'])}",
                    "transaction_status": "SUCCESS" if self.instant_payouts else "PENDING",
                    "payout_item": {
                        "amount": raw["amount"],
                        "receiver": raw["receiver"],
                        "sender_item_id": raw["sender_item_id"],
                    },
                }
            )
            self.disbursements.append((raw["receiver"], cents, sender_batch_id))
        batch_id = f"SIM-PAYOUT-{_digest(sender_batch_id)}"
        batch = _SimBatch(
            batch_id, sender_batch_id, "SUCCESS" if self.instant_payouts else "PENDING", items
        )
        self.batches[batch_id] = batch
        self.batches_by_sender_id[sender_batch_id] = batch_id
        return httpx.Response(201, json=self._batch_body(batch))

    def _get_payout(self, batch_id: str) -> httpx.Response:
        batch = self.batches.get(batch_id)
        if batch is None:
            return self._error(404, "RESOURCE_NOT_FOUND", "Batch not found", "INVALID_RESOURCE_ID")
        return httpx.Response(200, json=self._batch_body(batch))

    def _verify_webhook(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        expected = simulated_webhook_signature(
            self.settings.paypal_simulation_secret,
            transmission_id=body.get("transmission_id", ""),
            transmission_time=body.get("transmission_time", ""),
            webhook_id=body.get("webhook_id", ""),
            event=body.get("webhook_event") or {},
        )
        ok = hmac.compare_digest(expected, str(body.get("transmission_sig", "")))
        return httpx.Response(200, json={"verification_status": "SUCCESS" if ok else "FAILURE"})
