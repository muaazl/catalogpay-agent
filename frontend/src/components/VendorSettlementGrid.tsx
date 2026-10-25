import type { ColDef, ICellRendererParams, RowClickedEvent, ValueFormatterParams } from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { Download, History, Search, Split } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { useApi } from "../api/context";
import type {
  MatchPreviewResponse,
  OrderDetail,
  OrderStatus,
  OrderSummary,
  PayoutStatus,
} from "../api/types";
import { csvFileName, gridTheme } from "../lib/agGrid";
import { formatBps, formatCents } from "../utils/money";
import { Button } from "./ui/Button";
import { Panel } from "./ui/Panel";
import { Pill, type PillTone } from "./ui/Pill";

/** One vendor's split: from the persisted ledger, or the live preview before an order exists. */
export interface SplitRow {
  vendorId: number;
  vendorName: string;
  receiverEmail: string;
  currency: string;
  commissionBps: number | null;
  grossCents: number;
  feeCents: number;
  payoutCents: number;
  senderBatchId: string | null;
  paypalBatchId: string | null;
  status: PayoutStatus | "PREVIEW";
  /** `null` on the pinned Total row, where an attempt count is meaningless. */
  attempts: number | null;
  error: string | null;
}

export function toSplitRows(order: OrderDetail | null, preview: MatchPreviewResponse | null): SplitRow[] {
  if (order) {
    const bpsByVendor = new Map(order.line_items.map((l) => [l.vendor_id, l.commission_rate_bps]));
    return order.payouts.map((p) => ({
      vendorId: p.vendor_id,
      vendorName: p.vendor_name,
      receiverEmail: p.receiver_paypal_email,
      currency: p.currency,
      commissionBps: bpsByVendor.get(p.vendor_id) ?? null,
      grossCents: p.gross_subtotal_cents,
      feeCents: p.platform_fee_cents,
      payoutCents: p.payout_amount_cents,
      senderBatchId: p.sender_batch_id,
      paypalBatchId: p.paypal_payout_batch_id,
      status: p.status,
      attempts: p.attempt_count,
      error: p.error_message,
    }));
  }
  return (preview?.vendor_payouts ?? []).map((v) => ({
    vendorId: v.vendor_id,
    vendorName: v.vendor_name,
    receiverEmail: v.paypal_merchant_email,
    currency: preview?.currency ?? "USD",
    commissionBps: v.commission_rate_bps,
    grossCents: v.subtotal_cents,
    feeCents: v.platform_fee_cents,
    payoutCents: v.net_vendor_payout_cents,
    senderBatchId: null,
    paypalBatchId: null,
    status: "PREVIEW",
    attempts: 0,
    error: null,
  }));
}

const STATUS_TONE: Record<SplitRow["status"] | OrderStatus, PillTone> = {
  PREVIEW: "slate",
  PENDING: "slate",
  PROCESSING: "amber",
  SUCCESS: "green",
  FAILED: "red",
  DRAFT: "slate",
  PAYPAL_ORDER_CREATED: "blue",
  CAPTURED: "amber",
  PAYOUTS_PENDING: "amber",
  COMPLETED: "green",
  PAYOUTS_PARTIAL_FAILED: "red",
  CANCELLED: "slate",
};

function StatusCell({ value, data }: ICellRendererParams<SplitRow | OrderSummary, string>) {
  if (!value) return null;
  const error = data && "error" in data ? data.error : null;
  return (
    <Pill tone={STATUS_TONE[value as SplitRow["status"]] ?? "slate"} title={error ?? undefined}>
      {value}
    </Pill>
  );
}

// Both row types carry their own currency; never assume USD.
const money = (p: ValueFormatterParams<SplitRow | OrderSummary, number>) =>
  p.value === null || p.value === undefined ? "" : formatCents(p.value, p.data?.currency ?? "USD");

const moneyCol = <T,>(field: string, headerName: string, width = 130): ColDef<T> => ({
  field: field as ColDef<T>["field"],
  headerName,
  width,
  type: "rightAligned",
  valueFormatter: money as unknown as ColDef<T>["valueFormatter"],
  useValueFormatterForExport: false,
  cellClass: "font-mono tabular-nums",
});

