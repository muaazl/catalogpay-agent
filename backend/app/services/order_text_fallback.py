"""Deterministic, offline order-text parser used when Gemini is not configured.

Handles the common shapes of wholesale order messages:

    - 2x 500g Arabica beans          -> qty 2, "500g Arabica beans"
    * 3 * Oat Milk Barista           -> qty 3, "Oat Milk Barista"
    Espresso tamper 58mm x 1         -> qty 1, "Espresso tamper 58mm"
    4 bags matcha powder 250g        -> qty 4, "matcha powder 250g", unit_hint "bags"
    yeast - 6 / yeast qty 6 / yeast: 6
    a case of oat milk, a dozen croissants

Lines are split on newlines, semicolons, pipes and commas (but not "1,000g" / "0,5kg").
Greetings and headers are dropped; "note:"/"deliver ..." lines become the customer note.
"""

import re

from pydantic import ValidationError

from app.schemas.order_parser import ExtractedLineItem, GeminiExtractionPayload
from app.services.sku_matcher import UNIT_ALTERNATION

MAX_QUANTITY = 10_000

_BULLET_RE = re.compile(r"^\s*(?:[-*•·▪◦‣>]+|\(?\d{1,2}[.)])\s+")
_NOTES_RE = re.compile(r"\(([^()]{1,200})\)")

_PACK = (
    r"bags?|box(?:es)?|cases?|packs?|packets?|tins?|bottles?|sacks?|cartons?|units?|pcs|"
    r"pieces?|jars?|tubs?|crates?|blocks?|rolls?|pallets?"
)
# Same vocabulary the matcher canonicalizes ("gm", "milliliter", "pounds", ...), so a size such as
# "500 gm" or "600 milliliter" is never mistaken for the order multiplier.
_MEASURE_UNITS = UNIT_ALTERNATION
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
}  # fmt: skip
_NUM_WORD_ALT = "|".join(_NUMBER_WORDS)
# Conjunctions split lines only when a new quantity follows: "2x arabica and three tampers".
_SPLIT_RE = re.compile(
    r"\r?\n|;|\||,(?!\d)|\s+(?:and|plus|&|\+)\s+"
    rf"(?=(?:\d|(?:an?|half|dozen|{_NUM_WORD_ALT})\b))",
    re.I,
)
_Q = r"\d{1,6}"
_TIMES = r"(?:x|\u00d7|\*)(?![a-z])"

# Ordered from most to least explicit.
_QTY_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "2x 500g arabica", "3 * oat milk", "2 x tamper"
    re.compile(
        rf"^(?P<q>{_Q})\s*{_TIMES}\s*(?:(?P<pack>{_PACK})\s+)?(?:of\s+)?(?P<rest>.+)$", re.I
    ),
    # "tamper 58mm x 1", "beans x3", "beans * 2"
    re.compile(rf"^(?P<rest>.+?)\s*{_TIMES}\s*(?P<q>{_Q})\s*(?P<pack>{_PACK})?$", re.I),
    # "yeast - 6", "yeast: 6", "yeast qty 6", "yeast quantity: 6 bags"
    re.compile(
        rf"^(?P<rest>.+?)\s*(?:\s-|:|=|\bqty\b:?|\bquantity\b:?)\s*(?P<q>{_Q})\s*(?P<pack>{_PACK})?$",
        re.I,
    ),
    # "arabica 500g 4 bags"
    re.compile(rf"^(?P<rest>.+?)\s+(?P<q>{_Q})\s*(?P<pack>{_PACK})$", re.I),
    # "4 bags matcha 250g", "3 oat milk", "two cases of oat milk" (but not "500 g beans")
    re.compile(
        rf"^(?P<q>{_Q}|{_NUM_WORD_ALT})\s+(?!(?:{_MEASURE_UNITS})\b)"
        rf"(?:(?P<pack>{_PACK})\s+)?(?:of\s+)?(?P<rest>.+)$",
        re.I,
    ),
    # "a case of oat milk", "an box of straws"
    re.compile(rf"^(?P<q>an?)\s+(?P<pack>{_PACK})\s+(?:of\s+)?(?P<rest>.+)$", re.I),
)
_DOZEN_RE = re.compile(
    r"^(?:an?\s+)?(?P<half>half\s+(?:an?\s+)?)?dozen\s+(?:of\s+)?(?P<rest>.+)$", re.I
)

