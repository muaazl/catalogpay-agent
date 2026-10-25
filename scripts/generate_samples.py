"""Regenerate the demo sample assets.

    backend/.venv/Scripts/python scripts/generate_samples.py      (Windows)
    backend/.venv/bin/python scripts/generate_samples.py          (macOS/Linux)

Writes samples/sample_wholesale_po.png and samples/sample_order_note.txt and mirrors both
into frontend/public/samples/ so the Command Center can offer them as downloads.
Requires Pillow (`pip install -e "backend[samples]"`); the app itself never needs it.
"""

import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT_DIRS = (ROOT / "samples", ROOT / "frontend" / "public" / "samples")

ORDER_NOTE = """\
Hi CatalogPay team - weekly restock for Elm Street Cafe + the bakery counter pls!

- 3x 1kg Ethiopian Yirgacheffe
- 2 cases Barista Oat Milk
- espresso tamper 58mm x1
- 2 cases compostable 12oz hot cups + 2 cases 12oz sip lids
- instant dry yeast 500g x 3
- 1 box frozen croissants
- vanilla bean paste 1L x2
- 5 tins ceremonial matcha 100g

Note: deliver Thursday before 7am, back door. Thanks!
"""

PO_LINES = [
    ("3", "1kg Ethiopian Yirgacheffe beans", "Summit Peak Roasters"),
    ("2", "cases Barista Oat Milk (6 x 1L)", "Summit Peak Roasters"),
    ("1", "Espresso tamper 58mm", "BaristaPro Equipment"),
    ("2", "cases 12oz compostable hot cups", "GreenLoop Packaging"),
    ("2", "cases 12oz sip lids", "GreenLoop Packaging"),
    ("3", "Instant dry yeast 500g", "Golden Crumb Bakery"),
    ("1", "Box frozen croissants (48)", "Golden Crumb Bakery"),
]


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except (TypeError, OSError):  # very old Pillow / no FreeType
        return ImageFont.load_default()


def render_po(path: Path) -> None:
    w, h = 1000, 1240
    img = Image.new("RGB", (w, h), "#FBFAF6")
    d = ImageDraw.Draw(img)
    navy, ink, muted, rule = "#003087", "#1E293B", "#64748B", "#CBD5E1"

    d.rectangle([0, 0, w, 120], fill=navy)
    d.text((48, 30), "PURCHASE ORDER", font=_font(44), fill="white")
    d.text((50, 84), "Elm Street Cafe & Bakery  ·  PO #4471", font=_font(22), fill="#BFDBFE")
    d.text((650, 40), "Date: 2026-10-22", font=_font(22), fill="white")
    d.text((650, 74), "Terms: PayPal on delivery", font=_font(20), fill="#BFDBFE")

    d.text((48, 160), "Ship to: 214 Elm Street, Portland OR 97205", font=_font(22), fill=ink)
    d.text((48, 196), "Contact: Maya R. (kitchen)  ·  wholesale@elmstreet.example", font=_font(20), fill=muted)

    top = 260
    d.rectangle([40, top, w - 40, top + 52], fill="#E2E8F0")
    for x, label in ((60, "QTY"), (150, "ITEM"), (640, "SUPPLIER")):
        d.text((x, top + 14), label, font=_font(22), fill=ink)
    y = top + 52
    for qty, item, vendor in PO_LINES:
        y += 64
        d.line([40, y + 22, w - 40, y + 22], fill=rule, width=1)
        d.text((68, y - 18), qty, font=_font(28), fill=ink)
        d.text((150, y - 16), item, font=_font(26), fill=ink)
        d.text((640, y - 12), vendor, font=_font(21), fill=muted)

    y += 80
    d.text((48, y), "Notes:", font=_font(24), fill=ink)
    d.text((48, y + 40), "Deliver Thursday before 7am - use the back door.", font=_font(22), fill=ink)
    d.text((48, y + 76), "Prices per our wholesale catalog; split invoices by supplier please.", font=_font(22), fill=muted)

    d.rectangle([40, h - 150, w - 40, h - 40], outline=rule, width=2)
    d.text((60, h - 130), "Approved by: M. Rivera", font=_font(24), fill=ink)
    d.text((60, h - 90), "Sample document for the CatalogPay Agent demo - not a real order.", font=_font(18), fill=muted)
    img.save(path, format="PNG", optimize=True)


def main() -> None:
    primary, *mirrors = OUT_DIRS
    primary.mkdir(parents=True, exist_ok=True)
    render_po(primary / "sample_wholesale_po.png")
    (primary / "sample_order_note.txt").write_text(ORDER_NOTE, encoding="utf-8", newline="\n")
    for mirror in mirrors:
        mirror.mkdir(parents=True, exist_ok=True)
        for name in ("sample_wholesale_po.png", "sample_order_note.txt"):
            shutil.copyfile(primary / name, mirror / name)
    for out in OUT_DIRS:
        for f in sorted(out.iterdir()):
            print(f"{f.relative_to(ROOT)}  {f.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
