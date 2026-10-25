// Mirrors the FastAPI schemas in backend/app/schemas. Every *_cents field is an integer.

export type MatchStatus = "matched" | "low_confidence" | "unmatched";
export type StockStatus = "in_stock" | "insufficient_stock" | "not_applicable";
export type ParserMode = "gemini" | "deterministic_fallback";
export type PayPalMode = "live_sandbox" | "sandbox_simulation";

export type OrderStatus =
  | "DRAFT"
  | "PAYPAL_ORDER_CREATED"
  | "CAPTURED"
  | "PAYOUTS_PENDING"
  | "COMPLETED"
  | "PAYOUTS_PARTIAL_FAILED"
  | "CANCELLED";

export type PayoutStatus = "PENDING" | "PROCESSING" | "SUCCESS" | "FAILED";

// --- health & catalog ----------------------------------------------------------------------

export interface HealthResponse {
  status: "ok" | "degraded";
  app: string;
  environment: string;
  database: { connected: boolean; dialect: string; error: string | null };
  active_sku_count: number;
  integrations: {
    parser_mode: ParserMode;
    parser_model: string;
    paypal_mode: PayPalMode | null;
    paypal_env: "sandbox" | "live";
  };
}

export interface VendorSummary {
  id: number;
  name: string;
  paypal_merchant_email: string;
  commission_rate_bps: number;
}

export interface Sku {
  id: number;
  sku_code: string;
  name: string;
  description: string;
  category: string;
  unit_of_measure: string;
  aliases: string[];
  unit_price_cents: number;
  currency: string;
  stock_quantity: number;
  is_active: boolean;
  vendor: VendorSummary;
}

export interface SkuListResponse {
  total: number;
  limit: number;
  offset: number;
  items: Sku[];
}

export interface SkuListParams {
  vendor_id?: number;
  vendor?: string;
  category?: string;
  q?: string;
  limit?: number;
  offset?: number;
}

// --- match preview -------------------------------------------------------------------------

export interface MatchPreviewItem {
  query: string;
  quantity: number;
  /** Operator override: resolve this line to exactly this SKU. */
  sku_code?: string;
}

export interface MatchPreviewRequest {
  items: MatchPreviewItem[];
  top_k?: number;
}

export interface Candidate {
  sku_id: number;
  sku_code: string;
  name: string;
  unit_of_measure: string;
  unit_price_cents: number;
  currency: string;
  stock_quantity: number;
  confidence_score: number;
  match_reasoning: string;
  vendor: VendorSummary;
}

export interface LineItemPreview {
  line_index: number;
  query: string;
  normalized_query: string;
  quantity: number;
  match_status: MatchStatus;
  is_ambiguous: boolean;
  is_pinned: boolean;
  matched_sku: Candidate | null;
  candidates: Candidate[];
  stock_status: StockStatus;
  available_quantity: number | null;
  is_billable: boolean;
  issues: string[];
  unit_price_cents: number;
  subtotal_cents: number;
  commission_rate_bps: number;
  platform_fee_cents: number;
  net_vendor_payout_cents: number;
}

export interface VendorPayoutPreview {
  vendor_id: number;
  vendor_name: string;
  paypal_merchant_email: string;
  commission_rate_bps: number;
  line_count: number;
  subtotal_cents: number;
  platform_fee_cents: number;
  net_vendor_payout_cents: number;
}

export interface PreviewTotals {
  subtotal_cents: number;
  platform_fee_cents: number;
  net_vendor_payout_cents: number;
}

export interface MatchPreviewResponse {
  currency: string;
  all_lines_billable: boolean;
  unresolved_line_indexes: number[];
  lines: LineItemPreview[];
  vendor_payouts: VendorPayoutPreview[];
  totals: PreviewTotals;
}

// --- order parsing -------------------------------------------------------------------------

export interface ExtractedLineItem {
  raw_line_text: string;
  search_query: string;
  quantity: number;
  unit_hint: string | null;
  notes: string | null;
}

export interface ParseTextRequest {
  text: string;
  top_k?: number;
  allow_fallback?: boolean;
}

export interface ParsedOrderPreviewResponse {
  parser_mode: ParserMode;
  model_used: string;
  customer_note: string | null;
  extracted_items: ExtractedLineItem[];
  unrecognized_fragments: string[];
  match_preview: MatchPreviewResponse;
}

// --- order ledger --------------------------------------------------------------------------

export interface CreateOrderItem {
  sku_code: string;
  quantity: number;
  raw_query?: string | null;
}

export interface CreateOrderRequest {
  items: CreateOrderItem[];
  customer_note?: string | null;
  parser_mode?: ParserMode | "manual" | null;
}

export interface OrderLine {
  id: number;
  sku_id: number;
  vendor_id: number;
  sku_code: string;
  product_name: string;
  raw_query: string | null;
  quantity: number;
  unit_price_cents: number;
  subtotal_cents: number;
  commission_rate_bps: number;
  platform_fee_cents: number;
  net_vendor_payout_cents: number;
}

export interface VendorPayout {
  id: number;
  vendor_id: number;
  vendor_name: string;
  receiver_paypal_email: string;
  currency: string;
  gross_subtotal_cents: number;
  platform_fee_cents: number;
  payout_amount_cents: number;
  sender_batch_id: string;
  sender_item_id: string;
  paypal_payout_batch_id: string | null;
  paypal_payout_item_id: string | null;
  status: PayoutStatus;
  attempt_count: number;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface OrderSummary {
  public_order_id: string;
  status: OrderStatus;
  currency: string;
  gross_subtotal_cents: number;
  total_platform_fee_cents: number;
  total_vendor_payout_cents: number;
  paypal_mode: PayPalMode;
  paypal_order_id: string | null;
  paypal_capture_id: string | null;
  created_at: string;
  updated_at: string;
  captured_at: string | null;
}

export interface OrderDetail extends OrderSummary {
  idempotency_key: string;
  customer_note: string | null;
  parser_mode: string | null;
  approval_url: string | null;
  paypal_capture_status: string | null;
  stock_reserved: boolean;
  payout_hold_reason: string | null;
  last_error: string | null;
  line_items: OrderLine[];
  payouts: VendorPayout[];
}

export interface OrderListResponse {
  total: number;
  limit: number;
  offset: number;
  items: OrderSummary[];
}

export interface OrderListParams {
  status?: OrderStatus;
  limit?: number;
  offset?: number;
}

// --- agentic commerce ----------------------------------------------------------------------

export interface JsonSchema {
  type?: string;
  description?: string;
  properties?: Record<string, JsonSchema>;
  items?: JsonSchema;
  required?: string[];
  [key: string]: unknown;
}

export interface GeminiFunctionDeclaration {
  name: string;
  description: string;
  parameters: JsonSchema;
}

export interface McpTool {
  name: string;
  description: string;
  inputSchema: JsonSchema;
}

export interface AgentToolsResponse {
  gemini_function_declarations: GeminiFunctionDeclaration[];
  mcp_tools: McpTool[];
}

export interface ApiErrorBody {
  detail?: string | Array<{ loc?: Array<string | number>; msg?: string }>;
  error?: string;
}
