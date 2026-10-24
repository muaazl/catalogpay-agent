"""Feature 3: order ledger, PayPal OAuth/Orders v2/Payouts v1 client, settlement, webhooks."""

import asyncio
import base64
import json
from collections import Counter
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from google.genai import types
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_paypal_client
from app.core.config import Settings
from app.models.catalog import ProductSKU, Vendor
from app.models.order import (
    Order,
    OrderLineItem,
    OrderStatus,
    PayoutStatus,
    PayPalMode,
    PayPalWebhookEvent,
    VendorPayout,
)
from app.services.order_settlement import reserve_stock
from app.services.paypal_client import (
    TOKEN_REFRESH_MARGIN_SECONDS,
    PayoutItem,
    PayPalAPIError,
    PayPalClient,
    PayPalItem,
    PayPalNotConfiguredError,
    PayPalUnavailableError,
    build_order_payload,
    format_cents_for_paypal,
    get_gemini_paypal_tools,
    get_mcp_tool_definitions,
    parse_paypal_amount,
)
from app.services.paypal_simulator import simulate_webhook_headers

ORDERS = "/api/v1/orders"
WEBHOOK = "/api/v1/webhooks/paypal"

BASKET = [
    {"sku_code": "SPR-COF-ARB-500G", "quantity": 4, "raw_query": "arabica beans half kg"},
    {"sku_code": "BPE-TMP-58MM", "quantity": 2, "raw_query": "espresso tamper 58mm"},
    {"sku_code": "GLP-LID-12OZ-1000", "quantity": 1},
    {"sku_code": "GLP-LID-12OZ-1000", "quantity": 1},
]
# Hand-computed: roasters 5980 @5% = 299, equipment 7998 @8% = 640, packaging 13800 @6.5% = 897
EXPECTED_PAYOUTS = {
    "vendor-roasters@sandbox.paypal.com": (5980, 299, 5681),
    "vendor-equipment@sandbox.paypal.com": (7998, 640, 7358),
    "vendor-packaging@sandbox.paypal.com": (13800, 897, 12903),
}


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# =============================================================================================
# Fixtures & helpers
# =============================================================================================


@pytest.fixture
def paypal(client: TestClient) -> PayPalClient:
    """A fresh sandbox_simulation client wired into the app for this test."""
    pp = PayPalClient(Settings(paypal_client_id="", paypal_client_secret=""))
    client.app.dependency_overrides[get_paypal_client] = lambda: pp
    return pp


def create(client: TestClient, items=None, key: str = "test-key-0001", **body: Any):
    return client.post(
        ORDERS, headers={"Idempotency-Key": key}, json={"items": items or BASKET, **body}
    )


def stock(session: Session, code: str) -> int:
    session.expire_all()
    return session.scalar(select(ProductSKU.stock_quantity).where(ProductSKU.sku_code == code))


def set_stock(session: Session, code: str, qty: int) -> None:
    session.expire_all()
    sku = session.scalar(select(ProductSKU).where(ProductSKU.sku_code == code))
    sku.stock_quantity = qty
    session.commit()


def post_webhook(client: TestClient, pp: PayPalClient, event: dict, *, headers=None):
    hdrs = headers if headers is not None else simulate_webhook_headers(pp.settings, event)
    return client.post(
        WEBHOOK, content=json.dumps(event), headers={**hdrs, "content-type": "application/json"}
    )


def batch_event(batch_id: str, kind: str = "SUCCESS", event_id: str | None = None) -> dict:
    return {
        "id": event_id or f"WH-{kind}-{batch_id}",
        "event_type": f"PAYMENT.PAYOUTS-BATCH.{kind}",
        "resource": {"batch_header": {"payout_batch_id": batch_id, "batch_status": kind}},
    }


def capture_event(paypal_order_id: str, capture_id: str, value: str, event_id="WH-CAP-1") -> dict:
    return {
        "id": event_id,
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {
            "id": capture_id,
            "status": "COMPLETED",
            "amount": {"currency_code": "USD", "value": value},
            "supplementary_data": {"related_ids": {"order_id": paypal_order_id}},
        },
    }


def assert_ledger_balances(order: dict) -> None:
    assert order["gross_subtotal_cents"] == (
        order["total_platform_fee_cents"] + order["total_vendor_payout_cents"]
    )
    assert sum(ln["subtotal_cents"] for ln in order["line_items"]) == order["gross_subtotal_cents"]
    for ln in order["line_items"]:
        assert ln["subtotal_cents"] == ln["unit_price_cents"] * ln["quantity"]
        assert ln["subtotal_cents"] == ln["platform_fee_cents"] + ln["net_vendor_payout_cents"]
    for p in order["payouts"]:
        assert p["gross_subtotal_cents"] == p["platform_fee_cents"] + p["payout_amount_cents"]
        vendor_lines = [ln for ln in order["line_items"] if ln["vendor_id"] == p["vendor_id"]]
        assert p["platform_fee_cents"] == sum(ln["platform_fee_cents"] for ln in vendor_lines)
    assert (
        sum(p["payout_amount_cents"] for p in order["payouts"])
        == (order["total_vendor_payout_cents"])
    )


class RecordingPayPal:
    """A tiny fake of the live PayPal API for request-shape and error-mapping tests."""

    def __init__(self, routes: dict[tuple[str, str], Any] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.routes = routes or {}
        self.token_calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, request.url.path)
        if key == ("POST", "/v1/oauth2/token"):
            self.token_calls += 1
            route = self.routes.get(key)
            if route is not None:
                return route(request) if callable(route) else route
            return httpx.Response(
                200, json={"access_token": f"TOKEN-{self.token_calls}", "expires_in": 3600}
            )
        route = self.routes.get(key)
        if route is None:
            return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND"})
        return route(request) if callable(route) else route

    def client(self, clock=None, **settings: Any) -> PayPalClient:
        cfg = Settings(
            paypal_client_id="client-id", paypal_client_secret="client-secret", **settings
        )
        kwargs = {"clock": clock} if clock else {}
        return PayPalClient(cfg, transport=httpx.MockTransport(self.handler), **kwargs)

    def last(self, path: str) -> httpx.Request:
        return next(r for r in reversed(self.requests) if r.url.path == path)


# =============================================================================================
# Money formatting
# =============================================================================================


