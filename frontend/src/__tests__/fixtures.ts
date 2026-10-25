import { vi } from "vitest";

import type { ApiClient } from "../api/client";
import type {
  Candidate,
  HealthResponse,
  LineItemPreview,
  MatchPreviewResponse,
  OrderDetail,
  OrderStatus,
  ParsedOrderPreviewResponse,
  PayoutStatus,
  VendorSummary,
} from "../api/types";

export const ROASTERS: VendorSummary = {
  id: 1,
  name: "Summit Peak Roasters",
  paypal_merchant_email: "vendor-roasters@sandbox.paypal.com",
  commission_rate_bps: 500,
};
export const EQUIPMENT: VendorSummary = {
  id: 2,
  name: "BaristaPro Equipment Co.",
  paypal_merchant_email: "vendor-equipment@sandbox.paypal.com",
  commission_rate_bps: 800,
};

export function candidate(overrides: Partial<Candidate> & Pick<Candidate, "sku_code">): Candidate {
  return {
    sku_id: 1,
    name: "Colombian Arabica Whole Bean Coffee",
    unit_of_measure: "500g bag",
    unit_price_cents: 1495,
    currency: "USD",
    stock_quantity: 200,
    confidence_score: 0.86,
    match_reasoning: "fuzzy=0.90 via alias 'arabica beans'",
    vendor: ROASTERS,
    ...overrides,
  };
}

const ARABICA_500 = candidate({ sku_code: "SPR-COF-ARB-500G" });
const ARABICA_1KG = candidate({
  sku_id: 2,
  sku_code: "SPR-COF-ARB-1KG",
  unit_of_measure: "1kg bag",
  unit_price_cents: 2790,
  confidence_score: 0.84,
});
const TAMPER = candidate({
  sku_id: 9,
  sku_code: "BPE-TMP-58MM",
  name: "Stainless Steel Espresso Tamper 58mm",
  unit_of_measure: "unit",
  unit_price_cents: 3999,
  confidence_score: 0.65,
  vendor: EQUIPMENT,
});

/** 4 x arabica 500g (green) + 2 x tamper (amber) + one unmatched fragment (red). */
export function samplePreview(): MatchPreviewResponse {
  const lines: LineItemPreview[] = [
    {
      line_index: 0,
      query: "arabica beans half kg",
      normalized_query: "arabica beans 500g",
      quantity: 4,
      match_status: "matched",
      is_ambiguous: false,
      is_pinned: false,
      matched_sku: ARABICA_500,
      candidates: [ARABICA_500, ARABICA_1KG],
      stock_status: "in_stock",
      available_quantity: 200,
      is_billable: true,
      issues: [],
      unit_price_cents: 1495,
      subtotal_cents: 5980,
      commission_rate_bps: 500,
      platform_fee_cents: 299,
      net_vendor_payout_cents: 5681,
    },
    {
      line_index: 1,
      query: "espresso tamper 58mm",
      normalized_query: "espresso tamper 58mm",
      quantity: 2,
      match_status: "matched",
      is_ambiguous: false,
      is_pinned: false,
      matched_sku: TAMPER,
      candidates: [TAMPER],
      stock_status: "in_stock",
      available_quantity: 45,
      is_billable: true,
      issues: [],
      unit_price_cents: 3999,
      subtotal_cents: 7998,
      commission_rate_bps: 800,
      platform_fee_cents: 640,
      net_vendor_payout_cents: 7358,
    },
    {
      line_index: 2,
      query: "unicorn saddles",
      normalized_query: "unicorn saddles",
      quantity: 3,
      match_status: "unmatched",
      is_ambiguous: false,
      is_pinned: false,
      matched_sku: null,
      candidates: [{ ...TAMPER, confidence_score: 0.2 }],
      stock_status: "not_applicable",
      available_quantity: null,
      is_billable: false,
      issues: ["No catalog SKU matched this item with sufficient confidence."],
      unit_price_cents: 0,
      subtotal_cents: 0,
      commission_rate_bps: 0,
      platform_fee_cents: 0,
      net_vendor_payout_cents: 0,
    },
  ];
  return {
    currency: "USD",
    all_lines_billable: false,
    unresolved_line_indexes: [2],
    lines,
    vendor_payouts: [
      {
        vendor_id: 1,
        vendor_name: ROASTERS.name,
        paypal_merchant_email: ROASTERS.paypal_merchant_email,
        commission_rate_bps: 500,
        line_count: 1,
        subtotal_cents: 5980,
        platform_fee_cents: 299,
        net_vendor_payout_cents: 5681,
      },
      {
        vendor_id: 2,
        vendor_name: EQUIPMENT.name,
        paypal_merchant_email: EQUIPMENT.paypal_merchant_email,
        commission_rate_bps: 800,
        line_count: 1,
        subtotal_cents: 7998,
        platform_fee_cents: 640,
        net_vendor_payout_cents: 7358,
      },
    ],
    totals: { subtotal_cents: 13978, platform_fee_cents: 939, net_vendor_payout_cents: 13039 },
  };
}

export function sampleParse(preview = samplePreview()): ParsedOrderPreviewResponse {
  return {
    parser_mode: "deterministic_fallback",
    model_used: "deterministic-regex-v1",
    customer_note: "Back door delivery",
    extracted_items: preview.lines.map((l) => ({
      raw_line_text: l.query,
      search_query: l.query,
      quantity: l.quantity,
      unit_hint: null,
      notes: null,
    })),
    unrecognized_fragments: ["???"],
    match_preview: preview,
  };
}