const mono = "font-mono text-xs text-slate-600";

interface VendorSettlementGridProps {
  order: OrderDetail | null;
  preview: MatchPreviewResponse | null;
  historyVersion: number;
  active?: boolean;
  onSelectOrder: (publicOrderId: string) => void;
}

export function VendorSettlementGrid({
  order,
  preview,
  historyVersion,
  active,
  onSelectOrder,
}: VendorSettlementGridProps) {
  const client = useApi();
  const [tab, setTab] = useState<"split" | "history">("split");
  const [quickFilter, setQuickFilter] = useState("");
  const [history, setHistory] = useState<OrderSummary[]>([]);
  const splitRef = useRef<AgGridReact<SplitRow>>(null);
  const historyRef = useRef<AgGridReact<OrderSummary>>(null);

  useEffect(() => {
    let cancelled = false;
    client
      .listOrders({ limit: 100 })
      .then((res) => !cancelled && setHistory(res.items))
      .catch(() => !cancelled && setHistory([]));
    return () => {
      cancelled = true;
    };
  }, [client, historyVersion]);

  const splitRows = useMemo(() => toSplitRows(order, preview), [order, preview]);
  const splitTotals = useMemo<SplitRow[]>(
    () =>
      splitRows.length
        ? [
            {
              vendorId: -1,
              vendorName: "Total",
              receiverEmail: `${splitRows.length} receivers`,
              currency: splitRows[0]!.currency,
              commissionBps: null,
              grossCents: splitRows.reduce((s, r) => s + r.grossCents, 0),
              feeCents: splitRows.reduce((s, r) => s + r.feeCents, 0),
              payoutCents: splitRows.reduce((s, r) => s + r.payoutCents, 0),
              senderBatchId: null,
              paypalBatchId: null,
              status: "PREVIEW",
              attempts: null,
              error: null,
            },
          ]
        : [],
    [splitRows],
  );

  const splitCols = useMemo<ColDef<SplitRow>[]>(
    () => [
      { field: "vendorName", headerName: "Vendor", minWidth: 190, flex: 1, pinned: "left", cellClass: "font-medium" },
      { field: "receiverEmail", headerName: "PayPal receiver", minWidth: 230, flex: 1, cellClass: mono },
      {
        field: "commissionBps",
        headerName: "Commission",
        width: 116,
        type: "rightAligned",
        valueFormatter: (p) => (p.value === null || p.value === undefined ? "" : formatBps(p.value)),
      },
      moneyCol<SplitRow>("grossCents", "Gross subtotal"),
      moneyCol<SplitRow>("feeCents", "Platform fee", 120),
      moneyCol<SplitRow>("payoutCents", "Payout amount"),
      { field: "senderBatchId", headerName: "sender_batch_id", minWidth: 200, cellClass: mono },
      { field: "paypalBatchId", headerName: "PayPal payout batch", minWidth: 210, cellClass: mono },
      {
        field: "status",
        headerName: "Status",
        width: 126,
        cellRenderer: StatusCell,
        valueGetter: (p) => (p.data?.vendorId === -1 ? null : p.data?.status),
      },
      {
        field: "attempts",
        headerName: "Attempts",
        width: 100,
        type: "rightAligned",
        valueFormatter: (p) => (p.value === null || p.value === undefined ? "" : String(p.value)),
      },
    ],
    [],
  );

  const historyCols = useMemo<ColDef<OrderSummary>[]>(
    () => [
      { field: "public_order_id", headerName: "Order", width: 170, pinned: "left", cellClass: "font-mono text-xs font-semibold" },
      { field: "status", headerName: "Status", width: 190, cellRenderer: StatusCell },
      moneyCol<OrderSummary>("gross_subtotal_cents", "Gross"),
      moneyCol<OrderSummary>("total_platform_fee_cents", "Platform fee", 120),
      moneyCol<OrderSummary>("total_vendor_payout_cents", "Vendor payouts"),
      { field: "paypal_order_id", headerName: "PayPal order", minWidth: 210, cellClass: mono },
      { field: "paypal_capture_id", headerName: "Capture", minWidth: 190, cellClass: mono },
      { field: "paypal_mode", headerName: "Mode", width: 160 },
      {
        field: "created_at",
        headerName: "Created",
        width: 180,
        sort: "desc",
        valueFormatter: (p) => (p.value ? new Date(p.value).toLocaleString() : ""),
      },
    ],
    [],
  );

  const exportCsv = () => {
    const params = {
      fileName: csvFileName(tab === "split" ? "vendor-settlement" : "order-ledger"),
    };
    if (tab === "split") splitRef.current?.api.exportDataAsCsv(params);
    else historyRef.current?.api.exportDataAsCsv(params);
  };

  const tabButton = (id: "split" | "history", label: string, Icon: typeof Split, count: number) => (
    <button
      type="button"
      role="tab"
      aria-selected={tab === id}
      onClick={() => setTab(id)}
      className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition ${
        tab === id ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"
      }`}
    >
      <Icon className="size-3.5" aria-hidden /> {label}
      <span className={`rounded-full px-1.5 font-mono ${tab === id ? "bg-white/20" : "bg-slate-200"}`}>{count}</span>
    </button>
  );

  return (
    <Panel
      step={4}
      title="Multi-vendor settlement & audit ledger"
      subtitle={
        order
          ? `Persisted ledger for ${order.public_order_id}: integer cents end to end`
          : "Preview of each vendor's split; becomes the payout ledger at checkout"
      }
      active={active}
      testId="ledger-panel"
      actions={
        <>
          <div role="tablist" className="flex gap-1 rounded-xl bg-slate-100 p-1">
            {tabButton("split", "Vendor split", Split, splitRows.length)}
            {tabButton("history", "Order history", History, history.length)}
          </div>
          <label className="relative">
            <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-slate-400" aria-hidden />
            <input
              type="search"
              aria-label="Filter ledger"
              placeholder="Quick filter…"
              value={quickFilter}
              onChange={(e) => setQuickFilter(e.target.value)}
              className="w-36 rounded-lg border border-slate-300 py-1.5 pl-7 pr-2 text-xs focus:border-brand-500 focus:outline-none"
            />
          </label>
          <Button variant="secondary" size="sm" onClick={exportCsv} icon={<Download className="size-3.5" aria-hidden />}>
            Export CSV
          </Button>
        </>
      }
    >
      {tab === "split" ? (
        <div data-testid="split-grid">
          <AgGridReact<SplitRow>
            ref={splitRef}
            theme={gridTheme}
            rowData={splitRows}
            pinnedBottomRowData={splitTotals}
            columnDefs={splitCols}
            defaultColDef={{ sortable: true, resizable: true, filter: true }}
            getRowId={(p) => String(p.data.vendorId)}
            quickFilterText={quickFilter}
            domLayout="autoHeight"
            suppressColumnVirtualisation
            overlayNoRowsTemplate="Resolve an order to see each vendor's split."
            animateRows
          />
        </div>
      ) : (
        <div data-testid="history-grid">
          <AgGridReact<OrderSummary>
            ref={historyRef}
            theme={gridTheme}
            rowData={history}
            columnDefs={historyCols}
            defaultColDef={{ sortable: true, resizable: true, filter: true }}
            getRowId={(p) => p.data.public_order_id}
            quickFilterText={quickFilter}
            rowSelection={{ mode: "singleRow", checkboxes: false, enableClickSelection: true }}
            onRowClicked={(e: RowClickedEvent<OrderSummary>) => e.data && onSelectOrder(e.data.public_order_id)}
            overlayNoRowsTemplate="No orders yet. Create one from Panel 3."
            domLayout="autoHeight"
            pagination
            paginationPageSize={10}
            paginationPageSizeSelector={[10, 20, 50, 100]}
          />
          <p className="mt-2 text-xs text-slate-500">Click an order to open its settlement in Panel 3.</p>
        </div>
      )}
    </Panel>
  );
}