class TestPayPalAmounts:
    @pytest.mark.parametrize(
        "cents,expected",
        [
            (0, "0.00"),
            (1, "0.01"),
            (99, "0.99"),
            (100, "1.00"),
            (1050, "10.50"),
            (1250, "12.50"),
            (9999999, "99999.99"),
            (10**12 + 7, "10000000000.07"),
        ],
    )
    def test_format_cents_for_paypal(self, cents, expected):
        assert format_cents_for_paypal(cents) == expected
        assert parse_paypal_amount(expected) == cents

    @pytest.mark.parametrize("bad", [True, 1.5, "100", None, -1])
    def test_format_rejects_non_int_and_negative(self, bad):
        with pytest.raises((TypeError, ValueError)):
            format_cents_for_paypal(bad)

    @pytest.mark.parametrize("bad", ["1.005", "abc", "-1.00", "NaN", "Infinity", "", 12, None])
    def test_parse_rejects_non_cent_amounts(self, bad):
        with pytest.raises(ValueError):
            parse_paypal_amount(bad)

    def test_order_payload_breakdown_always_sums(self):
        odd_prices = [1, 3, 7, 99, 101, 333, 1495, 3999, 6900, 64900]
        items = [
            PayPalItem(name=f"Item {p}", sku=f"SKU-{p}", quantity=(p % 7) + 1, unit_amount_cents=p)
            for p in odd_prices
        ]
        payload = build_order_payload(
            reference_id="ORD-X",
            items=items,
            currency="USD",
            return_url="https://r",
            cancel_url="https://c",
            brand_name="CatalogPay",
        )
        unit = payload["purchase_units"][0]
        total = parse_paypal_amount(unit["amount"]["value"])
        assert total == sum(p * ((p % 7) + 1) for p in odd_prices)
        assert parse_paypal_amount(unit["amount"]["breakdown"]["item_total"]["value"]) == total
        assert total == sum(
            parse_paypal_amount(i["unit_amount"]["value"]) * int(i["quantity"])
            for i in unit["items"]
        )
        assert payload["intent"] == "CAPTURE"

    def test_order_payload_requires_items(self):
        with pytest.raises(ValueError):
            build_order_payload(
                reference_id="x", items=[], currency="USD", return_url="", cancel_url="",
                brand_name="b",
            )  # fmt: skip


# =============================================================================================
# OAuth2 token caching
# =============================================================================================


class TestOAuthTokenCache:
    def test_token_cached_and_refreshed_60s_before_expiry(self):
        now = [1000.0]
        fake = RecordingPayPal()
        pp = fake.client(clock=lambda: now[0])

        async def scenario():
            t1 = await pp.get_access_token()
            t2 = await pp.get_access_token()
            now[0] += 3600 - TOKEN_REFRESH_MARGIN_SECONDS - 1  # 1s before the refresh window
            t3 = await pp.get_access_token()
            now[0] += 1  # inside the 60s margin -> refresh
            t4 = await pp.get_access_token()
            return t1, t2, t3, t4

        t1, t2, t3, t4 = run(scenario())
        assert t1 == t2 == t3 == "TOKEN-1"
        assert t4 == "TOKEN-2"
        assert fake.token_calls == 2

        token_req = fake.last("/v1/oauth2/token")
        expected = base64.b64encode(b"client-id:client-secret").decode()
        assert token_req.headers["authorization"] == f"Basic {expected}"
        assert token_req.content == b"grant_type=client_credentials"

    def test_concurrent_callers_share_one_token_request(self):
        fake = RecordingPayPal()
        pp = fake.client()

        async def scenario():
            return await asyncio.gather(*(pp.get_access_token() for _ in range(25)))

        assert set(run(scenario())) == {"TOKEN-1"}
        assert fake.token_calls == 1

    def test_401_invalidates_token_and_retries_once(self):
        state = {"n": 0}

        def orders(request: httpx.Request) -> httpx.Response:
            state["n"] += 1
            if state["n"] == 1:
                return httpx.Response(401, json={"name": "AUTHENTICATION_FAILURE"})
            assert request.headers["authorization"] == "Bearer TOKEN-2"
            return httpx.Response(200, json={"id": "PP-1", "status": "COMPLETED"})

        fake = RecordingPayPal({("GET", "/v2/checkout/orders/PP-1"): orders})
        result = run(fake.client().get_paypal_order("PP-1"))
        assert result.order_id == "PP-1" and fake.token_calls == 2

    def test_bad_credentials_and_malformed_token(self):
        fake = RecordingPayPal(
            {("POST", "/v1/oauth2/token"): httpx.Response(401, json={"error": "invalid_client"})}
        )
        with pytest.raises(PayPalAPIError):
            run(fake.client().get_access_token())
        fake = RecordingPayPal({("POST", "/v1/oauth2/token"): httpx.Response(200, json={})})
        with pytest.raises(PayPalUnavailableError):
            run(fake.client().get_access_token())


# =============================================================================================
# Live-API request shapes & error mapping
# =============================================================================================


