import type { Candidate, LineItemPreview, MatchPreviewResponse } from "../api/types";
import type { WorkItem } from "../hooks/useCommandCenter";

export type LineHealth = "ready" | "review" | "out_of_stock" | "unmatched";
export type Tone = "green" | "amber" | "red";

export const CONFIDENCE_GREEN = 0.8;
export const CONFIDENCE_AMBER = 0.5;

/** One AG Grid row in the SKU Resolution Workbench (data rows and the pinned totals row). */
export interface ResolutionRow {
  id: string;
  index: number;
  isTotal: boolean;
  query: string;
  normalizedQuery: string;
  quantity: number | null;
  line: LineItemPreview | null;
  skuCode: string | null;
  skuName: string | null;
  confidence: number | null;
  vendorName: string | null;
  commissionBps: number | null;
  unitPriceCents: number | null;
  subtotalCents: number;
  platformFeeCents: number;
  netPayoutCents: number;
  health: LineHealth | null;
}

export function lineHealth(line: LineItemPreview): LineHealth {
  if (line.match_status === "unmatched") return "unmatched";
  if (line.stock_status === "insufficient_stock") return "out_of_stock";
  if (!line.is_billable) return "review";
  return "ready";
}

/** The candidate the line currently resolves to, or the matcher's best guess if unresolved. */
export function displayedCandidate(line: LineItemPreview): Candidate | null {
  return line.matched_sku ?? line.candidates[0] ?? null;
}

export function confidenceOf(line: LineItemPreview): number {
  return displayedCandidate(line)?.confidence_score ?? 0;
}

/** Green >= 0.80, amber 0.50-0.79, red < 0.50 or anything unmatched / out of stock. */
export function confidenceTone(line: LineItemPreview): Tone {
  const health = lineHealth(line);
  if (health === "unmatched" || health === "out_of_stock") return "red";
  const score = confidenceOf(line);
  if (score >= CONFIDENCE_GREEN) return "green";
  if (score >= CONFIDENCE_AMBER) return "amber";
  return "red";
}

/**
 * Grid rows. Quantities come from `items` (the operator's source of truth) when given, so an
 * edit in flight or a basket restored after a failed re-price is what the cell shows.
 */
export function toResolutionRows(
  preview: MatchPreviewResponse | null,
  items?: readonly WorkItem[],
): ResolutionRow[] {
  if (!preview) return [];
  return preview.lines.map((line) => {
    const shown = displayedCandidate(line);
    return {
      id: `line-${line.line_index}`,
      index: line.line_index,
      isTotal: false,
      query: line.query,
      normalizedQuery: line.normalized_query,
      quantity: items?.[line.line_index]?.quantity ?? line.quantity,
      line,
      skuCode: line.matched_sku?.sku_code ?? null,
      skuName: shown?.name ?? null,
      confidence: shown ? shown.confidence_score : null,
      vendorName: shown?.vendor.name ?? null,
      commissionBps: shown?.vendor.commission_rate_bps ?? null,
      unitPriceCents: line.is_billable ? line.unit_price_cents : (shown?.unit_price_cents ?? null),
      subtotalCents: line.subtotal_cents,
      platformFeeCents: line.platform_fee_cents,
      netPayoutCents: line.net_vendor_payout_cents,
      health: lineHealth(line),
    };
  });
}

export function totalsRow(preview: MatchPreviewResponse | null): ResolutionRow[] {
  if (!preview) return [];
  const billable = preview.lines.filter((l) => l.is_billable).length;
  return [
    {
      id: "totals",
      index: -1,
      isTotal: true,
      query: `Order totals · ${billable}/${preview.lines.length} lines billable`,
      normalizedQuery: "",
      quantity: null,
      line: null,
      skuCode: null,
      skuName: null,
      confidence: null,
      vendorName: `${preview.vendor_payouts.length} vendors`,
      commissionBps: null,
      unitPriceCents: null,
      subtotalCents: preview.totals.subtotal_cents,
      platformFeeCents: preview.totals.platform_fee_cents,
      netPayoutCents: preview.totals.net_vendor_payout_cents,
      health: null,
    },
  ];
}
