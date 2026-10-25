import { useCallback, useMemo, useRef, useState } from "react";

import { ApiError, type ApiClient } from "../api/client";
import type {
  Candidate,
  CreateOrderItem,
  LineItemPreview,
  MatchPreviewResponse,
  OrderDetail,
  ParsedOrderPreviewResponse,
  VendorPayoutPreview,
} from "../api/types";

/** What the operator is ordering: the buyer's fragment, a quantity, and an optional SKU pin. */
export interface WorkItem {
  query: string;
  quantity: number;
  skuCode?: string;
}

export type Busy =
  | null
  | "parsing"
  | "previewing"
  | "creating"
  | "capturing"
  | "retrying"
  | "loading";

export type Stage = "intake" | "resolve" | "checkout" | "settle" | "done";

export interface CommandCenterState {
  text: string;
  sourceLabel: string | null;
  parse: ParsedOrderPreviewResponse | null;
  items: WorkItem[];
  preview: MatchPreviewResponse | null;
  order: OrderDetail | null;
  idempotencyKey: string;
  busy: Busy;
  error: string | null;
  runStartedAt: number | null;
  runDurationMs: number | null;
  historyVersion: number;
}

export const MIN_QUANTITY = 1;
export const MAX_QUANTITY = 10_000;

export function newIdempotencyKey(): string {
  return `cp-${crypto.randomUUID()}`;
}

/** ``null`` if ``value`` is an acceptable order quantity, otherwise a user-facing reason. */
export function quantityError(value: unknown): string | null {
  const n = typeof value === "number" ? value : Number(String(value ?? "").trim() || NaN);
  if (!Number.isInteger(n) || n < MIN_QUANTITY || n > MAX_QUANTITY) {
    return `Quantity must be a whole number between ${MIN_QUANTITY} and ${MAX_QUANTITY}.`;
  }
  return null;
}

function messageOf(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "gemini_not_configured") {
      return "Media parsing needs GEMINI_API_KEY on the backend. Typed orders work offline.";
    }
    return error.message;
  }
  return error instanceof Error ? error.message : "Something went wrong.";
}

function itemsFromPreview(preview: MatchPreviewResponse): WorkItem[] {
  return preview.lines.map((line) => ({
    query: line.query,
    quantity: line.quantity,
    ...(line.is_pinned && line.matched_sku ? { skuCode: line.matched_sku.sku_code } : {}),
  }));
}

/**
 * Rebuild the workbench view of a persisted order, so loading one from history shows *its*
 * lines, splits and totals. Every number comes from the ledger as stored, never recomputed.
 */
export function previewFromOrder(order: OrderDetail): MatchPreviewResponse {
  const payoutByVendor = new Map(order.payouts.map((p) => [p.vendor_id, p]));
  const lines: LineItemPreview[] = order.line_items.map((line, index) => {
    const payout = payoutByVendor.get(line.vendor_id);
    const sku: Candidate = {
      sku_id: line.sku_id,
      sku_code: line.sku_code,
      name: line.product_name,
      unit_of_measure: "",
      unit_price_cents: line.unit_price_cents,
      currency: order.currency,
      stock_quantity: 0,
      confidence_score: 1,
      match_reasoning: `Persisted line of ${order.public_order_id}`,
      vendor: {
        id: line.vendor_id,
        name: payout?.vendor_name ?? `Vendor ${line.vendor_id}`,
        paypal_merchant_email: payout?.receiver_paypal_email ?? "",
        commission_rate_bps: line.commission_rate_bps,
      },
    };
    const query = line.raw_query ?? line.product_name;
    return {
      line_index: index,
      query,
      normalized_query: query.toLowerCase(),
      quantity: line.quantity,
      match_status: "matched",
      is_ambiguous: false,
      is_pinned: false,
      matched_sku: sku,
      candidates: [sku],
      stock_status: "in_stock",
      available_quantity: null,
      is_billable: true,
      issues: [],
      unit_price_cents: line.unit_price_cents,
      subtotal_cents: line.subtotal_cents,
      commission_rate_bps: line.commission_rate_bps,
      platform_fee_cents: line.platform_fee_cents,
      net_vendor_payout_cents: line.net_vendor_payout_cents,
    };
  });
  const vendorPayouts: VendorPayoutPreview[] = order.payouts.map((p) => ({
    vendor_id: p.vendor_id,
    vendor_name: p.vendor_name,
    paypal_merchant_email: p.receiver_paypal_email,
    commission_rate_bps:
      order.line_items.find((l) => l.vendor_id === p.vendor_id)?.commission_rate_bps ?? 0,
    line_count: order.line_items.filter((l) => l.vendor_id === p.vendor_id).length,
    subtotal_cents: p.gross_subtotal_cents,
    platform_fee_cents: p.platform_fee_cents,
    net_vendor_payout_cents: p.payout_amount_cents,
  }));
  return {
    currency: order.currency,
    all_lines_billable: true,
    unresolved_line_indexes: [],
    lines,
    vendor_payouts: vendorPayouts,
    totals: {
      subtotal_cents: order.gross_subtotal_cents,
      platform_fee_cents: order.total_platform_fee_cents,
      net_vendor_payout_cents: order.total_vendor_payout_cents,
    },
  };
}

