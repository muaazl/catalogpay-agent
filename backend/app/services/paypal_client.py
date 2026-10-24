"""PayPal REST client: OAuth2, Orders v2, Payouts v1, webhook signature verification.

The client always speaks real HTTP through ``httpx``. In ``sandbox_simulation`` mode (no
credentials, mock fallback enabled) the transport is :class:`PayPalSimulator`, which answers
with PayPal-shaped payloads, so request building, idempotency headers, response parsing and
error mapping are exercised identically in both modes.

Money crosses this boundary only as integer cents <-> exact 2-decimal strings (``Decimal``).
"""

import asyncio
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Final

import httpx

from app.core.config import Settings
from app.core.errors import DomainError
from app.models.order import PayPalMode
from app.services.money import require_int

TOKEN_REFRESH_MARGIN_SECONDS: Final = 60
_ID_RE: Final = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_CENT: Final = Decimal("0.01")


# --------------------------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------------------------


class PayPalError(DomainError):
    status_code, code = 502, "paypal_error"
    #: True when PayPal may have performed the operation (timeout, 5xx). Callers must retry
    #: with the *same* PayPal-Request-Id rather than assume nothing happened.
    ambiguous: bool = False


class PayPalNotConfiguredError(PayPalError):
    status_code, code = 503, "paypal_not_configured"


class PayPalUnavailableError(PayPalError):
    status_code, code = 503, "paypal_unavailable"
    ambiguous = True


