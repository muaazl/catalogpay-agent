"""Hybrid SKU Retrieval Engine.

Maps a messy buyer phrase ("arabica beans half kg", "oat milk barista case") to catalog SKUs
with no network calls, so it is fast and fully deterministic. Three signals are blended:

1. Lexical fuzzy score (rapidfuzz token-set + partial ratio) over name, aliases, SKU code,
   category and unit of measure.
2. Character n-gram TF-IDF cosine similarity (numpy), which is robust to typos and word order.
3. Unit agreement: quantities in the query ("0.5kg", "half kilo", "58mm") are normalized to
   canonical units and compared with the SKU's own size.
"""

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from fractions import Fraction

import numpy as np
from rapidfuzz import fuzz

from app.models.catalog import ProductSKU

# --------------------------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------------------------

# unit alias -> (canonical unit, multiplier into canonical unit)
_KG, _G, _LB = ("g", Fraction(1000)), ("g", Fraction(1)), ("g", Fraction(45359237, 100000))
_L, _ML, _OZ = ("ml", Fraction(1000)), ("ml", Fraction(1)), ("oz", Fraction(1))
_MM, _CM = ("mm", Fraction(1)), ("mm", Fraction(10))
_UNIT_TABLE: dict[str, tuple[str, Fraction]] = {
    **dict.fromkeys(("kg", "kgs", "kilo", "kilos", "kilogram", "kilograms"), _KG),
    **dict.fromkeys(("g", "gr", "grs", "gram", "grams", "gm", "gms"), _G),
    **dict.fromkeys(("lb", "lbs", "pound", "pounds"), _LB),
    **dict.fromkeys(("l", "lt", "ltr", "litre", "litres", "liter", "liters"), _L),
    **dict.fromkeys(("ml", "millilitre", "millilitres", "milliliter", "milliliters"), _ML),
    **dict.fromkeys(("oz", "ounce", "ounces"), _OZ),
    **dict.fromkeys(("mm", "millimeter", "millimeters", "millimetre"), _MM),
    **dict.fromkeys(("cm", "centimeter", "centimeters", "centimetre"), _CM),
}
_UNIT_ALTERNATION = "|".join(sorted(_UNIT_TABLE, key=len, reverse=True))
_QUANTITY_RE = re.compile(rf"(?<![a-z])(\d+(?:\.\d+)?)\s*({_UNIT_ALTERNATION})(?![a-z])")
_MEASURE_TOKEN_RE = re.compile(r"^\d+(?:g|ml|oz|mm)$")

_WORD_FRACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bhalf\s+(?:a\s+)?dozen\b"), "6"),
    (re.compile(r"\bdozen\b"), "12"),
    (re.compile(r"\b(?:half|1/2|½)\s*(?:a\s+)?(?=[a-z])"), "0.5 "),
    (re.compile(r"\b(?:quarter|1/4|¼)\s*(?:of\s+)?(?:a\s+)?(?=[a-z])"), "0.25 "),
)

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "of",
        "for",
        "and",
        "with",
        "please",
        "pls",
        "plz",
        "some",
        "need",
        "want",
        "x",
        "pcs",
        "pc",
        "qty",
        "approx",
        "about",
    }
)


def _format_amount(amount: Fraction) -> str:
    rounded = round(amount)
    if abs(amount - rounded) < Fraction(1, 100):
        return str(rounded)
    return f"{float(amount):.1f}".rstrip("0").rstrip(".")


def _canonicalize_quantity(match: re.Match[str]) -> str:
    unit, multiplier = _UNIT_TABLE[match.group(2)]
    return f" {_format_amount(Fraction(match.group(1)) * multiplier)}{unit} "


