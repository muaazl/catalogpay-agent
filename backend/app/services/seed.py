"""Idempotent seeder for the demo multi-vendor wholesale catalog.

Vendors are keyed on ``paypal_merchant_email`` and SKUs on ``sku_code``: re-running the seed
updates existing rows in place and never creates duplicates.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.catalog import ProductSKU, Vendor

ROASTERS = "vendor-roasters@sandbox.paypal.com"
EQUIPMENT = "vendor-equipment@sandbox.paypal.com"
PACKAGING = "vendor-packaging@sandbox.paypal.com"
BAKERY = "vendor-bakery@sandbox.paypal.com"

VENDOR_SEED: tuple[dict[str, Any], ...] = (
    {"name": "Summit Peak Roasters", "paypal_merchant_email": ROASTERS, "commission_rate_bps": 500},
    {
        "name": "BaristaPro Equipment Co.",
        "paypal_merchant_email": EQUIPMENT,
        "commission_rate_bps": 800,
    },
    {
        "name": "GreenLoop Eco Packaging",
        "paypal_merchant_email": PACKAGING,
        "commission_rate_bps": 650,
    },
    {
        "name": "Golden Crumb Bakery Supply",
        "paypal_merchant_email": BAKERY,
        "commission_rate_bps": 450,
    },
)

SKU_SEED: tuple[dict[str, Any], ...] = (
    # --- Summit Peak Roasters: specialty coffee & tea ---
    {
        "sku_code": "SPR-COF-ARB-500G",
        "vendor": ROASTERS,
        "name": "Colombian Arabica Whole Bean Coffee",
        "description": "Medium roast, washed Huila arabica. Notes of caramel and red apple.",
        "category": "Specialty Coffee",
        "unit_of_measure": "500g bag",
        "aliases_json": ["arabica beans", "colombian coffee", "house beans", "medium roast"],
        "unit_price_cents": 1495,
        "stock_quantity": 200,
    },
    {
        "sku_code": "SPR-COF-ARB-1KG",
        "vendor": ROASTERS,
        "name": "Colombian Arabica Whole Bean Coffee",
        "description": "Medium roast, washed process Huila arabica in a wholesale 1kg valve bag.",
        "category": "Specialty Coffee",
        "unit_of_measure": "1kg bag",
        "aliases_json": ["arabica beans", "colombian coffee", "house beans kilo"],
        "unit_price_cents": 2790,
        "stock_quantity": 150,
    },
    {
        "sku_code": "SPR-COF-ETH-1KG",
        "vendor": ROASTERS,
        "name": "Ethiopia Yirgacheffe Single-Origin Coffee Beans",
        "description": "Light roast, floral and citrus. Ideal for pour-over menus.",
        "category": "Specialty Coffee",
        "unit_of_measure": "1kg bag",
        "aliases_json": ["yirg", "ethiopian beans", "single origin", "light roast"],
        "unit_price_cents": 3450,
        "stock_quantity": 120,
    },
    {
        "sku_code": "SPR-COF-ESP-1KG",
        "vendor": ROASTERS,
        "name": "House Espresso Blend Dark Roast",
        "description": "Brazil/Guatemala blend roasted for milk-based espresso drinks.",
        "category": "Specialty Coffee",
        "unit_of_measure": "1kg bag",
        "aliases_json": ["espresso beans", "dark roast", "house blend", "espresso blend"],
        "unit_price_cents": 2650,
        "stock_quantity": 90,
    },
    {
        "sku_code": "SPR-COF-DCF-500G",
        "vendor": ROASTERS,
        "name": "Swiss Water Decaf Coffee Beans",
        "description": "Chemical-free Swiss Water Process decaffeinated arabica.",
        "category": "Specialty Coffee",
        "unit_of_measure": "500g bag",
        "aliases_json": ["decaf", "decaffeinated beans", "swiss water"],
        "unit_price_cents": 1690,
        "stock_quantity": 60,
    },
    {
        "sku_code": "SPR-TEA-MAT-100G",
        "vendor": ROASTERS,
        "name": "Ceremonial Grade Matcha Powder",
        "description": "Stone-ground Uji matcha for lattes and ceremonial service.",
        "category": "Tea",
        "unit_of_measure": "100g tin",
        "aliases_json": ["matcha", "green tea powder", "matcha tin"],
        "unit_price_cents": 2899,
        "stock_quantity": 40,
    },
    {
        "sku_code": "SPR-TEA-EGR-500G",
        "vendor": ROASTERS,
        "name": "Organic Earl Grey Loose Leaf Tea",
        "description": "Ceylon black tea with natural bergamot oil.",
        "category": "Tea",
        "unit_of_measure": "500g bag",
        "aliases_json": ["earl grey", "loose leaf tea", "black tea", "bergamot tea"],
        "unit_price_cents": 1875,
        "stock_quantity": 75,
    },
    {
        "sku_code": "SPR-OAT-BAR-6X1L",
        "vendor": ROASTERS,
        "name": "Barista Edition Oat Milk",
        "description": "Foams like dairy. Shelf-stable cartons, case of six 1L cartons.",
        "category": "Specialty Coffee",
        "unit_of_measure": "case of 6 x 1L",
        "aliases_json": ["oat milk", "barista oat", "oat milk case", "plant milk", "oatly"],
        "unit_price_cents": 1999,
        "stock_quantity": 300,
    },
    # --- BaristaPro Equipment Co.: cafe equipment ---
    {
        "sku_code": "BPE-TMP-58MM",
        "vendor": EQUIPMENT,
        "name": "Stainless Steel Espresso Tamper 58mm",
        "description": "Calibrated flat-base tamper with walnut handle for 58mm baskets.",
        "category": "Cafe Equipment",
        "unit_of_measure": "unit",
        "aliases_json": ["tamper", "coffee tamper", "58 tamper", "espresso tamp"],
        "unit_price_cents": 3999,
        "stock_quantity": 45,
    },
    {
        "sku_code": "BPE-TMP-53MM",
        "vendor": EQUIPMENT,
        "name": "Stainless Steel Espresso Tamper 53mm",
        "description": "Flat-base tamper sized for 53mm portafilter baskets.",
        "category": "Cafe Equipment",
        "unit_of_measure": "unit",
        "aliases_json": ["tamper", "coffee tamper", "53 tamper", "small tamper"],
        "unit_price_cents": 3599,
        "stock_quantity": 30,
    },
    {
        "sku_code": "BPE-PIT-600ML",
        "vendor": EQUIPMENT,
        "name": "Milk Frothing Pitcher 600ml",
        "description": "Stainless steaming jug with latte-art spout.",
        "category": "Cafe Equipment",
        "unit_of_measure": "unit",
        "aliases_json": ["milk jug", "steaming pitcher", "frothing jug", "latte art jug"],
        "unit_price_cents": 1850,
        "stock_quantity": 80,
    },
    {
        "sku_code": "BPE-GRD-64MM",
        "vendor": EQUIPMENT,
        "name": "Commercial Burr Coffee Grinder 64mm",
        "description": "On-demand flat burr espresso grinder with 1.2kg hopper.",
        "category": "Cafe Equipment",
        "unit_of_measure": "unit",
        "aliases_json": ["grinder", "burr grinder", "espresso grinder", "coffee mill"],
        "unit_price_cents": 64900,
        "stock_quantity": 5,
    },
    {
        "sku_code": "BPE-SCL-TMR",
        "vendor": EQUIPMENT,
        "name": "Digital Coffee Scale with Timer",
        "description": "0.1g resolution brew scale with built-in shot timer.",
        "category": "Cafe Equipment",
        "unit_of_measure": "unit",
        "aliases_json": ["barista scale", "coffee scale", "brew scale"],
        "unit_price_cents": 3299,
        "stock_quantity": 0,
    },
    # --- GreenLoop Eco Packaging: eco-packaging ---
    {
        "sku_code": "GLP-CUP-12OZ-1000",
        "vendor": PACKAGING,
        "name": "Compostable Hot Cups 12oz",
        "description": "PLA-lined double-wall paper cups, certified compostable.",
        "category": "Eco Packaging",
        "unit_of_measure": "case of 1000",
        "aliases_json": ["12oz cups", "paper cups", "coffee cups", "hot cups", "takeaway cups"],
        "unit_price_cents": 12900,
        "stock_quantity": 40,
    },
    {
        "sku_code": "GLP-LID-12OZ-1000",
        "vendor": PACKAGING,
        "name": "Compostable Sip Lids 12oz",
        "description": "Plant-based CPLA sip-through lids fitting 12oz hot cups.",
        "category": "Eco Packaging",
        "unit_of_measure": "case of 1000",
        "aliases_json": ["sip lids", "cup lids", "coffee lids"],
        "unit_price_cents": 6900,
        "stock_quantity": 40,
    },
    {
        "sku_code": "GLP-SLV-1000",
        "vendor": PACKAGING,
        "name": "Corrugated Kraft Cup Sleeves",
        "description": "Recycled-fiber heat sleeves for 10-16oz cups.",
        "category": "Eco Packaging",
        "unit_of_measure": "case of 1000",
        "aliases_json": ["cup sleeves", "coffee sleeves", "java jackets", "cup jackets"],
        "unit_price_cents": 5499,
        "stock_quantity": 50,
    },
    {
        "sku_code": "GLP-STR-500",
        "vendor": PACKAGING,
        "name": "Unwrapped Paper Straws",
        "description": "Plastic-free 3-ply paper straws, 8mm bore.",
        "category": "Eco Packaging",
        "unit_of_measure": "box of 500",
        "aliases_json": ["straws", "eco straws", "paper straw"],
        "unit_price_cents": 1899,
        "stock_quantity": 120,
    },
    {
        "sku_code": "GLP-BAG-KRF-500",
        "vendor": PACKAGING,
        "name": "Kraft Paper Takeaway Bags",
        "description": "Flat-handle recycled kraft carry bags.",
        "category": "Eco Packaging",
        "unit_of_measure": "pack of 500",
        "aliases_json": ["takeaway bags", "paper bags", "kraft bags", "carry bags"],
        "unit_price_cents": 4599,
        "stock_quantity": 60,
    },
    {
        "sku_code": "GLP-BOX-PST-100",
        "vendor": PACKAGING,
        "name": "Windowed Pastry Boxes",
        "description": "Kraft bakery boxes with compostable PLA window.",
        "category": "Eco Packaging",
        "unit_of_measure": "pack of 100",
        "aliases_json": ["cake boxes", "pastry boxes", "bakery boxes"],
        "unit_price_cents": 3299,
        "stock_quantity": 35,
    },
    # --- Golden Crumb Bakery Supply: wholesale bakery supplies ---
    {
        "sku_code": "GCB-FLR-BRD-25KG",
        "vendor": BAKERY,
        "name": "Unbleached Bread Flour",
        "description": "13% protein hard red wheat flour for artisan loaves.",
        "category": "Bakery Supplies",
        "unit_of_measure": "25kg sack",
        "aliases_json": ["bread flour", "strong flour", "high gluten flour", "flour sack"],
        "unit_price_cents": 3899,
        "stock_quantity": 70,
    },
    {
        "sku_code": "GCB-BTR-SHT-1KG",
        "vendor": BAKERY,
        "name": "European Unsalted Butter Sheets 82%",
        "description": "Pre-sheeted lamination butter for croissant and puff pastry.",
        "category": "Bakery Supplies",
        "unit_of_measure": "1kg block",
        "aliases_json": ["butter sheets", "lamination butter", "croissant butter"],
        "unit_price_cents": 1599,
        "stock_quantity": 40,
    },
    {
        "sku_code": "GCB-YST-500G",
        "vendor": BAKERY,
        "name": "Instant Dry Yeast",
        "description": "Vacuum-packed instant yeast for direct dough addition.",
        "category": "Bakery Supplies",
        "unit_of_measure": "500g pack",
        "aliases_json": ["yeast", "instant yeast", "dry yeast"],
        "unit_price_cents": 899,
        "stock_quantity": 100,
    },
    {
        "sku_code": "GCB-CHC-DRK-2500G",
        "vendor": BAKERY,
        "name": "Dark Chocolate Callets 54%",
        "description": "Belgian couverture callets for baking, ganache and hot chocolate.",
        "category": "Bakery Supplies",
        "unit_of_measure": "2.5kg bag",
        "aliases_json": ["chocolate chips", "callets", "baking chocolate", "couverture"],
        "unit_price_cents": 4250,
        "stock_quantity": 30,
    },
    {
        "sku_code": "GCB-CRS-FRZ-48",
        "vendor": BAKERY,
        "name": "Frozen Butter Croissants Ready-to-Bake",
        "description": "Pre-proofed all-butter croissants, bake from frozen in 18 minutes.",
        "category": "Bakery Supplies",
        "unit_of_measure": "box of 48",
        "aliases_json": ["croissants", "frozen croissants", "bake off croissants"],
        "unit_price_cents": 5799,
        "stock_quantity": 20,
    },
    {
        "sku_code": "GCB-VAN-PST-1L",
        "vendor": BAKERY,
        "name": "Pure Vanilla Bean Paste",
        "description": "Madagascar bourbon vanilla paste with visible seeds.",
        "category": "Bakery Supplies",
        "unit_of_measure": "1L bottle",
        "aliases_json": ["vanilla paste", "vanilla extract", "vanilla bean"],
        "unit_price_cents": 3999,
        "stock_quantity": 15,
    },
)


@dataclass(frozen=True, slots=True)
class SeedReport:
    vendors_created: int
    vendors_updated: int
    skus_created: int
    skus_updated: int


def _apply(obj: object, fields: dict[str, Any]) -> bool:
    changed = False
    for key, value in fields.items():
        if getattr(obj, key) != value:
            setattr(obj, key, value)
            changed = True
    return changed


def seed_catalog(session: Session) -> SeedReport:
    """Upsert the demo vendors and SKUs. Safe to call any number of times."""
    vendors_created = vendors_updated = skus_created = skus_updated = 0

    existing_vendors = {v.paypal_merchant_email: v for v in session.scalars(select(Vendor))}
    vendors_by_email: dict[str, Vendor] = {}
    for spec in VENDOR_SEED:
        fields = {**spec, "is_active": True}
        vendor = existing_vendors.get(spec["paypal_merchant_email"])
        if vendor is None:
            vendor = Vendor(**fields)
            session.add(vendor)
            vendors_created += 1
        elif _apply(vendor, fields):
            vendors_updated += 1
        vendors_by_email[spec["paypal_merchant_email"]] = vendor
    session.flush()

    existing_skus = {s.sku_code: s for s in session.scalars(select(ProductSKU))}
    for spec in SKU_SEED:
        fields = {k: v for k, v in spec.items() if k != "vendor"}
        fields.update(
            vendor_id=vendors_by_email[spec["vendor"]].id,
            currency="USD",
            is_active=True,
        )
        sku = existing_skus.get(spec["sku_code"])
        if sku is None:
            session.add(ProductSKU(**fields))
            skus_created += 1
        elif _apply(sku, fields):
            skus_updated += 1

    session.commit()
    return SeedReport(vendors_created, vendors_updated, skus_created, skus_updated)