_GREETING_RE = re.compile(
    r"^(?:hi|hello|hey|dear|good\s+(?:morning|afternoon|evening)|thanks|thank\s+you|thx|"
    r"cheers|regards|best|kind\s+regards|sincerely|many\s+thanks)\b",
    re.I,
)
_NOTE_RE = re.compile(
    r"^(?:notes?|delivery|deliver|ship|shipping|po|ref|reference)\b\s*[:#-]?\s*(?P<note>.*)$",
    re.I,
)
_LEADING_FILLER = frozenset(
    [
        "please",
        "pls",
        "kindly",
        "also",
        "and",
        "plus",
        "then",
        "we",
        "i",
        "i'd",
        "we'd",
        "would",
        "like",
        "need",
        "want",
        "send",
        "order",
        "get",
        "add",
        "could",
        "can",
        "you",
        "us",
        "me",
        "to",
        "the",
        "following",
        "some",
    ]
)
_TRAILING_FILLER_RE = re.compile(r"(?:\s+(?:thanks|thank\s+you|thx|please|pls|asap))+\W*$", re.I)


def _strip_leading_filler(text: str) -> str:
    words = text.split()
    while words and words[0].lower().strip(",.!") in _LEADING_FILLER:
        words.pop(0)
    return " ".join(words)


def _parse_quantity(fragment: str) -> tuple[int, str, str | None]:
    """Return ``(quantity, rest_of_text, packaging_hint)``; quantity defaults to 1."""
    dozen = _DOZEN_RE.match(fragment)
    if dozen:
        return (6 if dozen.group("half") else 12), dozen.group("rest"), None
    for pattern in _QTY_PATTERNS:
        m = pattern.match(fragment)
        if not m:
            continue
        raw_q = m.group("q").lower()
        if raw_q.isdigit():
            qty = int(raw_q)
        elif raw_q in ("a", "an"):
            qty = 1
        else:
            qty = _NUMBER_WORDS[raw_q]
        return qty, m.group("rest"), m.group("pack")
    return 1, fragment, None


def _clean_query(text: str) -> str:
    text = re.sub(r"^\s*of\s+", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" \t-:,.;!?*")
    return text[:500]


def parse_text_deterministic_fallback(text: str) -> GeminiExtractionPayload:
    items: list[ExtractedLineItem] = []
    unrecognized: list[str] = []
    notes: list[str] = []

    for raw in _SPLIT_RE.split(text):
        fragment = (raw or "").strip()
        if not fragment:
            continue
        line = _BULLET_RE.sub("", fragment).strip()
        if not line:
            continue

        note_match = _NOTE_RE.match(line)
        if note_match:
            if note_match.group("note").strip():
                notes.append(note_match.group("note").strip())
            continue
        has_digit = any(ch.isdigit() for ch in line)
        if not has_digit and (_GREETING_RE.match(line) or line.endswith(":")):
            continue  # salutation / header, not an order line

        line_notes = [n.strip() for n in _NOTES_RE.findall(line)]
        line = _NOTES_RE.sub(" ", line)
        line = _TRAILING_FILLER_RE.sub("", _strip_leading_filler(line)).strip()

        quantity, rest, pack = _parse_quantity(line)
        query = _clean_query(rest)
        if not any(ch.isalpha() for ch in query):
            unrecognized.append(fragment[:1000])
            continue
        if not 1 <= quantity <= MAX_QUANTITY:
            unrecognized.append(fragment[:1000])
            continue
        try:
            items.append(
                ExtractedLineItem(
                    raw_line_text=fragment[:1000],
                    search_query=query,
                    quantity=quantity,
                    unit_hint=pack.lower() if pack else None,
                    notes="; ".join(line_notes)[:500] or None,
                )
            )
        except ValidationError:
            unrecognized.append(fragment[:1000])

    return GeminiExtractionPayload(
        customer_note="; ".join(notes)[:2000] or None,
        items=items,
        unrecognized_fragments=unrecognized,
    )