export function sampleOrder(
  status: OrderStatus,
  payoutStatuses: [PayoutStatus, PayoutStatus] = ["PENDING", "PENDING"],
): OrderDetail {
  const captured = status !== "DRAFT" && status !== "PAYPAL_ORDER_CREATED";
  const batch = (i: number) =>
    payoutStatuses[i] === "PENDING" ? null : `SIM-PAYOUT-${i === 0 ? "AAAA" : "BBBB"}`;
  return {
    public_order_id: "ORD-1A2B3C4D5E6F",
    status,
    currency: "USD",
    gross_subtotal_cents: 13978,
    total_platform_fee_cents: 939,
    total_vendor_payout_cents: 13039,
    paypal_mode: "sandbox_simulation",
    paypal_order_id: "SIM-ORDER-0A43C924A6B3398D0",
    paypal_capture_id: captured ? "SIM-CAPTURE-734D392668783F50" : null,
    created_at: "2026-10-25T10:00:00Z",
    updated_at: "2026-10-25T10:00:01Z",
    captured_at: captured ? "2026-10-25T10:00:01Z" : null,
    idempotency_key: "cp-test",
    customer_note: "Back door delivery",
    parser_mode: "deterministic_fallback",
    approval_url: "https://www.sandbox.paypal.com/checkoutnow?token=SIM-ORDER-0A43C924A6B3398D0",
    paypal_capture_status: captured ? "COMPLETED" : null,
    stock_reserved: captured,
    payout_hold_reason: null,
    last_error: null,
    line_items: [
      {
        id: 1, sku_id: 1, vendor_id: 1, sku_code: "SPR-COF-ARB-500G",
        product_name: ARABICA_500.name, raw_query: "arabica beans half kg", quantity: 4,
        unit_price_cents: 1495, subtotal_cents: 5980, commission_rate_bps: 500,
        platform_fee_cents: 299, net_vendor_payout_cents: 5681,
      },
      {
        id: 2, sku_id: 9, vendor_id: 2, sku_code: "BPE-TMP-58MM",
        product_name: TAMPER.name, raw_query: "espresso tamper 58mm", quantity: 2,
        unit_price_cents: 3999, subtotal_cents: 7998, commission_rate_bps: 800,
        platform_fee_cents: 640, net_vendor_payout_cents: 7358,
      },
    ],
    payouts: [ROASTERS, EQUIPMENT].map((v, i) => ({
      id: i + 1,
      vendor_id: v.id,
      vendor_name: v.name,
      receiver_paypal_email: v.paypal_merchant_email,
      currency: "USD",
      gross_subtotal_cents: i === 0 ? 5980 : 7998,
      platform_fee_cents: i === 0 ? 299 : 640,
      payout_amount_cents: i === 0 ? 5681 : 7358,
      sender_batch_id: `ORD-1A2B3C4D5E6F-V${v.id}`,
      sender_item_id: `ORD-1A2B3C4D5E6F-V${v.id}-ITEM`,
      paypal_payout_batch_id: batch(i),
      paypal_payout_item_id: null,
      status: payoutStatuses[i]!,
      attempt_count: payoutStatuses[i] === "PENDING" ? 0 : 1,
      error_message: payoutStatuses[i] === "FAILED" ? "Ambiguous: PayPal returned HTTP 502." : null,
      created_at: "2026-10-25T10:00:00Z",
      updated_at: "2026-10-25T10:00:01Z",
    })),
  };
}

export const sampleHealth: HealthResponse = {
  status: "ok",
  app: "CatalogPay Agent",
  environment: "local",
  database: { connected: true, dialect: "sqlite", error: null },
  active_sku_count: 25,
  integrations: {
    parser_mode: "deterministic_fallback",
    parser_model: "regex",
    paypal_mode: "sandbox_simulation",
    paypal_env: "sandbox",
  },
};

/** A fully mocked ApiClient; every method is a vi.fn with a sensible default. */
export function fakeClient(overrides: Partial<Record<keyof ApiClient, unknown>> = {}): ApiClient {
  const client = {
    health: vi.fn().mockResolvedValue(sampleHealth),
    listSkus: vi.fn().mockResolvedValue({ total: 0, limit: 200, offset: 0, items: [] }),
    matchPreview: vi.fn().mockResolvedValue(samplePreview()),
    parseText: vi.fn().mockResolvedValue(sampleParse()),
    parseMedia: vi.fn().mockResolvedValue(sampleParse()),
    createOrder: vi.fn().mockResolvedValue(sampleOrder("PAYPAL_ORDER_CREATED")),
    listOrders: vi.fn().mockResolvedValue({ total: 0, limit: 100, offset: 0, items: [] }),
    getOrder: vi.fn().mockResolvedValue(sampleOrder("PAYPAL_ORDER_CREATED")),
    captureOrder: vi.fn().mockResolvedValue(sampleOrder("COMPLETED", ["SUCCESS", "SUCCESS"])),
    retryPayouts: vi.fn().mockResolvedValue(sampleOrder("COMPLETED", ["SUCCESS", "SUCCESS"])),
    agentTools: vi.fn().mockResolvedValue({ gemini_function_declarations: [], mcp_tools: [] }),
    ...overrides,
  };
  return client as unknown as ApiClient;
}
