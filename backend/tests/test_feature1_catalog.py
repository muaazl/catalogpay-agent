"""Feature 1: foundation, financial schema, seed catalog, hybrid SKU matcher, catalog API."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.db.session import build_engine, get_db
from app.models.catalog import ProductSKU, Vendor
from app.schemas.catalog import MatchPreviewItem
from app.services.money import line_subtotal_cents, platform_fee_cents, split_fee
from app.services.order_preview import build_match_preview
from app.services.seed import SKU_SEED, VENDOR_SEED, seed_catalog
from app.services.sku_matcher import (
    CharNgramTfidf,
    HybridSKUMatcher,
    MatchStatus,
    extract_measures,
    normalize_text,
)

# =============================================================================================
# Database initialization & schema
# =============================================================================================


class TestDatabase:
    def test_tables_created(self, engine):
        tables = set(inspect(engine).get_table_names())
        assert {"vendors", "product_skus"} <= tables

    def test_sqlite_pragmas_wal_and_foreign_keys(self, tmp_path: Path):
        eng = build_engine(f"sqlite:///{tmp_path / 'pragma.db'}")
        try:
            with eng.connect() as conn:
                assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
                assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        finally:
            eng.dispose()

    def test_in_memory_engine_shares_one_database(self, engine):
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE probe (x INTEGER)"))
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM probe")).scalar() == 0

    def test_foreign_key_enforced(self, session: Session):
        session.add(
            ProductSKU(
                sku_code="ORPHAN-1", vendor_id=9999, name="x", category="x", unit_price_cents=100
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    def test_sku_code_unique(self, seeded_session: Session):
        vendor_id = seeded_session.scalars(select(Vendor.id)).first()
        seeded_session.add(
            ProductSKU(
                sku_code="SPR-COF-ARB-500G",
                vendor_id=vendor_id,
                name="dup",
                category="x",
                unit_price_cents=1,
            )
        )
        with pytest.raises(IntegrityError):
            seeded_session.commit()

    @pytest.mark.parametrize("field,value", [("unit_price_cents", -1), ("stock_quantity", -5)])
    def test_sku_check_constraints(self, seeded_session: Session, field, value):
        vendor_id = seeded_session.scalars(select(Vendor.id)).first()
        kwargs = {
            "sku_code": f"BAD-{field}",
            "vendor_id": vendor_id,
            "name": "bad",
            "category": "x",
            "unit_price_cents": 100,
            field: value,
        }
        seeded_session.add(ProductSKU(**kwargs))
        with pytest.raises(IntegrityError):
            seeded_session.commit()

    def test_vendor_commission_bps_range(self, session: Session):
        session.add(
            Vendor(
                name="Greedy",
                paypal_merchant_email="g@sandbox.paypal.com",
                commission_rate_bps=10_001,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


# =============================================================================================
# Seeding
# =============================================================================================


class TestSeed:
    def test_seed_populates_catalog(self, session: Session):
        report = seed_catalog(session)
        assert (report.vendors_created, report.skus_created) == (4, 25)
        assert session.scalar(select(func.count()).select_from(Vendor)) == 4
        assert session.scalar(select(func.count()).select_from(ProductSKU)) == 25

    def test_seed_is_idempotent(self, session: Session):
        seed_catalog(session)
        second = seed_catalog(session)
        assert second.vendors_created == second.skus_created == 0
        assert second.vendors_updated == second.skus_updated == 0
        assert session.scalar(select(func.count()).select_from(Vendor)) == 4
        assert session.scalar(select(func.count()).select_from(ProductSKU)) == 25

    def test_reseed_repairs_drifted_rows_without_duplicating(self, seeded_session: Session):
        sku = seeded_session.scalar(select(ProductSKU).where(ProductSKU.sku_code == "GCB-YST-500G"))
        sku.unit_price_cents = 1
        seeded_session.commit()
        report = seed_catalog(seeded_session)
        assert report.skus_updated == 1 and report.skus_created == 0
        seeded_session.refresh(sku)
        assert sku.unit_price_cents == 899
        assert seeded_session.scalar(select(func.count()).select_from(ProductSKU)) == 25

    def test_seed_data_integrity(self, seeded_session: Session):
        vendors = seeded_session.scalars(select(Vendor)).all()
        assert len({v.paypal_merchant_email for v in vendors}) == 4
        assert all(v.paypal_merchant_email.endswith("@sandbox.paypal.com") for v in vendors)
        assert all(isinstance(v.commission_rate_bps, int) for v in vendors)

        skus = seeded_session.scalars(select(ProductSKU)).all()
        assert {s.category for s in skus} == {
            "Specialty Coffee",
            "Tea",
            "Cafe Equipment",
            "Eco Packaging",
            "Bakery Supplies",
        }
        assert {s.vendor_id for s in skus} == {v.id for v in vendors}
        for s in skus:
            assert type(s.unit_price_cents) is int and s.unit_price_cents > 0
            assert s.currency == "USD"
            assert isinstance(s.aliases_json, list) and s.aliases_json
            assert all(isinstance(a, str) for a in s.aliases_json)

    def test_seed_constants_have_no_duplicate_keys(self):
        assert len({s["sku_code"] for s in SKU_SEED}) == len(SKU_SEED) == 25
        assert len({v["paypal_merchant_email"] for v in VENDOR_SEED}) == len(VENDOR_SEED) == 4


# =============================================================================================
# Normalization
# =============================================================================================


class TestNormalization:
    @pytest.mark.parametrize(
        "raw,expected_measure",
        [
            ("0.5kg", "500g"),
            ("0.5 kg", "500g"),
            ("0,5 kg", "500g"),
            ("half kg", "500g"),
            ("half a kilo", "500g"),
            ("½ kg", "500g"),
            ("quarter kg", "250g"),
            ("500 grams", "500g"),
            ("1 L", "1000ml"),
            ("1 litre", "1000ml"),
            ("600 ML", "600ml"),
            ("2.5kg", "2500g"),
            ("5.8cm", "58mm"),
            ("58 mm", "58mm"),
            ("12 oz", "12oz"),
        ],
    )
    def test_unit_canonicalization(self, raw, expected_measure):
        assert expected_measure in extract_measures(normalize_text(f"beans {raw}"))

    def test_equivalent_units_normalize_identically(self):
        variants = [
            "arabica beans 500g",
            "Arabica Beans 0.5kg",
            "arabica beans half kg",
            "arabica  beans, 0.5 KG!",
        ]
        assert len({normalize_text(v) for v in variants}) == 1

    def test_dozen_and_stopwords(self):
        assert normalize_text("a dozen of the croissants please") == "12 croissants"
        assert normalize_text("half dozen croissants") == "6 croissants"

    def test_tfidf_identical_text_cosine_is_one(self):
        tfidf = CharNgramTfidf(["oat milk", "espresso tamper"])
        sims = tfidf.cosine("oat milk")
        assert sims[0] == pytest.approx(1.0)
        assert sims[1] < 0.2


# =============================================================================================
# Hybrid SKU matcher
# =============================================================================================


class TestHybridMatcher:
    @pytest.mark.parametrize(
        "query,expected_sku",
        [
            ("Ceremonial Grade Matcha Powder", "SPR-TEA-MAT-100G"),
            ("Stainless Steel Espresso Tamper 58mm", "BPE-TMP-58MM"),
            ("Unbleached Bread Flour", "GCB-FLR-BRD-25KG"),
            ("SPR-COF-ETH-1KG", "SPR-COF-ETH-1KG"),
        ],
    )
    def test_exact_names_and_codes(self, matcher: HybridSKUMatcher, query, expected_sku):
        result = matcher.match(query)
        assert result.status is MatchStatus.MATCHED
        assert result.best.entry.sku_code == expected_sku
        assert result.best.confidence_score >= 0.9

    @pytest.mark.parametrize(
        "query,expected_sku",
        [
            ("arabica beans half kg", "SPR-COF-ARB-500G"),
            ("oat milk barista case", "SPR-OAT-BAR-6X1L"),
            ("espresso tamper 58mm", "BPE-TMP-58MM"),
            ("java jackets", "GLP-SLV-1000"),
            ("yirgacheffe", "SPR-COF-ETH-1KG"),
            ("bread flour 25 kg", "GCB-FLR-BRD-25KG"),
            ("frozen croissants box", "GCB-CRS-FRZ-48"),
            ("sip lids 12oz", "GLP-LID-12OZ-1000"),
        ],
    )
    def test_slang_and_aliases(self, matcher: HybridSKUMatcher, query, expected_sku):
        result = matcher.match(query)
        assert result.status is MatchStatus.MATCHED, result
        assert result.best.entry.sku_code == expected_sku

    @pytest.mark.parametrize(
        "query,expected_sku",
        [
            ("espreso tampr 58mm", "BPE-TMP-58MM"),
            ("compostible cups 12 oz", "GLP-CUP-12OZ-1000"),
            ("vanila bean paste", "GCB-VAN-PST-1L"),
            ("dark chocolat callets", "GCB-CHC-DRK-2500G"),
            ("instnt dry yeast", "GCB-YST-500G"),
        ],
    )
    def test_typos(self, matcher: HybridSKUMatcher, query, expected_sku):
        result = matcher.match(query)
        assert result.status is MatchStatus.MATCHED, result
        assert result.best.entry.sku_code == expected_sku

    @pytest.mark.parametrize("size", ["500g", "0.5kg", "0.5 kg", "half kg", "500 grams"])
    def test_unit_variations_pick_500g_bag(self, matcher: HybridSKUMatcher, size):
        result = matcher.match(f"colombian arabica {size}")
        assert result.status is MatchStatus.MATCHED
        assert result.best.entry.sku_code == "SPR-COF-ARB-500G"
        assert result.best.unit_score == 1.0

    def test_unit_variations_score_identically(self, matcher: HybridSKUMatcher):
        scores = {
            matcher.match(f"arabica beans {s}").best.confidence_score
            for s in ("500g", "0.5kg", "half kg")
        }
        assert len(scores) == 1

    def test_size_disambiguates_sibling_skus(self, matcher: HybridSKUMatcher):
        assert matcher.match("arabica beans 1kg").best.entry.sku_code == "SPR-COF-ARB-1KG"
        assert matcher.match("arabica beans 1000 g").best.entry.sku_code == "SPR-COF-ARB-1KG"
        assert matcher.match("tamper 53 mm").best.entry.sku_code == "BPE-TMP-53MM"
        assert matcher.match("tamper 5.8 cm").best.entry.sku_code == "BPE-TMP-58MM"

    def test_size_conflict_is_penalized(self, matcher: HybridSKUMatcher):
        result = matcher.match("arabica beans 500g", top_k=5)
        by_code = {c.entry.sku_code: c for c in result.candidates}
        assert by_code["SPR-COF-ARB-1KG"].unit_score == 0.0
        assert (
            by_code["SPR-COF-ARB-500G"].confidence_score
            - by_code["SPR-COF-ARB-1KG"].confidence_score
        ) > 0.1

    @pytest.mark.parametrize(
        "query",
        [
            "laptop charger",
            "quantum flux capacitor",
            "iphone 15 pro max",
            "gold watch",
            "car tyres 205/55 r16",
            "zzzzqqq",
        ],
    )
    def test_unknown_items_are_unmatched(self, matcher: HybridSKUMatcher, query):
        result = matcher.match(query)
        assert result.status is MatchStatus.UNMATCHED
        assert result.best.confidence_score < matcher.low_confidence_threshold

    @pytest.mark.parametrize("query", ["", "   ", "!!!", "please"])
    def test_empty_queries_are_unmatched_without_candidates(self, matcher, query):
        result = matcher.match(query)
        assert result.status is MatchStatus.UNMATCHED
        assert result.candidates == ()

    def test_ambiguous_match_requires_confirmation(self, matcher: HybridSKUMatcher):
        # Same product name exists as a 500g and a 1kg SKU, and no size was given.
        result = matcher.match("Colombian Arabica Whole Bean Coffee")
        assert result.is_ambiguous
        assert result.status is MatchStatus.LOW_CONFIDENCE
        assert {c.entry.sku_code for c in result.candidates[:2]} == {
            "SPR-COF-ARB-500G",
            "SPR-COF-ARB-1KG",
        }

    def test_results_are_deterministic_bounded_and_sorted(self, matcher: HybridSKUMatcher):
        a = matcher.match("oat milk barista case", top_k=5)
        b = matcher.match("oat milk barista case", top_k=5)
        assert a == b
        assert len(a.candidates) == 5
        scores = [c.confidence_score for c in a.candidates]
        assert scores == sorted(scores, reverse=True)
        assert all(0.0 <= s <= 1.0 for s in scores)

    def test_candidate_carries_reasoning_price_and_vendor(self, matcher: HybridSKUMatcher):
        best = matcher.match("arabica beans half kg").best
        assert "fuzzy=" in best.match_reasoning and "cosine=" in best.match_reasoning
        assert "500g" in best.match_reasoning
        assert best.entry.unit_price_cents == 1495
        assert best.entry.vendor.paypal_merchant_email == "vendor-roasters@sandbox.paypal.com"

    def test_inactive_skus_are_excluded(self, seeded_session: Session):
        sku = seeded_session.scalar(select(ProductSKU).where(ProductSKU.sku_code == "GLP-SLV-1000"))
        sku.is_active = False
        seeded_session.commit()
        m = HybridSKUMatcher.from_skus(seeded_session.scalars(select(ProductSKU)).all())
        assert len(m.entries) == 24
        assert all(c.entry.sku_code != "GLP-SLV-1000" for c in m.match("java jackets").candidates)

    def test_invalid_configuration(self):
        with pytest.raises(ValueError):
            HybridSKUMatcher([], match_threshold=0.3, low_confidence_threshold=0.5)
        assert HybridSKUMatcher([]).match("anything").status is MatchStatus.UNMATCHED


# =============================================================================================
# Financial arithmetic
# =============================================================================================


class TestMoney:
    @pytest.mark.parametrize(
        "subtotal,bps,fee",
        [
            (5980, 500, 299),
            (7998, 800, 640),  # 639.84 -> 640
            (1, 5000, 1),  # 0.5 cent rounds half-up
            (1, 4999, 0),
            (3, 3333, 1),  # 0.9999 -> 1
            (99_999, 650, 6500),  # 6499.935 -> 6500
            (12_345, 450, 556),  # 555.525 -> 556
            (0, 800, 0),
            (1_000_001, 0, 0),
            (1_000_001, 10_000, 1_000_001),
        ],
    )
    def test_fee_rounding(self, subtotal, bps, fee):
        assert platform_fee_cents(subtotal, bps) == fee
        split = split_fee(subtotal, bps)
        assert split.platform_fee_cents + split.net_vendor_payout_cents == subtotal

    def test_no_rounding_drift_exhaustive(self):
        for bps in (0, 1, 333, 450, 500, 650, 799, 800, 1234, 9999, 10_000):
            for subtotal in range(0, 2_000, 7):
                s = split_fee(subtotal, bps)
                assert s.platform_fee_cents + s.net_vendor_payout_cents == s.subtotal_cents
                assert 0 <= s.platform_fee_cents <= subtotal
                assert isinstance(s.platform_fee_cents, int)

    def test_line_subtotal(self):
        assert line_subtotal_cents(1495, 3) == 4485
        with pytest.raises(ValueError):
            line_subtotal_cents(-1, 3)

    @pytest.mark.parametrize("bps", [-1, 10_001])
    def test_invalid_bps(self, bps):
        with pytest.raises(ValueError):
            platform_fee_cents(100, bps)

    def test_preview_totals_balance_across_vendors(self, matcher: HybridSKUMatcher):
        items = [
            MatchPreviewItem(query="arabica beans half kg", quantity=7),
            MatchPreviewItem(query="espresso tamper 58mm", quantity=3),
            MatchPreviewItem(query="compostable hot cups 12oz", quantity=1),
            MatchPreviewItem(query="java jackets", quantity=3),
            MatchPreviewItem(query="instant dry yeast", quantity=11),
            MatchPreviewItem(query="earl grey loose leaf", quantity=13),
        ]
        preview = build_match_preview(matcher, items, currency="USD")
        assert preview.all_lines_billable
        assert len(preview.vendor_payouts) == 4
        for line in preview.lines:
            assert line.subtotal_cents == line.unit_price_cents * line.quantity
            assert line.subtotal_cents == line.platform_fee_cents + line.net_vendor_payout_cents
        for p in preview.vendor_payouts:
            assert p.subtotal_cents == p.platform_fee_cents + p.net_vendor_payout_cents
        t = preview.totals
        assert t.subtotal_cents == t.platform_fee_cents + t.net_vendor_payout_cents
        assert t.subtotal_cents == sum(line.subtotal_cents for line in preview.lines)
        assert t.platform_fee_cents == sum(p.platform_fee_cents for p in preview.vendor_payouts)


# =============================================================================================
# API
# =============================================================================================


class TestHealthEndpoint:
    def test_health_ok(self, client: TestClient):
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["database"] == {"connected": True, "dialect": "sqlite", "error": None}
        assert body["active_sku_count"] == 25

    def test_health_reports_db_outage(self, client: TestClient):
        class BrokenSession:
            def get_bind(self):
                return build_engine("sqlite:///:memory:")

            def execute(self, *_a, **_k):
                raise OperationalError("SELECT 1", {}, Exception("db down"))

        client.app.dependency_overrides[get_db] = lambda: BrokenSession()
        resp = client.get("/api/v1/health")
        assert resp.status_code == 503
        assert resp.json()["status"] == "degraded"
        assert resp.json()["database"]["connected"] is False


class TestSkuListEndpoint:
    def test_list_all(self, client: TestClient):
        body = client.get("/api/v1/catalog/skus").json()
        assert body["total"] == 25 and len(body["items"]) == 25
        item = body["items"][0]
        assert {"sku_code", "aliases", "unit_price_cents", "vendor"} <= item.keys()
        assert isinstance(item["unit_price_cents"], int)

    def test_filter_by_vendor_id_and_name(self, client: TestClient):
        all_items = client.get("/api/v1/catalog/skus").json()["items"]
        roaster_id = next(i["vendor"]["id"] for i in all_items if i["sku_code"].startswith("SPR"))
        by_id = client.get("/api/v1/catalog/skus", params={"vendor_id": roaster_id}).json()
        assert by_id["total"] == 8
        by_name = client.get("/api/v1/catalog/skus", params={"vendor": "roasters"}).json()
        assert {i["sku_code"] for i in by_name["items"]} == {i["sku_code"] for i in by_id["items"]}

    def test_filter_by_category_case_insensitive(self, client: TestClient):
        body = client.get("/api/v1/catalog/skus", params={"category": "tea"}).json()
        assert body["total"] == 2
        assert all(i["category"] == "Tea" for i in body["items"])

    def test_search_query(self, client: TestClient):
        body = client.get("/api/v1/catalog/skus", params={"q": "TAMPER"}).json()
        assert {i["sku_code"] for i in body["items"]} == {"BPE-TMP-58MM", "BPE-TMP-53MM"}

    def test_combined_filters_and_pagination(self, client: TestClient):
        page = client.get("/api/v1/catalog/skus", params={"limit": 5, "offset": 20}).json()
        assert page["total"] == 25 and len(page["items"]) == 5
        none = client.get(
            "/api/v1/catalog/skus", params={"category": "tea", "vendor": "bakery"}
        ).json()
        assert none["total"] == 0 and none["items"] == []

    @pytest.mark.parametrize(
        "params", [{"limit": 0}, {"limit": 500}, {"offset": -1}, {"vendor_id": 0}]
    )
    def test_invalid_query_params(self, client: TestClient, params):
        assert client.get("/api/v1/catalog/skus", params=params).status_code == 422


class TestMatchPreviewEndpoint:
    URL = "/api/v1/catalog/match-preview"

    def test_multi_vendor_preview(self, client: TestClient):
        resp = client.post(
            self.URL,
            json={
                "items": [
                    {"query": "arabica beans half kg", "quantity": 4},
                    {"query": "espresso tamper 58mm", "quantity": 2},
                ]
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["currency"] == "USD" and body["all_lines_billable"] is True

        coffee, tamper = body["lines"]
        assert coffee["matched_sku"]["sku_code"] == "SPR-COF-ARB-500G"
        assert (
            coffee["subtotal_cents"],
            coffee["platform_fee_cents"],
            coffee["net_vendor_payout_cents"],
        ) == (5980, 299, 5681)
        assert tamper["matched_sku"]["sku_code"] == "BPE-TMP-58MM"
        assert (
            tamper["subtotal_cents"],
            tamper["platform_fee_cents"],
            tamper["net_vendor_payout_cents"],
        ) == (7998, 640, 7358)

        assert [p["paypal_merchant_email"] for p in body["vendor_payouts"]] == [
            "vendor-roasters@sandbox.paypal.com",
            "vendor-equipment@sandbox.paypal.com",
        ]
        assert body["totals"] == {
            "subtotal_cents": 13978,
            "platform_fee_cents": 939,
            "net_vendor_payout_cents": 13039,
        }

    def test_unknown_item_is_unresolved_and_not_billed(self, client: TestClient):
        body = client.post(
            self.URL,
            json={
                "items": [
                    {"query": "oat milk barista case", "quantity": 2},
                    {"query": "quantum flux capacitor", "quantity": 1},
                ]
            },
        ).json()
        unknown = body["lines"][1]
        assert unknown["match_status"] == "unmatched"
        assert unknown["matched_sku"] is None and unknown["is_billable"] is False
        assert unknown["subtotal_cents"] == 0 and unknown["issues"]
        assert body["unresolved_line_indexes"] == [1]
        assert body["all_lines_billable"] is False
        assert body["totals"]["subtotal_cents"] == 2 * 1999

    def test_out_of_stock_sku(self, client: TestClient):
        line = client.post(
            self.URL,
            json={
                "items": [
                    {"query": "digital coffee scale with timer", "quantity": 1},
                ]
            },
        ).json()["lines"][0]
        assert line["matched_sku"]["sku_code"] == "BPE-SCL-TMR"
        assert line["stock_status"] == "insufficient_stock"
        assert line["available_quantity"] == 0
        assert line["is_billable"] is False and line["subtotal_cents"] == 0

    def test_stock_is_reserved_across_lines(self, client: TestClient):
        body = client.post(
            self.URL,
            json={
                "items": [
                    {"query": "commercial burr grinder", "quantity": 3},
                    {"query": "espresso grinder 64mm", "quantity": 3},  # only 5 in stock in total
                ]
            },
        ).json()
        first, second = body["lines"]
        assert first["matched_sku"]["sku_code"] == second["matched_sku"]["sku_code"]
        assert first["stock_status"] == "in_stock"
        assert second["stock_status"] == "insufficient_stock"
        assert second["available_quantity"] == 2

    def test_ambiguous_line_needs_confirmation(self, client: TestClient):
        line = client.post(
            self.URL,
            json={
                "items": [
                    {"query": "Colombian Arabica Whole Bean Coffee", "quantity": 1},
                ]
            },
        ).json()["lines"][0]
        assert line["match_status"] == "low_confidence" and line["is_ambiguous"] is True
        assert line["matched_sku"] is None and len(line["candidates"]) >= 2

    def test_top_k(self, client: TestClient):
        body = client.post(
            self.URL, json={"items": [{"query": "tamper", "quantity": 1}], "top_k": 5}
        ).json()
        assert len(body["lines"][0]["candidates"]) == 5

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"items": []},
            {"items": [{"query": "beans"}]},
            {"items": [{"query": "beans", "quantity": 0}]},
            {"items": [{"query": "beans", "quantity": -2}]},
            {"items": [{"query": "beans", "quantity": 1.5}]},
            {"items": [{"query": "   ", "quantity": 1}]},
            {"items": [{"query": "", "quantity": 1}]},
            {"items": [{"query": "beans", "quantity": 1}], "top_k": 0},
            {"items": [{"query": "beans", "quantity": 1}] * 101},
        ],
    )
    def test_validation_errors(self, client: TestClient, payload):
        assert client.post(self.URL, json=payload).status_code == 422
