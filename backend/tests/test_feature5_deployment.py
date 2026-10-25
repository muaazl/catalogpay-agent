"""Feature 5: unified production serving, demo history seeding, sample assets."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.core.config import REPO_ROOT, Settings
from app.db.session import build_session_factory, get_db
from app.main import create_app
from app.models.catalog import ProductSKU
from app.models.order import Order, PayoutStatus, VendorPayout
from app.services.demo_orders import DEMO_ORDER_ID, seed_demo_orders
from app.services.gemini_parser import validate_media

INDEX_HTML = (
    "<!doctype html><html><head><title>CatalogPay Command Center</title></head>"
    "<body><div id=root></div></body></html>"
)


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "samples").mkdir()
    (root / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (root / "assets" / "index-abc123.js").write_text("console.log('catalogpay')", encoding="utf-8")
    (root / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    (root / "samples" / "sample_order_note.txt").write_text("2x tamper", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("do not serve", encoding="utf-8")
    return root


def _client(engine: Engine, settings: Settings) -> Iterator[TestClient]:
    factory = build_session_factory(engine)

    def _db() -> Iterator[Session]:
        with factory() as s:
            yield s

    app = create_app(settings)
    app.dependency_overrides[get_db] = _db
    with TestClient(app) as tc:
        yield tc


@pytest.fixture
def spa_client(engine: Engine, seeded_session: Session, dist: Path) -> Iterator[TestClient]:
    yield from _client(engine, Settings(static_dist_dir=str(dist)))


@pytest.fixture
def api_only_client(
    engine: Engine, seeded_session: Session, tmp_path: Path
) -> Iterator[TestClient]:
    yield from _client(engine, Settings(static_dist_dir=str(tmp_path / "missing")))


class TestUnifiedServing:
    def test_health_and_api_still_json(self, spa_client: TestClient):
        resp = spa_client.get("/api/v1/health")
        assert resp.status_code == 200 and resp.json()["status"] == "ok"
        assert resp.headers["content-type"].startswith("application/json")
        assert spa_client.get("/api/v1/catalog/skus").json()["total"] == 25

    def test_openapi_and_docs_are_not_shadowed(self, spa_client: TestClient):
        spec = spa_client.get("/openapi.json")
        assert spec.status_code == 200 and "/api/v1/orders" in spec.json()["paths"]
        assert not any(p.startswith("/{") for p in spec.json()["paths"])  # SPA routes hidden
        docs = spa_client.get("/docs")
        assert docs.status_code == 200 and "swagger-ui" in docs.text.lower()
        assert spa_client.get("/redoc").status_code == 200

    @pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE", "PATCH"])
    @pytest.mark.parametrize("path", ["/api/v1/nonexistent", "/api/v2/orders", "/api/", "/api"])
    def test_unknown_api_routes_are_json_404_never_html(self, spa_client: TestClient, method, path):
        resp = spa_client.request(method, path)
        assert resp.status_code == 404
        assert resp.headers["content-type"].startswith("application/json")
        assert resp.json() == {"detail": "Not Found"}
        assert "<html" not in resp.text.lower()

    @pytest.mark.parametrize(
        "path", ["/", "/checkout/return", "/orders/ORD-DEMO00000001", "/deep/link?x=1"]
    )
    def test_spa_routes_serve_index(self, spa_client: TestClient, path):
        resp = spa_client.get(path)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        assert "CatalogPay Command Center" in resp.text
        assert resp.headers["cache-control"] == "no-cache"

    def test_head_request_on_spa(self, spa_client: TestClient):
        assert spa_client.head("/").status_code == 200

    def test_static_assets_and_root_files(self, spa_client: TestClient):
        js = spa_client.get("/assets/index-abc123.js")
        assert js.status_code == 200 and "catalogpay" in js.text
        assert "immutable" in js.headers["cache-control"]
        assert spa_client.get("/assets/missing.js").status_code == 404
        assert spa_client.get("/favicon.svg").headers["content-type"].startswith("image/svg")
        assert spa_client.get("/samples/sample_order_note.txt").text == "2x tamper"

    @pytest.mark.parametrize(
        "path",
        ["/../secret.txt", "/%2e%2e/secret.txt", "/samples/../../secret.txt", "/..%2fsecret.txt"],
    )
    def test_path_traversal_never_escapes_dist(self, spa_client: TestClient, path):
        resp = spa_client.get(path)
        assert "do not serve" not in resp.text

    def test_reserved_paths_do_not_fall_back_to_index(self, spa_client: TestClient):
        assert (
            spa_client.get("/docs/anything").headers["content-type"].startswith("application/json")
        )

    def test_api_post_still_works_under_spa(self, spa_client: TestClient):
        resp = spa_client.post(
            "/api/v1/catalog/match-preview",
            json={"items": [{"query": "tamper 58mm", "quantity": 1}]},
        )
        assert resp.status_code == 200

    def test_without_dist_the_api_runs_alone(self, api_only_client: TestClient):
        assert api_only_client.get("/api/v1/health").status_code == 200
        root = api_only_client.get("/")
        assert root.status_code == 404 and root.json() == {"detail": "Not Found"}

    def test_settings_resolve_dist(self, dist: Path, tmp_path: Path):
        assert Settings(static_dist_dir=str(dist)).resolved_static_dist() == dist
        assert Settings(static_dist_dir=str(tmp_path / "nope")).resolved_static_dist() is None

    def test_render_external_url_drives_paypal_return_urls(self):
        s = Settings(render_external_url="https://catalogpay.onrender.com/")
        assert s.paypal_return_url == "https://catalogpay.onrender.com/checkout/return"
        assert s.paypal_cancel_url == "https://catalogpay.onrender.com/checkout/cancel"
        explicit = Settings(
            render_external_url="https://x.onrender.com", paypal_return_url="https://shop.example/r"
        )
        assert explicit.paypal_return_url == "https://shop.example/r"


class TestDemoOrderSeeding:
    def test_seeds_one_completed_multi_vendor_order(self, seeded_session: Session):
        stock_before = dict(
            seeded_session.execute(select(ProductSKU.sku_code, ProductSKU.stock_quantity)).all()
        )
        assert seed_demo_orders(seeded_session) is True

        order = seeded_session.scalar(select(Order).where(Order.public_order_id == DEMO_ORDER_ID))
        assert order is not None and order.status == "COMPLETED"
        assert (
            order.paypal_mode == "sandbox_simulation" and order.paypal_capture_status == "COMPLETED"
        )
        assert len(order.line_items) == 7 and len(order.payouts) == 4  # every vendor is paid
        assert (
            order.gross_subtotal_cents
            == order.total_platform_fee_cents + order.total_vendor_payout_cents
        )
        assert order.gross_subtotal_cents == sum(li.subtotal_cents for li in order.line_items)
        for payout in order.payouts:
            assert payout.status == PayoutStatus.SUCCESS
            assert payout.paypal_payout_batch_id.startswith("SIM-PAYOUT-")
            assert (
                payout.gross_subtotal_cents
                == payout.platform_fee_cents + payout.payout_amount_cents
            )
        # Historical: catalog stock is untouched.
        stock_after = dict(
            seeded_session.execute(select(ProductSKU.sku_code, ProductSKU.stock_quantity)).all()
        )
        assert stock_after == stock_before

    def test_is_idempotent_and_skips_when_orders_exist(self, seeded_session: Session):
        assert seed_demo_orders(seeded_session) is True
        assert seed_demo_orders(seeded_session) is False
        assert seeded_session.scalar(select(func.count()).select_from(Order)) == 1
        assert seeded_session.scalar(select(func.count()).select_from(VendorPayout)) == 4

    def test_demo_order_is_served_by_the_ledger_api(
        self, client: TestClient, seeded_session: Session
    ):
        seed_demo_orders(seeded_session)
        listing = client.get("/api/v1/orders").json()
        assert [o["public_order_id"] for o in listing["items"]] == [DEMO_ORDER_ID]
        detail = client.get(f"/api/v1/orders/{DEMO_ORDER_ID}").json()
        assert detail["status"] == "COMPLETED"
        assert {p["status"] for p in detail["payouts"]} == {"SUCCESS"}
        # Re-capturing the historical order is a safe no-op.
        assert (
            client.post(f"/api/v1/orders/{DEMO_ORDER_ID}/capture").json()["status"] == "COMPLETED"
        )

    def test_startup_seeds_when_enabled(
        self, engine: Engine, seeded_session: Session, dist: Path, monkeypatch
    ):
        import app.main as main_module

        factory = build_session_factory(engine)
        monkeypatch.setattr(main_module, "SessionLocal", factory)
        monkeypatch.setattr(main_module, "init_db", lambda: None)
        settings = Settings(static_dist_dir=str(dist), seed_on_startup=True, seed_demo_orders=True)
        with TestClient(create_app(settings)):
            pass
        assert seeded_session.scalar(select(func.count()).select_from(Order)) == 1


class TestSampleAssets:
    def test_sample_po_png_is_a_valid_uploadable_image(self):
        png = (REPO_ROOT / "samples" / "sample_wholesale_po.png").read_bytes()
        assert 1_000 < len(png) < 10 * 1024 * 1024
        assert validate_media(png, "image/png", 10 * 1024 * 1024) == "image/png"

    def test_sample_assets_are_mirrored_for_the_frontend(self):
        for name in ("sample_wholesale_po.png", "sample_order_note.txt"):
            assert (REPO_ROOT / "samples" / name).read_bytes() == (
                REPO_ROOT / "frontend" / "public" / "samples" / name
            ).read_bytes()

    def test_sample_text_order_resolves_end_to_end(self, client: TestClient):
        text = (REPO_ROOT / "samples" / "sample_order_note.txt").read_text(encoding="utf-8")
        body = client.post("/api/v1/orders/parse-text", json={"text": text}).json()
        preview = body["match_preview"]
        assert body["customer_note"].startswith("deliver Thursday before 7am")
        assert len(preview["lines"]) == 9
        assert preview["all_lines_billable"] is True
        assert len(preview["vendor_payouts"]) == 4