def normalize_text(text: str) -> str:
    """Lowercase, strip accents/punctuation, canonicalize units (0.5 kg / half kilo -> 500g)."""
    # Vulgar fractions first: NFKC rewrites them using U+2044 FRACTION SLASH.
    text = text.replace("½", " 1/2").replace("¼", " 1/4")
    text = unicodedata.normalize("NFKC", text).replace("\u2044", "/")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub(r"(\d),(\d)", r"\1.\2", text)  # European decimals: 0,5kg
    for pattern, replacement in _WORD_FRACTIONS:
        text = pattern.sub(replacement, text)
    text = re.sub(r"[^a-z0-9./%]+", " ", text)
    text = _QUANTITY_RE.sub(_canonicalize_quantity, text)
    text = re.sub(r"(?<!\d)\.|\.(?!\d)|/", " ", text)
    tokens = [t for t in text.split() if t not in _STOPWORDS]
    return " ".join(tokens)


def extract_measures(normalized: str) -> frozenset[str]:
    return frozenset(t for t in normalized.split() if _MEASURE_TOKEN_RE.match(t))


def _measure_dimension(token: str) -> str:
    return re.sub(r"^\d+", "", token)


# --------------------------------------------------------------------------------------------
# Character n-gram TF-IDF (numpy)
# --------------------------------------------------------------------------------------------


def _char_ngrams(text: str, n_min: int = 3, n_max: int = 4) -> Counter[str]:
    grams: Counter[str] = Counter()
    for word in text.split():
        padded = f" {word} "
        for n in range(n_min, n_max + 1):
            for i in range(max(len(padded) - n + 1, 0)):
                grams[padded[i : i + n]] += 1
    return grams


class CharNgramTfidf:
    """Minimal TF-IDF vectorizer over word-boundary character n-grams with L2-normalized rows."""

    def __init__(self, documents: Sequence[str]) -> None:
        counts = [_char_ngrams(doc) for doc in documents]
        vocab = sorted({gram for c in counts for gram in c})
        self._index = {gram: i for i, gram in enumerate(vocab)}
        n_docs = len(documents)
        df = np.zeros(len(vocab), dtype=np.float64)
        for c in counts:
            for gram in c:
                df[self._index[gram]] += 1
        self._idf = np.log((1 + n_docs) / (1 + df)) + 1.0
        self.matrix = (
            np.vstack([self._vectorize(c) for c in counts]) if counts else np.zeros((0, 0))
        )

    def _vectorize(self, grams: Counter[str]) -> np.ndarray:
        vec = np.zeros(len(self._index), dtype=np.float64)
        for gram, count in grams.items():
            idx = self._index.get(gram)
            if idx is not None:
                vec[idx] = (1.0 + math.log(count)) * self._idf[idx]
        norm = np.linalg.norm(vec)
        return vec / norm if norm > 0 else vec

    def cosine(self, query: str) -> np.ndarray:
        if self.matrix.size == 0:
            return np.zeros(0)
        return self.matrix @ self._vectorize(_char_ngrams(query))


# --------------------------------------------------------------------------------------------
# Domain types
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VendorInfo:
    id: int
    name: str
    paypal_merchant_email: str
    commission_rate_bps: int


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """ORM-free snapshot of an active SKU used by the matcher."""

    sku_id: int
    sku_code: str
    name: str
    description: str
    category: str
    unit_of_measure: str
    aliases: tuple[str, ...]
    unit_price_cents: int
    currency: str
    stock_quantity: int
    vendor: VendorInfo

    @classmethod
    def from_orm(cls, sku: ProductSKU) -> "CatalogEntry":
        v = sku.vendor
        return cls(
            sku_id=sku.id,
            sku_code=sku.sku_code,
            name=sku.name,
            description=sku.description,
            category=sku.category,
            unit_of_measure=sku.unit_of_measure,
            aliases=tuple(sku.aliases_json or ()),
            unit_price_cents=sku.unit_price_cents,
            currency=sku.currency,
            stock_quantity=sku.stock_quantity,
            vendor=VendorInfo(v.id, v.name, v.paypal_merchant_email, v.commission_rate_bps),
        )


class MatchStatus(StrEnum):
    MATCHED = "matched"
    LOW_CONFIDENCE = "low_confidence"
    UNMATCHED = "unmatched"


@dataclass(frozen=True, slots=True)
class MatchCandidate:
    entry: CatalogEntry
    confidence_score: float
    fuzzy_score: float
    vector_score: float
    unit_score: float | None
    match_reasoning: str