class TestLiveRequestShapes:
    def test_create_capture_payout_and_verify_requests(self):
        fake = RecordingPayPal(
            {
                ("POST", "/v2/checkout/orders"): httpx.Response(
                    201,
                    json={
                        "id": "5O190127TN364715T",
                        "status": "CREATED",
                        "links": [{"rel": "approve", "href": "https://paypal.test/approve"}],
                    },
                ),
                ("POST", "/v2/checkout/orders/5O190127TN364715T/capture"): httpx.Response(
                    201,
                    json={
                        "id": "5O190127TN364715T",
                        "status": "COMPLETED",
                        "purchase_units": [
                            {
                                "payments": {
                                    "captures": [
                                        {
                                            "id": "3C679366HH908993F",
                                            "status": "COMPLETED",
                                            "amount": {"currency_code": "USD", "value": "29.90"},
                                        }
                                    ]
                                }
                            }
                        ],
                    },
                ),
                ("POST", "/v1/payments/payouts"): httpx.Response(
                    201,
                    json={
                        "batch_header": {
                            "payout_batch_id": "5UXD2E8A7EBQJ",
                            "batch_status": "PENDING",
                        }
                    },
                ),
                ("POST", "/v1/notifications/verify-webhook-signature"): httpx.Response(
                    200, json={"verification_status": "SUCCESS"}
                ),
            }
        )
        pp = fake.client(paypal_webhook_id="WH-LIVE-ID")
        assert pp.mode is PayPalMode.LIVE_SANDBOX

        async def scenario():
            order = await pp.create_paypal_order(
                reference_id="ORD-ABC",
                items=[PayPalItem("Colombian Arabica", "SPR-COF-ARB-500G", 2, 1495)],
                currency="USD",
                request_id="ORD-ABC-create",
            )
            cap = await pp.capture_paypal_order(order.order_id, request_id="ORD-ABC-capture")
            batch = await pp.create_payout_batch(
                sender_batch_id="ORD-ABC-V1",
                items=[PayoutItem("vendor@example.com", 2840, "ORD-ABC-V1-ITEM", "settlement")],
                currency="USD",
            )
            ok = await pp.verify_webhook_signature(
                headers={
                    "PAYPAL-AUTH-ALGO": "SHA256withRSA",
                    "PAYPAL-CERT-URL": "https://api.paypal.com/cert",
                    "PAYPAL-TRANSMISSION-ID": "tx-1",
                    "PAYPAL-TRANSMISSION-SIG": "sig",
                    "PAYPAL-TRANSMISSION-TIME": "2026-10-25T00:00:00Z",
                },
                event={"id": "WH-1", "event_type": "X"},
            )
            return order, cap, batch, ok

        order, cap, batch, ok = run(scenario())
        assert order.approval_url == "https://paypal.test/approve"
        assert (cap.capture_id, cap.capture_status, cap.amount_cents) == (
            "3C679366HH908993F",
            "COMPLETED",
            2990,
        )
        assert (batch.payout_batch_id, batch.batch_status) == ("5UXD2E8A7EBQJ", "PENDING")
        assert ok is True

        create_req = fake.last("/v2/checkout/orders")
        assert create_req.headers["paypal-request-id"] == "ORD-ABC-create"
        assert create_req.headers["authorization"] == "Bearer TOKEN-1"
        body = json.loads(create_req.content)
        unit = body["purchase_units"][0]
        assert unit["amount"] == {
            "currency_code": "USD",
            "value": "29.90",
            "breakdown": {"item_total": {"currency_code": "USD", "value": "29.90"}},
        }
        assert unit["items"][0]["unit_amount"]["value"] == "14.95"
        assert unit["items"][0]["quantity"] == "2"
        assert unit["custom_id"] == "ORD-ABC"

        capture_req = fake.last("/v2/checkout/orders/5O190127TN364715T/capture")
        assert capture_req.headers["paypal-request-id"] == "ORD-ABC-capture"

        payout_req = fake.last("/v1/payments/payouts")
        assert payout_req.headers["paypal-request-id"] == "ORD-ABC-V1"
        payout = json.loads(payout_req.content)
        assert payout["sender_batch_header"]["sender_batch_id"] == "ORD-ABC-V1"
        assert payout["sender_batch_header"]["recipient_type"] == "EMAIL"
        assert payout["items"] == [
            {
                "recipient_type": "EMAIL",
                "amount": {"value": "28.40", "currency": "USD"},
                "receiver": "vendor@example.com",
                "sender_item_id": "ORD-ABC-V1-ITEM",
                "note": "settlement",
                "recipient_wallet": "PAYPAL",
            }
        ]

        verify = json.loads(fake.last("/v1/notifications/verify-webhook-signature").content)
        assert verify["webhook_id"] == "WH-LIVE-ID"
        assert verify["transmission_id"] == "tx-1"
        assert verify["webhook_event"] == {"id": "WH-1", "event_type": "X"}
        assert fake.token_calls == 1  # one cached token for all four calls

    @pytest.mark.parametrize(
        "response,error",
        [
            (httpx.Response(500, json={}), PayPalUnavailableError),
            (httpx.Response(503, text="down"), PayPalUnavailableError),
            (httpx.Response(429, json={}), PayPalUnavailableError),
            (httpx.Response(200, text="<html>"), PayPalUnavailableError),
            (httpx.Response(422, json={"name": "UNPROCESSABLE_ENTITY",
                                       "details": [{"issue": "ORDER_NOT_APPROVED"}]}),
             PayPalAPIError),
            (httpx.Response(400, json={"name": "INVALID_REQUEST"}), PayPalAPIError),
        ],
    )  # fmt: skip
    def test_error_mapping(self, response, error):
        fake = RecordingPayPal({("POST", "/v2/checkout/orders/PP-1/capture"): response})
        with pytest.raises(error) as exc:
            run(fake.client().capture_paypal_order("PP-1", request_id="r"))
        assert exc.value.ambiguous is (error is PayPalUnavailableError)
        if isinstance(exc.value, PayPalAPIError) and response.status_code == 422:
            assert exc.value.has_issue("ORDER_NOT_APPROVED")

    def test_timeouts_are_ambiguous(self):
        def boom(request):
            raise httpx.ReadTimeout("slow", request=request)

        fake = RecordingPayPal({("POST", "/v2/checkout/orders/PP-1/capture"): boom})
        with pytest.raises(PayPalUnavailableError) as exc:
            run(fake.client().capture_paypal_order("PP-1", request_id="r"))
        assert exc.value.ambiguous

    @pytest.mark.parametrize("bad_id", ["../../v1/oauth2/token", "PP 1", "", "x" * 65])
    def test_path_ids_are_validated(self, bad_id):
        with pytest.raises(PayPalAPIError):
            run(RecordingPayPal().client().capture_paypal_order(bad_id, request_id="r"))

    def test_live_webhook_requires_webhook_id(self):
        with pytest.raises(PayPalNotConfiguredError):
            run(RecordingPayPal().client().verify_webhook_signature(headers={}, event={}))

    def test_settings_guard_env_vs_url(self):
        with pytest.raises(ValueError):
            Settings(paypal_env="live")  # default URL is the sandbox host
        with pytest.raises(ValueError):
            Settings(paypal_base_url="http://api-m.sandbox.paypal.com")
        assert Settings(paypal_env="live", paypal_base_url="https://api-m.paypal.com")

    def test_not_configured_without_fallback(self):
        pp = PayPalClient(Settings(paypal_sandbox_mock_fallback=False))
        assert pp.is_configured is False
        with pytest.raises(PayPalNotConfiguredError):
            run(pp.get_access_token())


# =============================================================================================
# End-to-end checkout -> capture -> split payouts (sandbox_simulation)
# =============================================================================================


