"""Builds a priced, stock-checked, vendor-split preview from raw buyer line items."""

from collections import defaultdict
from collections.abc import Sequence

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
from app.services.money import line_subtotal_cents, split_fee
from app.services.sku_matcher import HybridSKUMatcher, MatchCandidate, MatchStatus


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


def build_match_preview(
    matcher: HybridSKUMatcher,
    items: Sequence[MatchPreviewItem],
    *,
    currency: str,
    top_k: int = 3,
) -> MatchPreviewResponse:
    lines: list[LineItemPreview] = []
    reserved: defaultdict[int, int] = defaultdict(int)  # sku_id -> qty claimed by earlier lines
    payouts: dict[int, VendorPayoutPreview] = {}

    for index, item in enumerate(items):
        result = matcher.match(item.query, top_k=top_k)
        candidates = [_candidate_out(c) for c in result.candidates]
        issues: list[str] = []
        best = result.best if result.status is MatchStatus.MATCHED else None
        stock_status = StockStatus.NOT_APPLICABLE
        available: int | None = None
        unit_price = subtotal = fee = net = bps = 0

        if result.status is MatchStatus.UNMATCHED:
            issues.append("No catalog SKU matched this item with sufficient confidence.")
        elif result.status is MatchStatus.LOW_CONFIDENCE:
            reason = "candidates are too close to call" if result.is_ambiguous else "low score"
            issues.append(f"Match needs buyer confirmation ({reason}).")

        if best is not None:
            entry = best.entry
            available = entry.stock_quantity - reserved[entry.sku_id]
            if entry.currency != currency:
                issues.append(f"SKU priced in {entry.currency}, order currency is {currency}.")
            if item.quantity <= available:
                stock_status = StockStatus.IN_STOCK
            else:
                stock_status = StockStatus.INSUFFICIENT_STOCK
                issues.append(f"Requested {item.quantity}, only {max(available, 0)} available.")

        is_billable = best is not None and not issues
        if is_billable and best is not None:
            entry = best.entry
            reserved[entry.sku_id] += item.quantity
            unit_price = entry.unit_price_cents
            bps = entry.vendor.commission_rate_bps
            split = split_fee(line_subtotal_cents(unit_price, item.quantity), bps)
            subtotal, fee, net = (
                split.subtotal_cents,
                split.platform_fee_cents,
                split.net_vendor_payout_cents,
            )
            v = entry.vendor
            agg = payouts.get(v.id) or VendorPayoutPreview(
                vendor_id=v.id,
                vendor_name=v.name,
                paypal_merchant_email=v.paypal_merchant_email,
                commission_rate_bps=v.commission_rate_bps,
                line_count=0,
                subtotal_cents=0,
                platform_fee_cents=0,
                net_vendor_payout_cents=0,
            )
            payouts[v.id] = agg.model_copy(
                update={
                    "line_count": agg.line_count + 1,
                    "subtotal_cents": agg.subtotal_cents + subtotal,
                    "platform_fee_cents": agg.platform_fee_cents + fee,
                    "net_vendor_payout_cents": agg.net_vendor_payout_cents + net,
                }
            )

        lines.append(
            LineItemPreview(
                line_index=index,
                query=item.query,
                normalized_query=result.normalized_query,
                quantity=item.quantity,
                match_status=result.status,
                is_ambiguous=result.is_ambiguous,
                matched_sku=_candidate_out(best) if best is not None else None,
                candidates=candidates,
                stock_status=stock_status,
                available_quantity=available,
                is_billable=is_billable,
                issues=issues,
                unit_price_cents=unit_price,
                subtotal_cents=subtotal,
                commission_rate_bps=bps,
                platform_fee_cents=fee,
                net_vendor_payout_cents=net,
            )
        )

    vendor_payouts = sorted(payouts.values(), key=lambda p: p.vendor_id)
    totals = PreviewTotals(
        subtotal_cents=sum(p.subtotal_cents for p in vendor_payouts),
        platform_fee_cents=sum(p.platform_fee_cents for p in vendor_payouts),
        net_vendor_payout_cents=sum(p.net_vendor_payout_cents for p in vendor_payouts),
    )
    unresolved = [line.line_index for line in lines if not line.is_billable]
    return MatchPreviewResponse(
        currency=currency,
        all_lines_billable=not unresolved,
        unresolved_line_indexes=unresolved,
        lines=lines,
        vendor_payouts=vendor_payouts,
        totals=totals,
    )