@dataclass(frozen=True, slots=True)
class MatchResult:
    query: str
    normalized_query: str
    status: MatchStatus
    candidates: tuple[MatchCandidate, ...] = field(default_factory=tuple)
    is_ambiguous: bool = False

    @property
    def best(self) -> MatchCandidate | None:
        return self.candidates[0] if self.candidates else None


@dataclass(frozen=True, slots=True)
class _Field:
    label: str
    text: str
    weight: float


# --------------------------------------------------------------------------------------------
# Matcher
# --------------------------------------------------------------------------------------------


class HybridSKUMatcher:
    FUZZY_WEIGHT = 0.55
    VECTOR_WEIGHT = 0.35
    UNIT_WEIGHT = 0.10
    # Mismatched explicit sizes (asked for 500g, SKU is 1kg) cost more than the unit weight alone.
    UNIT_MISMATCH_PENALTY = 0.08
    # If the runner-up is this close, the match needs human/agent clarification.
    AMBIGUITY_MARGIN = 0.05

    def __init__(
        self,
        entries: Iterable[CatalogEntry],
        *,
        match_threshold: float = 0.62,
        low_confidence_threshold: float = 0.40,
    ) -> None:
        if low_confidence_threshold > match_threshold:
            raise ValueError("low_confidence_threshold must not exceed match_threshold")
        self.entries: tuple[CatalogEntry, ...] = tuple(entries)
        self.match_threshold = match_threshold
        self.low_confidence_threshold = low_confidence_threshold

        self._fields: list[list[_Field]] = [self._fields_for(e) for e in self.entries]
        self._entry_measures: list[frozenset[str]] = [
            extract_measures(normalize_text(f"{e.name} {e.unit_of_measure} {e.sku_code}"))
            for e in self.entries
        ]

        # One TF-IDF row per (entry, field) so short queries are compared to short fields.
        row_texts: list[str] = []
        self._row_owner: list[int] = []
        self._row_label: list[str] = []
        for i, fields in enumerate(self._fields):
            for f in fields:
                row_texts.append(f.text)
                self._row_owner.append(i)
                self._row_label.append(f.label)
        self._tfidf = CharNgramTfidf(row_texts)

    @classmethod
    def from_skus(cls, skus: Iterable[ProductSKU], **kwargs: float) -> "HybridSKUMatcher":
        active = (s for s in skus if s.is_active and s.vendor.is_active)
        return cls((CatalogEntry.from_orm(s) for s in active), **kwargs)

    @staticmethod
    def _fields_for(entry: CatalogEntry) -> list[_Field]:
        name = normalize_text(entry.name)
        uom = normalize_text(entry.unit_of_measure)
        fields = [
            _Field("name", name, 1.0),
            _Field("name+size", f"{name} {uom}".strip(), 1.0),
            _Field("sku_code", normalize_text(entry.sku_code.replace("-", " ")), 0.95),
            _Field("category", normalize_text(entry.category), 0.55),
        ]
        fields.extend(
            _Field(f"alias '{alias}'", normalize_text(alias), 0.97) for alias in entry.aliases
        )
        return [f for f in fields if f.text]

    @staticmethod
    def _token_coverage(query_tokens: Sequence[str], text_tokens: Sequence[str]) -> float:
        """Mean best per-token similarity of query words against field words (typo tolerant).

        Unlike char-level partial ratios, an unrelated query word contributes ~0, so "gold watch"
        does not ride on the shared substring in "matcha".
        """
        if not query_tokens or not text_tokens:
            return 0.0
        total = 0.0
        for qt in query_tokens:
            best = max(fuzz.ratio(qt, tt) for tt in text_tokens)
            total += best if best >= 75 else 0.0
        return total / (100.0 * len(query_tokens))

    @classmethod
    def _fuzzy(cls, query: str, text: str) -> float:
        token_set = fuzz.token_set_ratio(query, text) / 100.0
        token_sort = fuzz.token_sort_ratio(query, text) / 100.0
        coverage = cls._token_coverage(query.split(), text.split())
        return 0.35 * token_set + 0.25 * token_sort + 0.40 * coverage

    def _unit_score(self, query_measures: frozenset[str], entry_idx: int) -> float | None:
        if not query_measures:
            return None
        entry_measures = self._entry_measures[entry_idx]
        if query_measures & entry_measures:
            return 1.0
        query_dims = {_measure_dimension(m) for m in query_measures}
        entry_dims = {_measure_dimension(m) for m in entry_measures}
        if query_dims & entry_dims:
            return 0.0  # same dimension, different size: explicit conflict
        return 0.5  # SKU has no comparable size: neutral

    def _status_for(self, confidence: float) -> MatchStatus:
        if confidence >= self.match_threshold:
            return MatchStatus.MATCHED
        if confidence >= self.low_confidence_threshold:
            return MatchStatus.LOW_CONFIDENCE
        return MatchStatus.UNMATCHED

    def match(self, query: str, top_k: int = 3) -> MatchResult:
        if top_k < 1:
            raise ValueError("top_k must be >= 1")
        normalized = normalize_text(query)
        if not normalized or not self.entries:
            return MatchResult(query, normalized, MatchStatus.UNMATCHED)

        query_measures = extract_measures(normalized)
        row_cosines = self._tfidf.cosine(normalized)
        vector_scores = np.zeros(len(self.entries), dtype=np.float64)
        vector_label = [""] * len(self.entries)
        for row, owner in enumerate(self._row_owner):
            if row_cosines[row] > vector_scores[owner]:
                vector_scores[owner] = row_cosines[row]
                vector_label[owner] = self._row_label[row]

        scored: list[MatchCandidate] = []
        for i, entry in enumerate(self.entries):
            fuzzy_best, fuzzy_label = 0.0, ""
            for f in self._fields[i]:
                score = self._fuzzy(normalized, f.text) * f.weight
                if score > fuzzy_best:
                    fuzzy_best, fuzzy_label = score, f.label
            vector = float(np.clip(vector_scores[i], 0.0, 1.0))
            unit = self._unit_score(query_measures, i)

            if unit is None:
                total_weight = self.FUZZY_WEIGHT + self.VECTOR_WEIGHT
                confidence = (self.FUZZY_WEIGHT * fuzzy_best + self.VECTOR_WEIGHT * vector) / (
                    total_weight
                )
            else:
                confidence = (
                    self.FUZZY_WEIGHT * fuzzy_best
                    + self.VECTOR_WEIGHT * vector
                    + self.UNIT_WEIGHT * unit
                )
                if unit == 0.0:
                    confidence -= self.UNIT_MISMATCH_PENALTY
            confidence = round(min(max(confidence, 0.0), 1.0), 4)

            reasoning = [
                f"fuzzy={fuzzy_best:.2f} via {fuzzy_label}",
                f"char-ngram cosine={vector:.2f} via {vector_label[i] or 'n/a'}",
            ]
            if unit is not None:
                wanted = ", ".join(sorted(query_measures))
                verdict = {1.0: "matches", 0.0: "conflicts with", 0.5: "not comparable to"}[unit]
                reasoning.append(f"size {wanted} {verdict} '{entry.unit_of_measure}' / name")
            scored.append(
                MatchCandidate(
                    entry=entry,
                    confidence_score=confidence,
                    fuzzy_score=round(fuzzy_best, 4),
                    vector_score=round(vector, 4),
                    unit_score=unit,
                    match_reasoning="; ".join(reasoning),
                )
            )

        # Deterministic ordering: confidence desc, then sku_code for ties.
        scored.sort(key=lambda c: (-c.confidence_score, c.entry.sku_code))
        best = scored[0]
        status = self._status_for(best.confidence_score)
        is_ambiguous = (
            len(scored) > 1
            and best.confidence_score - scored[1].confidence_score < self.AMBIGUITY_MARGIN
        )
        if is_ambiguous and status is MatchStatus.MATCHED:
            status = MatchStatus.LOW_CONFIDENCE
        return MatchResult(query, normalized, status, tuple(scored[:top_k]), is_ambiguous)