class TestEndToEndSettlement:
    def test_full_lifecycle(self, client: TestClient, paypal: PayPalClient, seeded_session):
        sim = paypal.simulator
        before = {c: stock(seeded_session, c) for c in ("SPR-COF-ARB-500G", "GLP-LID-12OZ-1000")}

        resp = create(client, customer_note="Back door", parser_mode="gemini")
        assert resp.status_code == 201, resp.text
        order = resp.json()
        assert order["status"] == "PAYPAL_ORDER_CREATED"
        assert order["paypal_mode"] == "sandbox_simulation"
        assert order["paypal_order_id"].startswith("SIM-ORDER-")
        assert order["approval_url"].endswith(order["paypal_order_id"])
        assert (order["gross_subtotal_cents"], order["total_platform_fee_cents"]) == (27778, 1836)
        assert_ledger_balances(order)
        assert all(p["status"] == "PENDING" for p in order["payouts"])

        # The Orders v2 payload PayPal received: breakdown == total == ledger gross.
        sent = sim.orders[order["paypal_order_id"]].raw_request["purchase_units"][0]
        assert parse_paypal_amount(sent["amount"]["value"]) == order["gross_subtotal_cents"]
        assert (
            sum(
                parse_paypal_amount(i["unit_amount"]["value"]) * int(i["quantity"])
                for i in sent["items"]
            )
            == order["gross_subtotal_cents"]
        )
        assert [i["sku"] for i in sent["items"]] == [b["sku_code"] for b in BASKET]
        assert stock(seeded_session, "SPR-COF-ARB-500G") == before["SPR-COF-ARB-500G"]

        captured = client.post(f"{ORDERS}/{order['public_order_id']}/capture")
        assert captured.status_code == 200, captured.text
        done = captured.json()
        assert done["status"] == "COMPLETED"
        assert done["paypal_capture_id"].startswith("SIM-CAPTURE-")
        assert done["captured_at"] is not None and done["stock_reserved"] is True
        assert_ledger_balances(done)

        # Stock deducted by the *aggregated* quantity per SKU.
        assert stock(seeded_session, "SPR-COF-ARB-500G") == before["SPR-COF-ARB-500G"] - 4
        assert stock(seeded_session, "GLP-LID-12OZ-1000") == before["GLP-LID-12OZ-1000"] - 2

        # One Payouts v1 batch per vendor, paying exactly the net share.
        by_email = {p["receiver_paypal_email"]: p for p in done["payouts"]}
        for email, (gross, fee, net) in EXPECTED_PAYOUTS.items():
            p = by_email[email]
            assert (p["gross_subtotal_cents"], p["platform_fee_cents"]) == (gross, fee)
            assert p["payout_amount_cents"] == net
            assert p["status"] == "SUCCESS"
            assert p["paypal_payout_batch_id"].startswith("SIM-PAYOUT-")
        assert sorted(sim.disbursements) == sorted(
            (email, net, by_email[email]["sender_batch_id"])
            for email, (_, _, net) in EXPECTED_PAYOUTS.items()
        )
        assert (
            sum(c for _, c, _ in sim.disbursements) + done["total_platform_fee_cents"]
            == (done["gross_subtotal_cents"])
        )

        detail = client.get(f"{ORDERS}/{order['public_order_id']}").json()
        assert detail == done

    def test_duplicate_sku_lines_are_aggregated_against_stock(self, client, paypal, seeded_session):
        set_stock(seeded_session, "GCB-YST-500G", 6)
        items = [
            {"sku_code": "GCB-YST-500G", "quantity": 5},
            {"sku_code": "GCB-YST-500G", "quantity": 5},
        ]
        resp = create(client, items)
        assert resp.status_code == 409
        assert resp.json()["error"] == "insufficient_stock"
        assert "requested 10, available 6" in resp.json()["detail"]
        assert seeded_session.scalar(select(func.count()).select_from(Order)) == 0
        assert paypal.simulator.calls["create_order"] == 0

        ok = create(client, [{"sku_code": "GCB-YST-500G", "quantity": 3}] * 2, key="test-key-0002")
        assert ok.status_code == 201
        client.post(f"{ORDERS}/{ok.json()['public_order_id']}/capture")
        assert stock(seeded_session, "GCB-YST-500G") == 0

    def test_stock_rechecked_atomically_at_capture(self, client, paypal, seeded_session):
        set_stock(seeded_session, "GCB-YST-500G", 1)
        a = create(client, [{"sku_code": "GCB-YST-500G", "quantity": 1}], key="race-key-000A")
        b = create(client, [{"sku_code": "GCB-YST-500G", "quantity": 1}], key="race-key-000B")
        assert a.status_code == b.status_code == 201  # both fit when created

        assert client.post(f"{ORDERS}/{a.json()['public_order_id']}/capture").status_code == 200
        lost = client.post(f"{ORDERS}/{b.json()['public_order_id']}/capture")
        assert lost.status_code == 409 and lost.json()["error"] == "insufficient_stock"
        assert "No payment was captured" in lost.json()["detail"]
        assert stock(seeded_session, "GCB-YST-500G") == 0  # never negative
        assert paypal.simulator.calls["capture"] == 1  # the loser never reached PayPal
        b_after = client.get(f"{ORDERS}/{b.json()['public_order_id']}").json()
        assert b_after["status"] == "PAYPAL_ORDER_CREATED" and b_after["stock_reserved"] is False

    def test_reserve_stock_claims_once(self, client, paypal, seeded_session):
        order_id = create(client).json()["public_order_id"]
        seeded_session.expire_all()
        order = seeded_session.scalar(select(Order).where(Order.public_order_id == order_id))
        before = stock(seeded_session, "BPE-TMP-58MM")
        assert reserve_stock(seeded_session, order) is True
        assert reserve_stock(seeded_session, order) is False
        assert stock(seeded_session, "BPE-TMP-58MM") == before - 2

    def test_zero_net_payout_is_settled_without_paypal_call(self, client, paypal, seeded_session):
        vendor = seeded_session.scalar(
            select(Vendor).where(Vendor.paypal_merchant_email == "vendor-bakery@sandbox.paypal.com")
        )
        vendor.commission_rate_bps = 10_000
        seeded_session.commit()
        resp = create(client, [{"sku_code": "GCB-YST-500G", "quantity": 1}])
        (payout,) = resp.json()["payouts"]
        assert payout["payout_amount_cents"] == 0 and payout["platform_fee_cents"] == 899
        done = client.post(f"{ORDERS}/{resp.json()['public_order_id']}/capture").json()
        assert done["status"] == "COMPLETED" and done["payouts"][0]["status"] == "SUCCESS"
        assert paypal.simulator.calls["payout"] == 0

    @pytest.mark.parametrize(
        "items,status,error",
        [
            ([{"sku_code": "NOPE-123", "quantity": 1}], 422, "invalid_order"),
            ([{"sku_code": "GLP-STR-500", "quantity": 0}], 422, None),
            ([{"sku_code": "GLP-STR-500", "quantity": True}], 422, None),
            ([{"sku_code": "GLP-STR-500", "quantity": 1, "unit_price_cents": 1}], 422, None),
            ([{"sku_code": "bad code!", "quantity": 1}], 422, None),
            ([], 422, None),
        ],
    )
    def test_create_validation(self, client, paypal, items, status, error):
        resp = client.post(
            ORDERS, headers={"Idempotency-Key": "valid-key-1"}, json={"items": items}
        )
        assert resp.status_code == status
        if error:
            assert resp.json()["error"] == error

    def test_inactive_sku_rejected(self, client, paypal, seeded_session):
        sku = seeded_session.scalar(select(ProductSKU).where(ProductSKU.sku_code == "GLP-STR-500"))
        sku.is_active = False
        seeded_session.commit()
        resp = create(client, [{"sku_code": "GLP-STR-500", "quantity": 1}])
        assert resp.status_code == 422 and "not available" in resp.json()["detail"]

    def test_paypal_not_configured_is_503(self, client: TestClient):
        pp = PayPalClient(Settings(paypal_sandbox_mock_fallback=False))
        client.app.dependency_overrides[get_paypal_client] = lambda: pp
        resp = create(client)
        assert resp.status_code == 503 and resp.json()["error"] == "paypal_not_configured"


