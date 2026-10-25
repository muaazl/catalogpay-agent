"""Backend support for the Feature 4 Command Center: operator SKU pins + integration status."""

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_paypal_client
from app.core.config import Settings, get_settings
from app.services.paypal_client import PayPalClient

PREVIEW = "/api/v1/catalog/match-preview"


class TestOperatorSkuPin:
    def test_pin_overrides_match_and_keeps_alternatives(self, client: TestClient):
        # "colombian arabica" alone is ambiguous (500g vs 1kg); the operator picks the 1kg bag.
        unpinned = client.post(
            PREVIEW, json={"items": [{"query": "colombian arabica", "quantity": 2}]}
        ).json()["lines"][0]
        assert unpinned["is_billable"] is False and unpinned["is_pinned"] is False

        pinned = client.post(
            PREVIEW,
            json={
                "items": [
                    {"query": "colombian arabica", "quantity": 2, "sku_code": "SPR-COF-ARB-1KG"}
                ]
            },
        ).json()["lines"][0]
        assert pinned["is_pinned"] is True and pinned["is_billable"] is True
        assert pinned["match_status"] == "matched" and pinned["is_ambiguous"] is False
        assert pinned["matched_sku"]["sku_code"] == "SPR-COF-ARB-1KG"
        assert pinned["matched_sku"]["match_reasoning"].startswith("Pinned by operator")
        assert pinned["subtotal_cents"] == 2 * 2790
        codes = [c["sku_code"] for c in pinned["candidates"]]
        assert codes[0] == "SPR-COF-ARB-1KG" and "SPR-COF-ARB-500G" in codes
        assert len(codes) == len(set(codes))

    def test_pinned_lines_share_vendor_level_fee_allocation(self, client: TestClient):
        body = client.post(
            PREVIEW,
            json={
                "items": [
                    {"query": "lids", "quantity": 1, "sku_code": "GLP-LID-12OZ-1000"},
                    {"query": "lids again", "quantity": 1, "sku_code": "GLP-LID-12OZ-1000"},
                ]
            },
        ).json()
        assert body["vendor_payouts"][0]["platform_fee_cents"] == 897  # not 449 + 449

    def test_pin_to_inactive_or_unknown_sku_is_unmatched(self, client: TestClient, seeded_session):
        from sqlalchemy import select

        from app.models.catalog import ProductSKU

        sku = seeded_session.scalar(select(ProductSKU).where(ProductSKU.sku_code == "GLP-STR-500"))
        sku.is_active = False
        seeded_session.commit()
        for code in ("GLP-STR-500", "NOPE-1"):
            line = client.post(
                PREVIEW, json={"items": [{"query": "straws", "quantity": 1, "sku_code": code}]}
            ).json()["lines"][0]
            assert line["match_status"] == "unmatched" and line["is_billable"] is False
            assert f"Pinned SKU {code}" in line["issues"][0]

    def test_pin_still_checks_stock(self, client: TestClient):
        line = client.post(
            PREVIEW, json={"items": [{"query": "scale", "quantity": 1, "sku_code": "BPE-SCL-TMR"}]}
        ).json()["lines"][0]
        assert line["stock_status"] == "insufficient_stock" and line["is_billable"] is False

    @pytest.mark.parametrize("bad", ["bad code!", "x" * 65, 123])
    def test_pin_validation(self, client: TestClient, bad):
        resp = client.post(
            PREVIEW, json={"items": [{"query": "q", "quantity": 1, "sku_code": bad}]}
        )
        assert resp.status_code == 422


class TestHealthIntegrations:
    def test_defaults_report_fallback_and_simulation(self, client: TestClient):
        integrations = client.get("/api/v1/health").json()["integrations"]
        assert integrations == {
            "parser_mode": "deterministic_fallback",
            "parser_model": "regex",
            "paypal_mode": "sandbox_simulation",
            "paypal_env": "sandbox",
        }

    def test_configured_modes(self, client: TestClient):
        configured = Settings(
            gemini_api_key="k", paypal_client_id="id", paypal_client_secret="secret"
        )
        client.app.dependency_overrides[get_settings] = lambda: configured
        client.app.dependency_overrides[get_paypal_client] = lambda: PayPalClient(configured)
        integrations = client.get("/api/v1/health").json()["integrations"]
        assert integrations["parser_mode"] == "gemini"
        assert integrations["parser_model"] == "gemini-2.5-flash"
        assert integrations["paypal_mode"] == "live_sandbox"

    def test_paypal_not_configured(self, client: TestClient):
        off = PayPalClient(Settings(paypal_sandbox_mock_fallback=False))
        client.app.dependency_overrides[get_paypal_client] = lambda: off
        assert client.get("/api/v1/health").json()["integrations"]["paypal_mode"] is None