const initialState = (): CommandCenterState => ({
  text: "",
  sourceLabel: null,
  parse: null,
  items: [],
  preview: null,
  order: null,
  idempotencyKey: newIdempotencyKey(),
  busy: null,
  error: null,
  runStartedAt: null,
  runDurationMs: null,
  historyVersion: 0,
});

export function stageOf(state: CommandCenterState): Stage {
  const status = state.order?.status;
  if (status === "COMPLETED") return "done";
  if (status && status !== "DRAFT" && status !== "PAYPAL_ORDER_CREATED") return "settle";
  if (state.order) return "checkout";
  if (state.preview) return "resolve";
  return "intake";
}

/** Billable preview lines become order items; unresolved ones are left out (and reported). */
export function orderItemsFromPreview(preview: MatchPreviewResponse | null): CreateOrderItem[] {
  if (!preview) return [];
  return preview.lines
    .filter((line) => line.is_billable && line.matched_sku)
    .map((line) => ({
      sku_code: line.matched_sku!.sku_code,
      quantity: line.quantity,
      raw_query: line.query,
    }));
}

export function useCommandCenter(client: ApiClient, now: () => number = () => performance.now()) {
  const [state, setState] = useState<CommandCenterState>(initialState);
  const stateRef = useRef(state);
  stateRef.current = state;

  // One sequence for *every* action. Each action takes a ticket on entry and may only commit
  // if its ticket is still the latest, so a slow response can never overwrite a newer preset,
  // parse, edit, order selection or reset.
  const requestSeq = useRef(0);
  const begin = useCallback(() => ++requestSeq.current, []);
  const isCurrent = useCallback((seq: number) => seq === requestSeq.current, []);

  const commit = useCallback(
    (seq: number, update: (prev: CommandCenterState) => CommandCenterState) => {
      if (!isCurrent(seq)) return false;
      setState(update);
      return true;
    },
    [isCurrent],
  );

  const setText = useCallback((text: string) => setState((prev) => ({ ...prev, text })), []);

  const applyParse = (parse: ParsedOrderPreviewResponse) => (prev: CommandCenterState) => ({
    ...prev,
    parse,
    preview: parse.match_preview,
    items: itemsFromPreview(parse.match_preview),
    order: null,
    idempotencyKey: newIdempotencyKey(),
    busy: null,
    error: null,
  });

  const runParse = useCallback(
    async (sourceLabel: string, text: string | null, call: () => Promise<ParsedOrderPreviewResponse>) => {
      const seq = begin();
      setState((prev) => ({
        ...prev,
        ...(text !== null ? { text } : {}),
        sourceLabel,
        busy: "parsing",
        error: null,
        runStartedAt: now(),
        runDurationMs: null,
      }));
      try {
        const parse = await call();
        commit(seq, applyParse(parse));
      } catch (error) {
        commit(seq, (prev) => ({ ...prev, busy: null, error: messageOf(error) }));
      }
    },
    [begin, commit, now],
  );

  const parseText = useCallback(
    async (text: string, sourceLabel = "Typed order") => {
      if (!text.trim()) return;
      await runParse(sourceLabel, text, () => client.parseText({ text, top_k: 3 }));
    },
    [client, runParse],
  );

  const parseMedia = useCallback(
    (file: File, note = "") => runParse(file.name, null, () => client.parseMedia(file, note, 3)),
    [client, runParse],
  );

  const recompute = useCallback(
    async (items: WorkItem[]) => {
      const seq = begin();
      const previousItems = stateRef.current.items; // restored if the re-price fails
      setState((prev) => ({
        ...prev,
        items,
        busy: "previewing",
        error: null,
        idempotencyKey: newIdempotencyKey(), // a different basket is a different order intent
      }));
      try {
        const preview = await client.matchPreview({
          top_k: 3,
          items: items.map((i) => ({
            query: i.query,
            quantity: i.quantity,
            ...(i.skuCode ? { sku_code: i.skuCode } : {}),
          })),
        });
        commit(seq, (prev) => ({ ...prev, preview, busy: null }));
      } catch (error) {
        // Keep the grid and the server preview in agreement: fall back to the last good basket.
        commit(seq, (prev) => ({
          ...prev,
          items: previousItems,
          busy: null,
          error: messageOf(error),
        }));
      }
    },
    [begin, client, commit],
  );

  const updateQuantity = useCallback(
    (index: number, quantity: number) => {
      const current = stateRef.current;
      if (current.order) return; // the basket is locked once a PayPal order exists
      const problem = quantityError(quantity);
      if (problem) {
        setState((prev) => ({ ...prev, error: problem }));
        return;
      }
      const items = current.items.map((it, i) => (i === index ? { ...it, quantity } : it));
      void recompute(items);
    },
    [recompute],
  );

  const pinSku = useCallback(
    (index: number, skuCode: string) => {
      const current = stateRef.current;
      if (current.order) return;
      const items = current.items.map((it, i) =>
        i === index ? { ...it, skuCode: skuCode || undefined } : it,
      );
      void recompute(items);
    },
    [recompute],
  );

  const createOrder = useCallback(async () => {
    const current = stateRef.current;
    if (current.busy !== null) return; // never submit a basket that is still being re-priced
    const items = orderItemsFromPreview(current.preview);
    if (items.length === 0) {
      setState((prev) => ({ ...prev, error: "No billable lines yet: resolve at least one SKU first." }));
      return;
    }
    const seq = begin();
    setState((prev) => ({ ...prev, busy: "creating", error: null }));
    try {
      const order = await client.createOrder(
        {
          items,
          customer_note: current.parse?.customer_note ?? null,
          parser_mode: current.parse?.parser_mode ?? "manual",
        },
        current.idempotencyKey, // reused on retry, so a double-click can never create two orders
      );
      commit(seq, (prev) => ({
        ...prev,
        order,
        busy: null,
        historyVersion: prev.historyVersion + 1,
      }));
    } catch (error) {
      commit(seq, (prev) => ({ ...prev, busy: null, error: messageOf(error) }));
    }
  }, [begin, client, commit]);

  const withOrder = useCallback(
    (order: OrderDetail) => (prev: CommandCenterState) => ({
      ...prev,
      order,
      busy: null,
      runDurationMs:
        order.status === "COMPLETED" && prev.runStartedAt !== null && prev.runDurationMs === null
          ? now() - prev.runStartedAt
          : prev.runDurationMs,
      historyVersion: prev.historyVersion + 1,
    }),
    [now],
  );

  const runOrderAction = useCallback(
    async (busy: "capturing" | "retrying", action: (id: string) => Promise<OrderDetail>) => {
      const order = stateRef.current.order;
      if (!order) return;
      const seq = begin();
      setState((prev) => ({ ...prev, busy, error: null }));
      try {
        commit(seq, withOrder(await action(order.public_order_id)));
      } catch (error) {
        if (!commit(seq, (prev) => ({ ...prev, busy: null, error: messageOf(error) }))) return;
        try {
          commit(seq, withOrder(await client.getOrder(order.public_order_id))); // true state
        } catch {
          /* keep the last known order */
        }
      }
    },
    [begin, client, commit, withOrder],
  );

  const capture = useCallback(
    () => runOrderAction("capturing", client.captureOrder),
    [client, runOrderAction],
  );
  const retryPayouts = useCallback(
    () => runOrderAction("retrying", client.retryPayouts),
    [client, runOrderAction],
  );

  const loadOrder = useCallback(
    async (publicOrderId: string) => {
      const seq = begin();
      setState((prev) => ({ ...prev, busy: "loading", error: null }));
      try {
        const order = await client.getOrder(publicOrderId);
        const preview = previewFromOrder(order);
        commit(seq, (prev) => ({
          ...prev,
          order,
          preview,
          items: itemsFromPreview(preview),
          parse: null,
          text: "",
          sourceLabel: `Order ${order.public_order_id}`,
          busy: null,
          runStartedAt: null,
          runDurationMs: null,
        }));
      } catch (error) {
        commit(seq, (prev) => ({ ...prev, busy: null, error: messageOf(error) }));
      }
    },
    [begin, client, commit],
  );

  const reset = useCallback(() => {
    begin(); // invalidates every in-flight request
    setState((prev) => ({ ...initialState(), historyVersion: prev.historyVersion }));
  }, [begin]);

  const dismissError = useCallback(() => setState((prev) => ({ ...prev, error: null })), []);
  const reportError = useCallback(
    (message: string) => setState((prev) => ({ ...prev, error: message })),
    [],
  );

  const actions = useMemo(
    () => ({
      setText,
      parseText,
      parseMedia,
      updateQuantity,
      pinSku,
      createOrder,
      capture,
      retryPayouts,
      loadOrder,
      reset,
      dismissError,
      reportError,
    }),
    [
      setText,
      parseText,
      parseMedia,
      updateQuantity,
      pinSku,
      createOrder,
      capture,
      retryPayouts,
      loadOrder,
      reset,
      dismissError,
      reportError,
    ],
  );

  return { state, stage: stageOf(state), actions };
}

export type CommandCenter = ReturnType<typeof useCommandCenter>;