# =============================================================================================
# Idempotency
# =============================================================================================


class TestIdempotency:
    def test_create_replay_and_conflict(self, client, paypal, seeded_session):
        first = create(client, key="idem-key-0001")
        replay = create(client, key="idem-key-0001")
        assert first.status_code == 201 and replay.status_code == 200
        assert replay.json() == first.json()
        assert paypal.simulator.calls["create_order"] == 1

        changed = create(client, [{"sku_code": "GLP-STR-500", "quantity": 1}], key="idem-key-0001")
        assert changed.status_code == 409 and changed.json()["error"] == "idempotency_conflict"
        note_changed = create(client, key="idem-key-0001", customer_note="different")
        assert note_changed.status_code == 409
        assert seeded_session.scalar(select(func.count()).select_from(Order)) == 1

    def test_body_key_and_header_rules(self, client, paypal):
        body_key = client.post(ORDERS, json={"items": BASKET, "idempotency_key": "body-key-0001"})
        assert body_key.status_code == 201
        mismatch = client.post(
            ORDERS,
            headers={"Idempotency-Key": "header-key-01"},
            json={"items": BASKET, "idempotency_key": "body-key-0002"},
        )
        assert mismatch.status_code == 422
        assert client.post(ORDERS, json={"items": BASKET}).status_code == 422
        bad = client.post(ORDERS, headers={"Idempotency-Key": "short"}, json={"items": BASKET})
        assert bad.status_code == 422

    def test_paypal_failure_on_create_leaves_draft_then_replay_recovers(self, client, paypal):
        paypal.simulator.inject("create_order", status=503)
        failed = create(client, key="draft-key-0001")
        assert failed.status_code == 503
        recovered = create(client, key="draft-key-0001")
        assert recovered.status_code == 200
        assert recovered.json()["status"] == "PAYPAL_ORDER_CREATED"

    def test_capture_twice_charges_and_pays_once(self, client, paypal, seeded_session):
        order_id = create(client).json()["public_order_id"]
        before = stock(seeded_session, "SPR-COF-ARB-500G")
        first = client.post(f"{ORDERS}/{order_id}/capture").json()
        second = client.post(f"{ORDERS}/{order_id}/capture").json()
        third = client.post(f"{ORDERS}/{order_id}/retry-payouts").json()
        assert first == second == third
        assert stock(seeded_session, "SPR-COF-ARB-500G") == before - 4
        sim = paypal.simulator
        assert sim.calls["capture"] == 1 and sim.calls["payout"] == 3
        assert len(sim.disbursements) == 3


# =============================================================================================
# Capture failure modes
# =============================================================================================