class PayPalAPIError(PayPalError):
    """A definitive rejection (4xx). PayPal did not perform the operation."""

    status_code, code = 502, "paypal_api_error"

    def __init__(
        self,
        message: str,
        *,
        http_status: int,
        name: str | None = None,
        issues: Sequence[str] = (),
        debug_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.name = name
        self.issues = tuple(issues)
        self.debug_id = debug_id

    def has_issue(self, *issues: str) -> bool:
        return any(i in self.issues for i in issues) or (self.name in issues)


# --------------------------------------------------------------------------------------------
# Money formatting
# --------------------------------------------------------------------------------------------


def format_cents_for_paypal(cents: int) -> str:
    """``1250`` -> ``"12.50"`` using exact Decimal arithmetic. Never floats."""
    require_int("cents", cents)
    if cents < 0:
        raise ValueError("PayPal amounts must be non-negative")
    return str((Decimal(cents) / 100).quantize(_CENT))


def parse_paypal_amount(value: str) -> int:
    """``"12.50"`` -> ``1250``. Rejects anything that is not an exact cent amount."""
    if not isinstance(value, str):
        raise ValueError("PayPal amount must be a string")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid PayPal amount {value!r}") from exc
    cents = amount * 100
    if not amount.is_finite() or cents != cents.to_integral_value() or amount < 0:
        raise ValueError(f"Invalid PayPal amount {value!r}")
    return int(cents)


# --------------------------------------------------------------------------------------------
# Request / result types
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PayPalItem:
    name: str
    sku: str
    quantity: int
    unit_amount_cents: int


@dataclass(frozen=True, slots=True)
class PayoutItem:
    receiver_email: str
    amount_cents: int
    sender_item_id: str
    note: str


@dataclass(frozen=True, slots=True)
class PayPalOrderResult:
    order_id: str
    status: str
    approval_url: str | None
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PayPalCaptureResult:
    order_id: str
    status: str
    capture_id: str | None
    capture_status: str | None
    amount_cents: int | None
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PayoutItemResult:
    sender_item_id: str | None
    payout_item_id: str | None
    transaction_status: str | None


@dataclass(frozen=True, slots=True)
class PayPalPayoutBatchResult:
    payout_batch_id: str
    batch_status: str
    items: tuple[PayoutItemResult, ...]
    raw: dict[str, Any]


def build_order_payload(
    *,
    reference_id: str,
    items: Sequence[PayPalItem],
    currency: str,
    return_url: str,
    cancel_url: str,
    brand_name: str,
    description: str | None = None,
) -> dict[str, Any]:
    """Orders v2 body. Verifies sum(unit_amount * quantity) == item_total == amount.value."""
    if not items:
        raise ValueError("A PayPal order needs at least one item")
    total_cents = 0
    pp_items = []
    for item in items:
        require_int("quantity", item.quantity)
        if item.quantity < 1:
            raise ValueError("item quantity must be >= 1")
        total_cents += item.unit_amount_cents * item.quantity
        pp_items.append(
            {
                "name": item.name[:127],
                "sku": item.sku[:127],
                "quantity": str(item.quantity),
                "unit_amount": {
                    "currency_code": currency,
                    "value": format_cents_for_paypal(item.unit_amount_cents),
                },
                "category": "PHYSICAL_GOODS",
            }
        )
    total = format_cents_for_paypal(total_cents)
    breakdown_sum = sum(
        parse_paypal_amount(i["unit_amount"]["value"]) * int(i["quantity"]) for i in pp_items
    )
    if breakdown_sum != parse_paypal_amount(total):
        raise AssertionError("PayPal item breakdown does not sum to the order amount")
    unit: dict[str, Any] = {
        "reference_id": reference_id,
        "custom_id": reference_id,
        "invoice_id": reference_id,
        "amount": {
            "currency_code": currency,
            "value": total,
            "breakdown": {"item_total": {"currency_code": currency, "value": total}},
        },
        "items": pp_items,
    }
    if description:
        unit["description"] = description[:127]
    return {
        "intent": "CAPTURE",
        "purchase_units": [unit],
        "payment_source": {
            "paypal": {
                "experience_context": {
                    "brand_name": brand_name,
                    "user_action": "PAY_NOW",
                    "shipping_preference": "NO_SHIPPING",
                    "return_url": return_url,
                    "cancel_url": cancel_url,
                }
            }
        },
    }


def build_payout_payload(
    *,
    sender_batch_id: str,
    items: Sequence[PayoutItem],
    currency: str,
    email_subject: str,
    email_message: str,
) -> dict[str, Any]:
    if not items:
        raise ValueError("A payout batch needs at least one item")
    return {
        "sender_batch_header": {
            "sender_batch_id": sender_batch_id,
            "email_subject": email_subject,
            "email_message": email_message,
            "recipient_type": "EMAIL",
        },
        "items": [
            {
                "recipient_type": "EMAIL",
                "amount": {
                    "value": format_cents_for_paypal(i.amount_cents),
                    "currency": currency,
                },
                "receiver": i.receiver_email,
                "sender_item_id": i.sender_item_id,
                "note": i.note[:4000],
                "recipient_wallet": "PAYPAL",
            }
            for i in items
        ],
    }


def _parse_capture(body: dict[str, Any]) -> PayPalCaptureResult:
    capture: dict[str, Any] = {}
    for unit in body.get("purchase_units") or []:
        captures = (unit.get("payments") or {}).get("captures") or []
        if captures:
            capture = captures[0]
            break
    amount = (capture.get("amount") or {}).get("value")
    return PayPalCaptureResult(
        order_id=str(body.get("id", "")),
        status=str(body.get("status", "")),
        capture_id=capture.get("id"),
        capture_status=capture.get("status"),
        amount_cents=parse_paypal_amount(amount) if isinstance(amount, str) else None,
        raw=body,
    )


def _parse_payout_batch(body: dict[str, Any]) -> PayPalPayoutBatchResult:
    header = body.get("batch_header") or {}
    batch_id = header.get("payout_batch_id")
    if not batch_id:
        raise PayPalUnavailableError("PayPal payout response had no payout_batch_id.")
    items = tuple(
        PayoutItemResult(
            sender_item_id=(i.get("payout_item") or {}).get("sender_item_id"),
            payout_item_id=i.get("payout_item_id"),
            transaction_status=i.get("transaction_status"),
        )
        for i in body.get("items") or []
    )
    return PayPalPayoutBatchResult(
        payout_batch_id=str(batch_id),
        batch_status=str(header.get("batch_status", "PENDING")),
        items=items,
        raw=body,
    )


def _safe_id(value: str, what: str) -> str:
    if not _ID_RE.match(value or ""):
        raise PayPalAPIError(f"Invalid {what} {value!r}", http_status=400, name="INVALID_ID")
    return value


WEBHOOK_HEADERS: Final = {
    "auth_algo": "paypal-auth-algo",
    "cert_url": "paypal-cert-url",
    "transmission_id": "paypal-transmission-id",
    "transmission_sig": "paypal-transmission-sig",
    "transmission_time": "paypal-transmission-time",
}


# --------------------------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------------------------


class PayPalClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self._clock = clock
        self.simulator: Any = None
        if settings.paypal_credentials_configured:
            self.mode: PayPalMode | None = PayPalMode.LIVE_SANDBOX
            self._client_id = settings.paypal_client_id.strip()
            self._client_secret = settings.paypal_client_secret.strip()
        elif settings.paypal_sandbox_mock_fallback:
            from app.services.paypal_simulator import PayPalSimulator

            self.mode = PayPalMode.SANDBOX_SIMULATION
            self._client_id, self._client_secret = "SIM-CLIENT-ID", "SIM-CLIENT-SECRET"
            if transport is None:
                self.simulator = PayPalSimulator(settings)
                transport = self.simulator.transport
        else:
            self.mode = None
            self._client_id = self._client_secret = ""
        self._transport = transport
        self._http: httpx.AsyncClient | None = None
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock: asyncio.Lock | None = None
        self.token_requests = 0

    @property
    def is_configured(self) -> bool:
        return self.mode is not None

    @property
    def webhook_id(self) -> str:
        from app.services.paypal_simulator import SIM_WEBHOOK_ID

        configured = self.settings.paypal_webhook_id.strip()
        if self.mode is PayPalMode.SANDBOX_SIMULATION:
            return configured or SIM_WEBHOOK_ID
        return configured

    def _require_configured(self) -> None:
        if self.mode is None:
            raise PayPalNotConfiguredError(
                "PayPal is not configured: set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET, "
                "or enable PAYPAL_SANDBOX_MOCK_FALLBACK."
            )

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            timeout = self.settings.paypal_timeout_seconds
            self._http = httpx.AsyncClient(
                base_url=self.settings.paypal_base_url,
                transport=self._transport,
                timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0)),
                headers={"Accept": "application/json"},
            )
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # --- transport helpers -----------------------------------------------------------------

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._client().request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise PayPalUnavailableError(f"PayPal {method} {path} timed out.") from exc
        except httpx.TransportError as exc:
            raise PayPalUnavailableError(f"Could not reach PayPal ({type(exc).__name__}).") from exc

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return
        status = response.status_code
        if status == 429 or status >= 500:
            raise PayPalUnavailableError(f"PayPal returned HTTP {status}.")
        try:
            body = response.json()
        except ValueError:
            body = {}
        body = body if isinstance(body, dict) else {}
        details = body.get("details") or []
        issues = [d.get("issue", "") for d in details if isinstance(d, dict)]
        name = body.get("name") or body.get("error")
        message = body.get("message") or body.get("error_description") or f"HTTP {status}"
        raise PayPalAPIError(
            f"PayPal rejected the request: {name or status} - {message}",
            http_status=status,
            name=name,
            issues=issues,
            debug_id=body.get("debug_id"),
        )

    def _token_is_fresh(self) -> bool:
        return self._token is not None and (
            self._clock() < self._token_expires_at - TOKEN_REFRESH_MARGIN_SECONDS
        )

    def invalidate_token(self) -> None:
        self._token = None
        self._token_expires_at = 0.0

    async def get_access_token(self) -> str:
        self._require_configured()
        if self._token_is_fresh():
            return self._token  # type: ignore[return-value]
        if self._token_lock is None:
            self._token_lock = asyncio.Lock()
        async with self._token_lock:
            if self._token_is_fresh():  # another coroutine refreshed while we waited
                return self._token  # type: ignore[return-value]
            self.token_requests += 1
            response = await self._send(
                "POST",
                "/v1/oauth2/token",
                data={"grant_type": "client_credentials"},
                auth=(self._client_id, self._client_secret),
            )
            self._raise_for_status(response)
            body = response.json()
            token, expires_in = body.get("access_token"), body.get("expires_in")
            if not isinstance(token, str) or not isinstance(expires_in, int | float):
                raise PayPalUnavailableError("PayPal token response was malformed.")
            self._token = token
            self._token_expires_at = self._clock() + float(expires_in)
            return token

    async def _api(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_configured()
        for attempt in (1, 2):
            token = await self.get_access_token()
            headers = {"Authorization": f"Bearer {token}", "Prefer": "return=representation"}
            if request_id:
                headers["PayPal-Request-Id"] = request_id
            response = await self._send(method, path, json=json, headers=headers)
            if response.status_code == 401 and attempt == 1:
                self.invalidate_token()  # revoked/rotated token: refresh once and retry
                continue
            self._raise_for_status(response)
            try:
                body = response.json()
            except ValueError as exc:
                # 2xx with an unreadable body: the operation may have happened.
                raise PayPalUnavailableError("PayPal returned an unreadable response.") from exc
            if not isinstance(body, dict):
                raise PayPalUnavailableError("PayPal returned an unexpected response shape.")
            return body
        raise PayPalUnavailableError("PayPal authentication kept failing.")  # pragma: no cover

    # --- Orders v2 -------------------------------------------------------------------------

    async def create_paypal_order(
        self,
        *,
        reference_id: str,
        items: Sequence[PayPalItem],
        currency: str,
        request_id: str,
        description: str | None = None,
    ) -> PayPalOrderResult:
        payload = build_order_payload(
            reference_id=reference_id,
            items=items,
            currency=currency,
            return_url=self.settings.paypal_return_url,
            cancel_url=self.settings.paypal_cancel_url,
            brand_name=self.settings.paypal_brand_name,
            description=description,
        )
        body = await self._api("POST", "/v2/checkout/orders", json=payload, request_id=request_id)
        order_id = body.get("id")
        if not isinstance(order_id, str):
            raise PayPalUnavailableError("PayPal order response had no id.")
        approval_url = next(
            (
                link.get("href")
                for rel in ("payer-action", "approve")
                for link in body.get("links") or []
                if isinstance(link, dict) and link.get("rel") == rel
            ),
            None,
        )
        return PayPalOrderResult(order_id, str(body.get("status", "")), approval_url, body)

    async def capture_paypal_order(
        self, paypal_order_id: str, *, request_id: str
    ) -> PayPalCaptureResult:
        oid = _safe_id(paypal_order_id, "PayPal order id")
        body = await self._api(
            "POST", f"/v2/checkout/orders/{oid}/capture", json={}, request_id=request_id
        )
        return _parse_capture(body)

    async def get_paypal_order(self, paypal_order_id: str) -> PayPalCaptureResult:
        oid = _safe_id(paypal_order_id, "PayPal order id")
        return _parse_capture(await self._api("GET", f"/v2/checkout/orders/{oid}"))

    # --- Payouts v1 ------------------------------------------------------------------------

    async def create_payout_batch(
        self,
        *,
        sender_batch_id: str,
        items: Sequence[PayoutItem],
        currency: str,
        email_subject: str = "You have a payout from CatalogPay",
        email_message: str = "Your CatalogPay marketplace sales have been settled.",
    ) -> PayPalPayoutBatchResult:
        payload = build_payout_payload(
            sender_batch_id=sender_batch_id,
            items=items,
            currency=currency,
            email_subject=email_subject,
            email_message=email_message,
        )
        body = await self._api(
            "POST", "/v1/payments/payouts", json=payload, request_id=sender_batch_id
        )
        return _parse_payout_batch(body)

    async def get_payout_batch(self, payout_batch_id: str) -> PayPalPayoutBatchResult:
        bid = _safe_id(payout_batch_id, "payout batch id")
        return _parse_payout_batch(await self._api("GET", f"/v1/payments/payouts/{bid}"))

    # --- Webhooks --------------------------------------------------------------------------

    async def verify_webhook_signature(
        self, *, headers: Mapping[str, str], event: dict[str, Any]
    ) -> bool:
        """Ask PayPal (or the simulator) whether this delivery is authentic."""
        self._require_configured()
        webhook_id = self.webhook_id
        if not webhook_id:
            raise PayPalNotConfiguredError("PAYPAL_WEBHOOK_ID is required to verify webhooks.")
        lowered = {k.lower(): v for k, v in headers.items()}
        fields = {key: lowered.get(header, "") for key, header in WEBHOOK_HEADERS.items()}
        if not all(fields.values()):
            return False
        body = await self._api(
            "POST",
            "/v1/notifications/verify-webhook-signature",
            json={**fields, "webhook_id": webhook_id, "webhook_event": event},
        )
        return body.get("verification_status") == "SUCCESS"


# --------------------------------------------------------------------------------------------
# Agentic-commerce tool definitions (Gemini function calling / MCP)
# --------------------------------------------------------------------------------------------

_ORDER_ID_SCHEMA: Final = {
    "type": "string",
    "description": "CatalogPay public order id, e.g. ORD-1A2B3C4D5E6F.",
    "pattern": "^ORD-[A-Z0-9]{12}$",
}

_TOOLS: Final[tuple[dict[str, Any], ...]] = (
    {
        "name": "create_catalogpay_order",
        "description": (
            "Create a CatalogPay order from catalog-verified SKUs and open a PayPal Orders v2 "
            "checkout. Prices, fees and vendor splits are computed server-side from the "
            "catalog; never pass prices. Returns the order id and PayPal approval URL."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 100,
                    "items": {
                        "type": "object",
                        "properties": {
                            "sku_code": {"type": "string", "description": "Catalog SKU code."},
                            "quantity": {"type": "integer", "minimum": 1, "maximum": 10000},
                            "raw_query": {
                                "type": "string",
                                "description": "The buyer's original wording for audit.",
                            },
                        },
                        "required": ["sku_code", "quantity"],
                    },
                },
                "customer_note": {"type": "string", "maxLength": 2000},
                "idempotency_key": {
                    "type": "string",
                    "minLength": 8,
                    "maxLength": 128,
                    "description": "Unique per buyer intent; safe to retry with the same key.",
                },
            },
            "required": ["items", "idempotency_key"],
        },
    },
    {
        "name": "capture_and_split_payouts",
        "description": (
            "Capture the buyer-approved PayPal order, atomically deduct stock, and pay each "
            "vendor its net share via PayPal Payouts. Idempotent: repeated calls never charge "
            "the buyer or pay a vendor twice."
        ),
        "parameters": {
            "type": "object",
            "properties": {"public_order_id": _ORDER_ID_SCHEMA},
            "required": ["public_order_id"],
        },
    },
    {
        "name": "get_order_settlement_status",
        "description": (
            "Read an order's status, line items, and per-vendor payout ledger (amounts in "
            "integer cents, PayPal order/capture/payout ids)."
        ),
        "parameters": {
            "type": "object",
            "properties": {"public_order_id": _ORDER_ID_SCHEMA},
            "required": ["public_order_id"],
        },
    },
)


def get_gemini_paypal_tools() -> list[dict[str, Any]]:
    """Function declarations (JSON Schema parameters) for Gemini function calling."""
    return [dict(tool) for tool in _TOOLS]


def get_mcp_tool_definitions() -> list[dict[str, Any]]:
    """The same tools in MCP ``tools/list`` shape."""
    return [
        {"name": t["name"], "description": t["description"], "inputSchema": t["parameters"]}
        for t in _TOOLS
    ]
