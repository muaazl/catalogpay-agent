"""Builds a priced, stock-checked, vendor-split preview from raw buyer line items.

Two passes: (1) resolve every line to a SKU and check stock, (2) per vendor, charge the platform
fee once on the vendor subtotal and apportion it to that vendor's lines. Every level (line,
vendor, order) satisfies ``subtotal == platform_fee + net_vendor_payout`` exactly, and the
vendor fee does not depend on how the buyer split their lines.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.schemas.catalog import (
    CandidateOut,
    LineItemPreview,
    MatchPreviewItem,
    MatchPreviewResponse,
    PreviewTotals,
    StockStatus,
    VendorPayoutPreview,
    VendorSummary,
)
from app.services.money import allocate_fee, line_subtotal_cents
from app.services.sku_matcher import CatalogEntry, HybridSKUMatcher, MatchCandidate, MatchStatus


def _candidate_out(candidate: MatchCandidate) -> CandidateOut:
    e = candidate.entry
    return CandidateOut(
        sku_id=e.sku_id,
        sku_code=e.sku_code,
        name=e.name,
        unit_of_measure=e.unit_of_measure,
        unit_price_cents=e.unit_price_cents,
        currency=e.currency,
        stock_quantity=e.stock_quantity,
        confidence_score=candidate.confidence_score,
        match_reasoning=candidate.match_reasoning,
        vendor=VendorSummary(
            id=e.vendor.id,
            name=e.vendor.name,
            paypal_merchant_email=e.vendor.paypal_merchant_email,
            commission_rate_bps=e.vendor.commission_rate_bps,
        ),
    )


@dataclass(slots=True)
class _ResolvedLine:
    index: int
    item: MatchPreviewItem
    normalized_query: str
    status: MatchStatus
    is_ambiguous: bool
    is_pinned: bool
    candidates: list[CandidateOut]
    best: MatchCandidate | None
    stock_status: StockStatus
    available: int | None
    issues: list[str] = field(default_factory=list)
    subtotal_cents: int = 0
    platform_fee_cents: int = 0

    @property
    def entry(self) -> CatalogEntry | None:
        return self.best.entry if self.best is not None else None

    @property
    def is_billable(self) -> bool:
        return self.best is not None and not self.issues


def _resolve(
    matcher: HybridSKUMatcher,
    items: Sequence[MatchPreviewItem],
    currency: str,
    top_k: int,
) -> list[_ResolvedLine]:
    reserved: defaultdict[int, int] = defaultdict(int)  # sku_id -> qty claimed by earlier lines
    lines: list[_ResolvedLine] = []
    for index, item in enumerate(items):
        if item.sku_code:
            result = matcher.match_pinned(item.query, item.sku_code, top_k=top_k)
        else:
            result = matcher.match(item.query, top_k=top_k)
        best = result.best if result.status is MatchStatus.MATCHED else None
        line = _ResolvedLine(
            index=index,
            item=item,
            normalized_query=result.normalized_query,
            status=result.status,
            is_ambiguous=result.is_ambiguous,
            is_pinned=bool(item.sku_code),
            candidates=[_candidate_out(c) for c in result.candidates],
            best=best,
            stock_status=StockStatus.NOT_APPLICABLE,
            available=None,
        )
        if result.status is MatchStatus.UNMATCHED and item.sku_code:
            line.issues.append(f"Pinned SKU {item.sku_code} is not available in the catalog.")
        elif result.status is MatchStatus.UNMATCHED:
            line.issues.append("No catalog SKU matched this item with sufficient confidence.")
        elif result.status is MatchStatus.LOW_CONFIDENCE:
            reason = "candidates are too close to call" if result.is_ambiguous else "low score"
            line.issues.append(f"Match needs buyer confirmation ({reason}).")

        entry = line.entry
        if entry is not None:
            line.available = entry.stock_quantity - reserved[entry.sku_id]
            if entry.currency != currency:
                line.issues.append(f"SKU priced in {entry.currency}, order currency is {currency}.")
            if item.quantity <= line.available:
                line.stock_status = StockStatus.IN_STOCK
            else:
                line.stock_status = StockStatus.INSUFFICIENT_STOCK
                line.issues.append(
                    f"Requested {item.quantity}, only {max(line.available, 0)} available."
                )
            if line.is_billable:
                reserved[entry.sku_id] += item.quantity
                line.subtotal_cents = line_subtotal_cents(entry.unit_price_cents, item.quantity)
        lines.append(line)
    return lines


def build_match_preview(
    matcher: HybridSKUMatcher,
    items: Sequence[MatchPreviewItem],
    *,
    currency: str,
    top_k: int = 3,
) -> MatchPreviewResponse:
    lines = _resolve(matcher, items, currency, top_k)

    by_vendor: dict[int, list[_ResolvedLine]] = defaultdict(list)
    for line in lines:
        if line.is_billable and line.entry is not None:
            by_vendor[line.entry.vendor.id].append(line)

    vendor_payouts: list[VendorPayoutPreview] = []
    for vendor_id in sorted(by_vendor):
        vendor_lines = by_vendor[vendor_id]
        first_entry = vendor_lines[0].entry
        assert first_entry is not None  # only billable lines with an entry are grouped
        vendor = first_entry.vendor
        fees = allocate_fee([ln.subtotal_cents for ln in vendor_lines], vendor.commission_rate_bps)
        for ln, fee in zip(vendor_lines, fees, strict=True):
            ln.platform_fee_cents = fee
        subtotal = sum(ln.subtotal_cents for ln in vendor_lines)
        fee_total = sum(fees)
        vendor_payouts.append(
            VendorPayoutPreview(
                vendor_id=vendor.id,
                vendor_name=vendor.name,
                paypal_merchant_email=vendor.paypal_merchant_email,
                commission_rate_bps=vendor.commission_rate_bps,
                line_count=len(vendor_lines),
                subtotal_cents=subtotal,
                platform_fee_cents=fee_total,
                net_vendor_payout_cents=subtotal - fee_total,
            )
        )

    line_previews = [
        LineItemPreview(
            line_index=ln.index,
            query=ln.item.query,
            normalized_query=ln.normalized_query,
            quantity=ln.item.quantity,
            match_status=ln.status,
            is_ambiguous=ln.is_ambiguous,
            is_pinned=ln.is_pinned,
            matched_sku=_candidate_out(ln.best) if ln.best is not None else None,
            candidates=ln.candidates,
            stock_status=ln.stock_status,
            available_quantity=ln.available,
            is_billable=ln.is_billable,
            issues=ln.issues,
            unit_price_cents=ln.entry.unit_price_cents if ln.is_billable and ln.entry else 0,
            subtotal_cents=ln.subtotal_cents,
            commission_rate_bps=(
                ln.entry.vendor.commission_rate_bps if ln.is_billable and ln.entry else 0
            ),
            platform_fee_cents=ln.platform_fee_cents,
            net_vendor_payout_cents=ln.subtotal_cents - ln.platform_fee_cents,
        )
        for ln in lines
    ]
    totals = PreviewTotals(
        subtotal_cents=sum(p.subtotal_cents for p in vendor_payouts),
        platform_fee_cents=sum(p.platform_fee_cents for p in vendor_payouts),
        net_vendor_payout_cents=sum(p.net_vendor_payout_cents for p in vendor_payouts),
    )
    unresolved = [ln.index for ln in lines if not ln.is_billable]
    return MatchPreviewResponse(
        currency=currency,
        all_lines_billable=not unresolved,
        unresolved_line_indexes=unresolved,
        lines=line_previews,
        vendor_payouts=vendor_payouts,
        totals=totals,
    )