class TestCaptureFailures:
    def test_not_approved_releases_stock(self, client, paypal, seeded_session):
        order = create(client).json()
        before = stock(seeded_session, "BPE-TMP-58MM")
        paypal.simulator.mark_order_unapproved(order["paypal_order_id"])
        resp = client.post(f"{ORDERS}/{order['public_order_id']}/capture")
        assert resp.status_code == 409 and resp.json()["error"] == "order_not_approved"
        after = client.get(f"{ORDERS}/{order['public_order_id']}").json()
        assert after["status"] == "PAYPAL_ORDER_CREATED" and after["stock_reserved"] is False
        assert stock(seeded_session, "BPE-TMP-58MM") == before

        paypal.simulator.orders[order["paypal_order_id"]].status = "APPROVED"  # buyer approves
        ok = client.post(f"{ORDERS}/{order['public_order_id']}/capture")
        assert ok.json()["status"] == "COMPLETED"
        assert stock(seeded_session, "BPE-TMP-58MM") == before - 2

    def test_declined_is_402_and_releases_stock(self, client, paypal, seeded_session):
        order_id = create(client).json()["public_order_id"]
        before = stock(seeded_session, "BPE-TMP-58MM")
        paypal.simulator.inject(
            "capture",
            status=422,
            body={"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "INSTRUMENT_DECLINED"}]},
        )
        resp = client.post(f"{ORDERS}/{order_id}/capture")
        assert resp.status_code == 402 and resp.json()["error"] == "payment_declined"
        assert stock(seeded_session, "BPE-TMP-58MM") == before

    @pytest.mark.parametrize("lost_response", [False, True])
    def test_ambiguous_capture_keeps_reservation_and_retry_is_exactly_once(
        self, client, paypal, seeded_session, lost_response
    ):
        order_id = create(client).json()["public_order_id"]
        before = stock(seeded_session, "BPE-TMP-58MM")
        if lost_response:  # PayPal captured, but the response never reached us
            paypal.simulator.inject("capture", lost_response=True)
        else:
            paypal.simulator.inject("capture", exc=httpx.ReadTimeout)
        first = client.post(f"{ORDERS}/{order_id}/capture")
        assert first.status_code == 503
        mid = client.get(f"{ORDERS}/{order_id}").json()
        assert mid["status"] == "PAYPAL_ORDER_CREATED" and mid["stock_reserved"] is True
        assert stock(seeded_session, "BPE-TMP-58MM") == before - 2

        retry = client.post(f"{ORDERS}/{order_id}/capture")
        assert retry.status_code == 200 and retry.json()["status"] == "COMPLETED"
        assert stock(seeded_session, "BPE-TMP-58MM") == before - 2  # still exactly once
        capture_ids = {
            r.headers["paypal-request-id"]
            for r in paypal.simulator.requests
            if r.url.path.endswith("/capture")
        }
        assert capture_ids == {f"{order_id}-capture-1"}  # same id on retry
        assert len(paypal.simulator.disbursements) == 3

    def test_already_captured_is_recovered_from_paypal(self, client, paypal, seeded_session):
        order = create(client).json()
        sim_order = paypal.simulator.orders[order["paypal_order_id"]]
        sim_order.status, sim_order.capture_id = "COMPLETED", "SIM-CAPTURE-EXTERNAL"
        done = client.post(f"{ORDERS}/{order['public_order_id']}/capture").json()
        assert done["status"] == "COMPLETED"
        assert done["paypal_capture_id"] == "SIM-CAPTURE-EXTERNAL"

    def test_capture_amount_mismatch_holds_payouts(self, client, paypal, seeded_session):
        order = create(client).json()
        paypal.simulator.orders[order["paypal_order_id"]].amount_cents -= 1
        held = client.post(f"{ORDERS}/{order['public_order_id']}/capture").json()
        assert held["status"] == "CAPTURED"
        assert "ledger total is 27778" in held["payout_hold_reason"]
        assert paypal.simulator.disbursements == []
        retry = client.post(f"{ORDERS}/{order['public_order_id']}/retry-payouts").json()
        assert retry["status"] == "CAPTURED" and paypal.simulator.disbursements == []

    def test_capture_requires_paypal_order(self, client, paypal):
        paypal.simulator.inject("create_order", status=503)
        create(client, key="draft-key-0002")
        draft_id = client.get(ORDERS).json()["items"][0]["public_order_id"]
        resp = client.post(f"{ORDERS}/{draft_id}/capture")
        assert resp.status_code == 409 and resp.json()["error"] == "invalid_order_state"

    def test_mode_mismatch_is_rejected(self, client, paypal):
        order_id = create(client).json()["public_order_id"]
        live = RecordingPayPal().client()
        client.app.dependency_overrides[get_paypal_client] = lambda: live
        resp = client.post(f"{ORDERS}/{order_id}/capture")
        assert resp.status_code == 409 and "sandbox_simulation" in resp.json()["detail"]


# =============================================================================================
# Payout failure recovery
# =============================================================================================


class TestPayoutRecovery:
    def test_payout_5xx_then_retry_completes_without_recapture(
        self, client, paypal, seeded_session
    ):
        sim = paypal.simulator
        order_id = create(client).json()["public_order_id"]
        before = stock(seeded_session, "SPR-COF-ARB-500G")
        sim.inject("payout", status=502, times=3)  # every vendor payout fails

        partial = client.post(f"{ORDERS}/{order_id}/capture")
        assert partial.status_code == 200
        body = partial.json()
        assert body["status"] == "PAYOUTS_PARTIAL_FAILED"
        assert body["paypal_capture_id"] and body["captured_at"]
        assert all(p["status"] == "FAILED" for p in body["payouts"])
        assert all(p["error_message"].startswith("Ambiguous") for p in body["payouts"])
        batch_ids = [p["sender_batch_id"] for p in body["payouts"]]
        assert stock(seeded_session, "SPR-COF-ARB-500G") == before - 4

        retried = client.post(f"{ORDERS}/{order_id}/retry-payouts").json()
        assert retried["status"] == "COMPLETED"
        assert [p["sender_batch_id"] for p in retried["payouts"]] == batch_ids  # ids kept
        assert all(p["attempt_count"] == 2 for p in retried["payouts"])
        assert stock(seeded_session, "SPR-COF-ARB-500G") == before - 4  # deducted once
        assert sim.calls["capture"] == 1
        assert len(sim.disbursements) == 3

    def test_partial_failure_only_retries_the_failed_vendor(self, client, paypal):
        sim = paypal.simulator
        order_id = create(client).json()["public_order_id"]
        sim.inject("payout", exc=httpx.ConnectTimeout)  # first vendor only
        partial = client.post(f"{ORDERS}/{order_id}/capture").json()
        statuses = Counter(p["status"] for p in partial["payouts"])
        assert statuses == {"FAILED": 1, "SUCCESS": 2}
        assert partial["status"] == "PAYOUTS_PARTIAL_FAILED"
        assert sim.calls["payout"] == 3

        done = client.post(f"{ORDERS}/{order_id}/retry-payouts").json()
        assert done["status"] == "COMPLETED"
        assert sim.calls["payout"] == 4  # only the failed one was resent

    def test_lost_payout_response_never_pays_twice(self, client, paypal):
        sim = paypal.simulator
        order_id = create(client).json()["public_order_id"]
        sim.inject("payout", lost_response=True)  # PayPal paid vendor 1; we saw a timeout
        partial = client.post(f"{ORDERS}/{order_id}/capture").json()
        assert partial["status"] == "PAYOUTS_PARTIAL_FAILED"
        assert len(sim.disbursements) == 3  # money already moved for all three

        done = client.post(f"{ORDERS}/{order_id}/retry-payouts").json()
        assert done["status"] == "COMPLETED"
        assert len(sim.disbursements) == 3  # same sender_batch_id replayed: no second payment
        receivers = Counter(r for r, _, _ in sim.disbursements)
        assert set(receivers.values()) == {1}

    def test_definitive_rejection_rotates_batch_id(self, client, paypal):
        sim = paypal.simulator
        order_id = create(client).json()["public_order_id"]
        sim.inject(
            "payout",
            status=422,
            body={"name": "VALIDATION_ERROR", "details": [{"issue": "RECEIVER_UNREGISTERED"}]},
        )
        partial = client.post(f"{ORDERS}/{order_id}/capture").json()
        failed = next(p for p in partial["payouts"] if p["status"] == "FAILED")
        assert failed["sender_batch_id"].endswith("-R1")
        assert "RECEIVER_UNREGISTERED" not in failed["sender_batch_id"]

        done = client.post(f"{ORDERS}/{order_id}/retry-payouts").json()
        assert done["status"] == "COMPLETED"
        paid_ids = [b for _, _, b in sim.disbursements]
        assert failed["sender_batch_id"] in paid_ids
        assert len(sim.disbursements) == 3

    def test_retry_payouts_requires_capture(self, client, paypal):
        order_id = create(client).json()["public_order_id"]
        resp = client.post(f"{ORDERS}/{order_id}/retry-payouts")
        assert resp.status_code == 409


# =============================================================================================
# Webhooks
# =============================================================================================


class TestWebhooks:
    def _pending_order(self, client, paypal) -> dict:
        paypal.simulator.instant_payouts = False  # behave like live: batches start PENDING
        order_id = create(client).json()["public_order_id"]
        order = client.post(f"{ORDERS}/{order_id}/capture").json()
        assert order["status"] == "PAYOUTS_PENDING"
        assert all(p["status"] == "PROCESSING" for p in order["payouts"])
        return order

    def test_payout_batch_success_webhooks_complete_order(self, client, paypal, seeded_session):
        order = self._pending_order(client, paypal)
        batches = [p["paypal_payout_batch_id"] for p in order["payouts"]]
        for i, batch_id in enumerate(batches):
            ack = post_webhook(client, paypal, batch_event(batch_id))
            assert ack.status_code == 200, ack.text
            assert ack.json()["status"] == "processed"
            state = client.get(f"{ORDERS}/{order['public_order_id']}").json()
            expected = "COMPLETED" if i == len(batches) - 1 else "PAYOUTS_PENDING"
            assert state["status"] == expected

        dup = post_webhook(client, paypal, batch_event(batches[0]))
        assert dup.json()["status"] == "duplicate"
        assert seeded_session.scalar(select(func.count()).select_from(PayPalWebhookEvent)) == 3

    def test_payout_batch_denied_then_retry(self, client, paypal):
        order = self._pending_order(client, paypal)
        denied = order["payouts"][0]
        ack = post_webhook(client, paypal, batch_event(denied["paypal_payout_batch_id"], "DENIED"))
        assert ack.json()["status"] == "processed"
        state = client.get(f"{ORDERS}/{order['public_order_id']}").json()
        assert state["status"] == "PAYOUTS_PARTIAL_FAILED"
        row = next(p for p in state["payouts"] if p["id"] == denied["id"])
        assert row["status"] == "FAILED" and row["sender_batch_id"] != denied["sender_batch_id"]

        paypal.simulator.instant_payouts = True
        for p in state["payouts"]:
            if p["paypal_payout_batch_id"]:
                paypal.simulator.settle_batch(p["paypal_payout_batch_id"])
        done = client.post(f"{ORDERS}/{order['public_order_id']}/retry-payouts").json()
        assert done["status"] == "COMPLETED"

    def test_retry_payouts_polls_processing_batches(self, client, paypal):
        order = self._pending_order(client, paypal)
        for p in order["payouts"]:
            paypal.simulator.settle_batch(p["paypal_payout_batch_id"])
        done = client.post(f"{ORDERS}/{order['public_order_id']}/retry-payouts").json()
        assert done["status"] == "COMPLETED"
        assert paypal.simulator.calls["get_payout"] == 3
        assert paypal.simulator.calls["payout"] == 3  # nothing re-submitted

    def test_capture_completed_webhook_adopts_external_capture(
        self, client, paypal, seeded_session
    ):
        order = create(client).json()
        before = stock(seeded_session, "BPE-TMP-58MM")
        event = capture_event(order["paypal_order_id"], "CAP-EXTERNAL-1", "277.78")
        ack = post_webhook(client, paypal, event)
        assert ack.status_code == 200 and "captured" in ack.json()["detail"]
        state = client.get(f"{ORDERS}/{order['public_order_id']}").json()
        assert state["status"] == "COMPLETED"
        assert state["paypal_capture_id"] == "CAP-EXTERNAL-1"
        assert stock(seeded_session, "BPE-TMP-58MM") == before - 2

        # A later API capture call is a no-op: no second deduction, no second payout.
        again = client.post(f"{ORDERS}/{order['public_order_id']}/capture").json()
        assert again["status"] == "COMPLETED"
        assert stock(seeded_session, "BPE-TMP-58MM") == before - 2
        assert len(paypal.simulator.disbursements) == 3

    def test_capture_completed_after_api_capture_is_noop(self, client, paypal):
        order = create(client).json()
        done = client.post(f"{ORDERS}/{order['public_order_id']}/capture").json()
        event = capture_event(done["paypal_order_id"], done["paypal_capture_id"], "277.78")
        ack = post_webhook(client, paypal, event).json()
        assert ack["status"] == "processed" and ack["detail"].startswith("no-op")

    def test_payout_item_webhook(self, client, paypal):
        order = self._pending_order(client, paypal)
        p = order["payouts"][0]
        event = {
            "id": "WH-ITEM-1",
            "event_type": "PAYMENT.PAYOUTSITEM.SUCCEEDED",
            "resource": {"payout_item": {"sender_item_id": p["sender_item_id"]}},
        }
        assert post_webhook(client, paypal, event).json()["status"] == "processed"
        state = client.get(f"{ORDERS}/{order['public_order_id']}").json()
        assert next(x for x in state["payouts"] if x["id"] == p["id"])["status"] == "SUCCESS"

    @pytest.mark.parametrize(
        "event",
        [
            {"id": "WH-X1", "event_type": "CHECKOUT.ORDER.APPROVED", "resource": {}},
            batch_event("UNKNOWN-BATCH", event_id="WH-X2"),
            capture_event("UNKNOWN-ORDER", "CAP-1", "1.00", event_id="WH-X3"),
        ],
    )
    def test_unknown_or_unhandled_events_are_acknowledged(self, client, paypal, event):
        ack = post_webhook(client, paypal, event)
        assert ack.status_code == 200 and ack.json()["status"] == "ignored"

    def test_signature_is_required_and_verified(self, client, paypal):
        order = self._pending_order(client, paypal)
        event = batch_event(order["payouts"][0]["paypal_payout_batch_id"])
        headers = simulate_webhook_headers(paypal.settings, event)

        tampered = {**event, "event_type": "PAYMENT.PAYOUTS-BATCH.DENIED"}
        assert post_webhook(client, paypal, tampered, headers=headers).status_code == 400
        assert post_webhook(client, paypal, event, headers={}).status_code == 400
        forged = {**headers, "paypal-transmission-sig": "0" * 64}
        assert post_webhook(client, paypal, event, headers=forged).status_code == 400
        wrong_secret = Settings(paypal_simulation_secret="attacker-guess")
        forged2 = simulate_webhook_headers(wrong_secret, event)
        assert post_webhook(client, paypal, event, headers=forged2).status_code == 400

        state = client.get(f"{ORDERS}/{order['public_order_id']}").json()
        assert state["status"] == "PAYOUTS_PENDING"  # nothing applied
        assert post_webhook(client, paypal, event, headers=headers).status_code == 200

    @pytest.mark.parametrize("raw", [b"not json", b"[1,2]", b'"str"'])
    def test_malformed_bodies(self, client, paypal, raw):
        resp = client.post(WEBHOOK, content=raw, headers={"content-type": "application/json"})
        assert resp.status_code == 400

    def test_event_without_id_is_rejected(self, client, paypal):
        event = {"event_type": "PAYMENT.PAYOUTS-BATCH.SUCCESS", "resource": {}}
        assert post_webhook(client, paypal, event).status_code == 422

    def test_oversized_webhook_body(self, client, paypal):
        resp = client.post(WEBHOOK, content=b"{" + b" " * 300_000 + b"}")
        assert resp.status_code == 413


# =============================================================================================
# Listing, schema, DB constraints, agent tools
# =============================================================================================


class TestLedgerApi:
    def test_list_filter_and_pagination(self, client, paypal):
        ids = [create(client, key=f"list-key-000{i}").json()["public_order_id"] for i in range(3)]
        client.post(f"{ORDERS}/{ids[1]}/capture")
        listing = client.get(ORDERS).json()
        assert listing["total"] == 3
        assert {o["public_order_id"] for o in listing["items"]} == set(ids)
        completed = client.get(ORDERS, params={"status": "COMPLETED"}).json()
        assert [o["public_order_id"] for o in completed["items"]] == [ids[1]]
        page = client.get(ORDERS, params={"limit": 2, "offset": 2}).json()
        assert page["total"] == 3 and len(page["items"]) == 1
        assert client.get(ORDERS, params={"status": "BOGUS"}).status_code == 422

    def test_get_unknown_and_malformed_ids(self, client, paypal):
        assert client.get(f"{ORDERS}/ORD-000000000000").status_code == 404
        assert client.get(f"{ORDERS}/not-an-id").status_code == 422
        assert client.post(f"{ORDERS}/ORD-000000000000/capture").status_code == 404

    def test_agent_tools(self, client):
        tools = get_gemini_paypal_tools()
        assert [t["name"] for t in tools] == [
            "create_catalogpay_order",
            "capture_and_split_payouts",
            "get_order_settlement_status",
        ]
        declarations = [
            types.FunctionDeclaration(
                name=t["name"], description=t["description"], parameters_json_schema=t["parameters"]
            )
            for t in tools
        ]
        assert types.Tool(function_declarations=declarations)
        create_schema = tools[0]["parameters"]
        assert "price" not in json.dumps(create_schema["properties"]).lower().replace(
            "never pass prices", ""
        )
        assert {t["name"] for t in get_mcp_tool_definitions()} == {t["name"] for t in tools}
        assert all("inputSchema" in t for t in get_mcp_tool_definitions())
        body = client.get("/api/v1/agent/tools").json()
        assert len(body["gemini_function_declarations"]) == len(body["mcp_tools"]) == 3


class TestLedgerConstraints:
    def _order(self, **overrides) -> Order:
        fields = {
            "public_order_id": "ORD-AAAAAAAAAAAA",
            "idempotency_key": "constraint-key",
            "request_hash": "h",
            "status": OrderStatus.DRAFT,
            "currency": "USD",
            "gross_subtotal_cents": 100,
            "total_platform_fee_cents": 5,
            "total_vendor_payout_cents": 95,
            "paypal_mode": PayPalMode.SANDBOX_SIMULATION,
        }
        return Order(**{**fields, **overrides})

    @pytest.mark.parametrize(
        "overrides",
        [
            {"total_vendor_payout_cents": 94},  # parts do not sum
            {"gross_subtotal_cents": -1, "total_platform_fee_cents": -96},
            {"status": "PAID_MAYBE"},
            {"paypal_mode": "production"},
        ],
    )
    def test_order_constraints(self, seeded_session: Session, overrides):
        seeded_session.add(self._order(**overrides))
        with pytest.raises(IntegrityError):
            seeded_session.commit()

    @pytest.mark.parametrize(
        "line_overrides",
        [
            {"subtotal_cents": 101},  # != unit * qty
            {"platform_fee_cents": 6},  # != subtotal - net
            {"quantity": 0, "subtotal_cents": 0, "platform_fee_cents": 0,
             "net_vendor_payout_cents": 0},
        ],
    )  # fmt: skip
    def test_line_constraints(self, seeded_session: Session, line_overrides):
        sku = seeded_session.scalars(select(ProductSKU)).first()
        order = self._order()
        line = {
            "sku_id": sku.id,
            "vendor_id": sku.vendor_id,
            "sku_code": sku.sku_code,
            "product_name": sku.name,
            "quantity": 1,
            "unit_price_cents": 100,
            "subtotal_cents": 100,
            "commission_rate_bps": 500,
            "platform_fee_cents": 5,
            "net_vendor_payout_cents": 95,
        }
        order.line_items.append(OrderLineItem(**{**line, **line_overrides}))
        seeded_session.add(order)
        with pytest.raises(IntegrityError):
            seeded_session.commit()

    def test_payout_constraints(self, seeded_session: Session):
        vendor = seeded_session.scalars(select(Vendor)).first()
        order = self._order()
        order.payouts.append(
            VendorPayout(
                vendor_id=vendor.id,
                vendor_name=vendor.name,
                receiver_paypal_email=vendor.paypal_merchant_email,
                currency="USD",
                gross_subtotal_cents=100,
                platform_fee_cents=5,
                payout_amount_cents=96,
                sender_batch_id="B-1",
                sender_item_id="I-1",
                status=PayoutStatus.PENDING,
            )
        )
        seeded_session.add(order)
        with pytest.raises(IntegrityError):
            seeded_session.commit()


class TestBugsFoundDuringFeature3:
    def test_timestamps_are_utc_aware_after_db_round_trip(self, client, paypal, seeded_session):
        created = create(client).json()
        reloaded = client.get(f"{ORDERS}/{created['public_order_id']}").json()
        for body in (created, reloaded):
            for key in ("created_at", "updated_at"):
                assert body[key].endswith("Z") or body[key].endswith("+00:00"), body[key]
        assert created["created_at"] == reloaded["created_at"]
        seeded_session.expire_all()
        row = seeded_session.scalar(select(Order))
        assert row.created_at.tzinfo is not None
        assert seeded_session.scalar(select(Vendor)).created_at.tzinfo is not None

    def test_naive_datetimes_are_refused(self, seeded_session: Session):
        from datetime import datetime

        vendor = seeded_session.scalars(select(Vendor)).first()
        vendor.created_at = datetime(2026, 1, 1)
        with pytest.raises(Exception, match="Naive datetimes"):
            seeded_session.commit()
        seeded_session.rollback()

    def test_definitive_capture_failure_advances_request_id(self, client, paypal):
        order = create(client).json()
        paypal.simulator.mark_order_unapproved(order["paypal_order_id"])
        client.post(f"{ORDERS}/{order['public_order_id']}/capture")
        paypal.simulator.orders[order["paypal_order_id"]].status = "APPROVED"
        assert client.post(f"{ORDERS}/{order['public_order_id']}/capture").status_code == 200
        ids = [
            r.headers["paypal-request-id"]
            for r in paypal.simulator.requests
            if r.url.path.endswith("/capture")
        ]
        oid = order["public_order_id"]
        assert ids == [f"{oid}-capture-1", f"{oid}-capture-2"]

    def test_definitive_create_failure_advances_request_id(self, client, paypal):
        paypal.simulator.inject(
            "create_order",
            status=422,
            body={"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "PAYEE_NOT_ENABLED"}]},
        )
        assert create(client, key="create-seq-01").status_code == 502
        assert create(client, key="create-seq-01").json()["status"] == "PAYPAL_ORDER_CREATED"
        ids = [
            r.headers["paypal-request-id"]
            for r in paypal.simulator.requests
            if r.url.path == "/v2/checkout/orders"
        ]
        assert ids[0].endswith("-create-1") and ids[1].endswith("-create-2")

    def test_ambiguous_create_failure_reuses_request_id(self, client, paypal):
        paypal.simulator.inject("create_order", lost_response=True)
        assert create(client, key="create-seq-02").status_code == 503
        replay = create(client, key="create-seq-02").json()
        ids = {
            r.headers["paypal-request-id"]
            for r in paypal.simulator.requests
            if r.url.path == "/v2/checkout/orders"
        }
        assert len(ids) == 1 and replay["status"] == "PAYPAL_ORDER_CREATED"
        assert len(paypal.simulator.orders) == 1  # PayPal holds one order, not two
