import type {
  CellValueChangedEvent,
  ColDef,
  ColGroupDef,
  ICellRendererParams,
  ITooltipParams,
  RowClassRules,
  ValueFormatterParams,
} from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { CheckCircle2, Download, Lock, Pin, Search } from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";

import type { MatchPreviewResponse } from "../api/types";
import { quantityError, type WorkItem } from "../hooks/useCommandCenter";
import { csvFileName, gridTheme } from "../lib/agGrid";
import { formatBps, formatCents } from "../utils/money";
import {
  confidenceTone,
  totalsRow,
  toResolutionRows,
  type LineHealth,
  type ResolutionRow,
} from "../utils/resolution";
import { Button } from "./ui/Button";
import { Panel } from "./ui/Panel";
import { Pill, type PillTone } from "./ui/Pill";

interface GridContext {
  locked: boolean;
  currency: string;
  onPinSku: (index: number, skuCode: string) => void;
}

type CellProps<V = unknown> = ICellRendererParams<ResolutionRow, V, GridContext>;

const MONEY_FIELDS = new Set(["unitPriceCents", "subtotalCents", "platformFeeCents", "netPayoutCents"]);

function CandidateCell({ data, context }: CellProps) {
  if (!data || data.isTotal || !data.line) return null;
  const { line } = data;
  if (line.candidates.length === 0) return <span className="text-slate-400">No candidates</span>;
  const selected = line.matched_sku?.sku_code ?? "";
  return (
    <div className="flex h-full items-center gap-1.5">
      {line.is_pinned && <Pin className="size-3.5 shrink-0 text-brand-600" aria-label="Pinned by operator" />}
      <select
        aria-label={`SKU for "${line.query}"`}
        value={selected}
        disabled={context.locked}
        onChange={(e) => context.onPinSku(line.line_index, e.target.value)}
        className={`w-full min-w-0 truncate rounded-md border bg-white py-1 pl-2 pr-6 font-mono text-xs focus:border-brand-500 focus:outline-none disabled:bg-slate-50 ${
          selected ? "border-slate-300 text-slate-800" : "border-amber-400 text-amber-800"
        }`}
      >
        {!selected && <option value="">Choose a SKU…</option>}
        {line.candidates.map((c) => (
          <option key={c.sku_code} value={c.sku_code}>
            {[c.sku_code, c.name, c.unit_of_measure, formatCents(c.unit_price_cents, c.currency)]
              .filter(Boolean)
              .join(" · ")}
          </option>
        ))}
      </select>
    </div>
  );
}

const TONE_BAR: Record<string, string> = {
  green: "bg-emerald-500",
  amber: "bg-amber-500",
  red: "bg-rose-500",
};

function ConfidenceCell({ data }: CellProps) {
  if (!data || data.isTotal || !data.line) return null;
  const tone = confidenceTone(data.line);
  const score = data.confidence ?? 0;
  const pct = Math.round(score * 100);
  return (
    <div className="flex h-full items-center gap-2" data-testid={`confidence-${data.index}`} data-tone={tone}>
      <div className="h-1.5 w-14 overflow-hidden rounded-full bg-slate-200" aria-hidden>
        <div className={`h-full ${TONE_BAR[tone]}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="font-mono text-xs tabular-nums text-slate-700">
        {data.line.is_pinned ? "Pinned" : score.toFixed(2)}
      </span>
    </div>
  );
}

function VendorCell({ data }: CellProps) {
  if (!data) return null;
  if (data.isTotal) return <span className="text-xs font-semibold text-slate-600">{data.vendorName}</span>;
  if (!data.vendorName) return <span className="text-slate-400">—</span>;
  return (
    <div className="flex h-full items-center gap-1.5">
      <span className="truncate text-xs font-medium text-slate-700">{data.vendorName}</span>
      {data.commissionBps !== null && (
        <Pill tone="slate" title="Platform commission (basis points)">
          {formatBps(data.commissionBps)}
        </Pill>
      )}
    </div>
  );
}

const HEALTH: Record<LineHealth, { tone: PillTone; label: string }> = {
  ready: { tone: "green", label: "Ready" },
  review: { tone: "amber", label: "Needs review" },
  out_of_stock: { tone: "red", label: "Out of stock" },
  unmatched: { tone: "red", label: "Unmatched" },
};

function HealthCell({ data }: CellProps) {
  if (!data || data.isTotal || !data.health) return null;
  const { tone, label } = HEALTH[data.health];
  const available = data.line?.available_quantity;
  return (
    <Pill tone={tone} title={data.line?.issues.join(" ") || undefined}>
      {data.health === "ready" && <CheckCircle2 className="size-3" aria-hidden />}
      {label}
      {data.health === "out_of_stock" && available !== null && available !== undefined
        ? ` (${Math.max(available, 0)} left)`
        : ""}
    </Pill>
  );
}

function moneyFormatter(params: ValueFormatterParams<ResolutionRow, number | null>): string {
  const value = params.value;
  if (value === null || value === undefined) return "—";
  return formatCents(value, (params.context as GridContext).currency);
}

const REVERT_SOURCE = "catalogpay-revert";

interface SkuResolutionGridProps {
  preview: MatchPreviewResponse | null;
  items?: readonly WorkItem[];
  onInvalidQuantity?: (message: string) => void;
  locked: boolean;
  busy: boolean;
  active?: boolean;
  lockedOrderId?: string | null;
  onQuantityChange: (index: number, quantity: number) => void;
  onPinSku: (index: number, skuCode: string) => void;
}

export function SkuResolutionGrid({
  preview,
  items,
  onInvalidQuantity,
  locked,
  busy,
  active,
  lockedOrderId,
  onQuantityChange,
  onPinSku,
}: SkuResolutionGridProps) {
  const gridRef = useRef<AgGridReact<ResolutionRow>>(null);
  const [quickFilter, setQuickFilter] = useState("");
  const rows = useMemo(() => toResolutionRows(preview, items), [preview, items]);
  const pinnedBottom = useMemo(() => totalsRow(preview), [preview]);

  const context = useMemo<GridContext>(
    () => ({ locked, currency: preview?.currency ?? "USD", onPinSku }),
    [locked, preview?.currency, onPinSku],
  );

  const columnDefs = useMemo<(ColDef<ResolutionRow> | ColGroupDef<ResolutionRow>)[]>(
    () => [
      {
        headerName: "#",
        valueGetter: (p) => (p.data && !p.data.isTotal ? p.data.index + 1 : ""),
        width: 56,
        pinned: "left",
        sortable: false,
        cellClass: "text-slate-400 font-mono",
      },
      {
        field: "query",
        headerName: "Raw buyer fragment",
        minWidth: 200,
        flex: 1.3,
        pinned: "left",
        cellClass: (p) => (p.data?.isTotal ? "font-semibold text-slate-800" : "text-slate-800"),
        tooltipValueGetter: (p: ITooltipParams<ResolutionRow>) =>
          p.data && !p.data.isTotal ? `Normalized: ${p.data.normalizedQuery}` : undefined,
      },
      {
        field: "quantity",
        headerName: "Qty",
        width: 84,
        type: "rightAligned",
        editable: (p) => !locked && !p.data?.isTotal,
        cellEditor: "agNumberCellEditor",
        cellEditorParams: { min: 1, max: 10000, precision: 0, showStepperButtons: true },
        cellClass: (p) =>
          !locked && !p.data?.isTotal
            ? "font-mono font-semibold text-brand-700 underline decoration-dotted underline-offset-4 cursor-text"
            : "font-mono",
        headerTooltip: "Click to edit; the preview is re-priced instantly",
      },
      {
        field: "skuCode",
        headerName: "Matched SKU · candidates",
        minWidth: 260,
        flex: 1.6,
        cellRenderer: CandidateCell,
        sortable: false,
        tooltipValueGetter: (p: ITooltipParams<ResolutionRow>) =>
          p.data?.line?.matched_sku?.match_reasoning ??
          p.data?.line?.candidates[0]?.match_reasoning,
      },
      {
        field: "confidence",
        headerName: "Confidence",
        width: 130,
        cellRenderer: ConfidenceCell,
        tooltipValueGetter: (p: ITooltipParams<ResolutionRow>) =>
          p.data?.line?.issues.join(" ") || undefined,
      },
      {
        field: "vendorName",
        headerName: "Vendor · commission",
        minWidth: 210,
        flex: 1,
        cellRenderer: VendorCell,
      },
      {
        headerName: "Economics",
        marryChildren: true,
        children: [
          { field: "unitPriceCents", headerName: "Unit price", width: 108 },
          { field: "subtotalCents", headerName: "Line subtotal", width: 122 },
          { field: "platformFeeCents", headerName: "Platform fee", width: 116 },
          { field: "netPayoutCents", headerName: "Net vendor payout", width: 146 },
        ].map(
          (c): ColDef<ResolutionRow> => ({
            ...(c as ColDef<ResolutionRow>),
            type: "rightAligned",
            valueFormatter: moneyFormatter,
            useValueFormatterForExport: false, // CSV gets exact integer cents for finance import
            cellClass: "font-mono tabular-nums",
          }),
        ),
      },
      { field: "health", headerName: "Status", width: 150, cellRenderer: HealthCell },
    ],
    [locked],
  );

  const rowClassRules = useMemo<RowClassRules<ResolutionRow>>(
    () => ({
      "!bg-rose-50/50": (p) => !!p.data && !p.data.isTotal && p.data.health !== "ready",
      "!bg-slate-50 font-semibold": (p) => !!p.data?.isTotal,
    }),
    [],
  );

  const onCellValueChanged = useCallback(
    (event: CellValueChangedEvent<ResolutionRow>) => {
      if (event.colDef.field !== "quantity" || !event.data || event.data.isTotal) return;
      if (event.source === REVERT_SOURCE) return; // our own rollback below
      const problem = quantityError(event.newValue);
      if (problem) {
        // Never leave an invalid number in the cell: it would disagree with state.items.
        event.node.setDataValue("quantity", event.oldValue, REVERT_SOURCE);
        onInvalidQuantity?.(problem);
        return;
      }
      const next = Number(event.newValue);
      if (next !== Number(event.oldValue)) onQuantityChange(event.data.index, next);
    },
    [onQuantityChange, onInvalidQuantity],
  );

  const exportCsv = () =>
    gridRef.current?.api.exportDataAsCsv({
      fileName: csvFileName("sku-resolution"),
      processHeaderCallback: (p) => {
        const name = p.column.getColDef().headerName ?? p.column.getColId();
        return MONEY_FIELDS.has(p.column.getColId()) ? `${name} (cents)` : name;
      },
    });

  const billable = preview?.lines.filter((l) => l.is_billable).length ?? 0;
  const total = preview?.lines.length ?? 0;

  return (
    <Panel
      step={2}
      title="SKU resolution & margin workbench"
      subtitle="Edit a quantity or switch a candidate: every cent is re-priced server-side"
      active={active}
      testId="resolution-panel"
      actions={
        <>
          {locked && lockedOrderId && (
            <Pill tone="blue">
              <Lock className="size-3" aria-hidden /> Locked to {lockedOrderId}
            </Pill>
          )}
          {preview && (
            <Pill tone={billable === total ? "green" : "amber"}>
              {billable}/{total} lines billable
            </Pill>
          )}
          <label className="relative">
            <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-slate-400" aria-hidden />
            <input
              type="search"
              aria-label="Filter resolution grid"
              placeholder="Filter lines…"
              value={quickFilter}
              onChange={(e) => setQuickFilter(e.target.value)}
              className="w-36 rounded-lg border border-slate-300 py-1.5 pl-7 pr-2 text-xs focus:border-brand-500 focus:outline-none"
            />
          </label>
          <Button
            variant="secondary"
            size="sm"
            onClick={exportCsv}
            disabled={!preview}
            icon={<Download className="size-3.5" aria-hidden />}
          >
            Export CSV
          </Button>
        </>
      }
    >
      {preview ? (
        <div className={`transition-opacity ${busy ? "opacity-60" : ""}`} aria-busy={busy} data-testid="resolution-grid">
          <AgGridReact<ResolutionRow>
            ref={gridRef}
            theme={gridTheme}
            rowData={rows}
            pinnedBottomRowData={pinnedBottom}
            columnDefs={columnDefs}
            defaultColDef={{ sortable: true, resizable: true, suppressHeaderMenuButton: true }}
            context={context}
            getRowId={(p) => p.data.id}
            rowClassRules={rowClassRules}
            onCellValueChanged={onCellValueChanged}
            singleClickEdit
            stopEditingWhenCellsLoseFocus
            quickFilterText={quickFilter}
            domLayout="autoHeight"
            suppressColumnVirtualisation
            tooltipShowDelay={300}
            animateRows
            rowHeight={42}
          />
        </div>
      ) : (
        <div className="grid flex-1 place-items-center rounded-xl border border-dashed border-slate-300 bg-slate-50/60 px-6 py-14 text-center">
          <div>
            <p className="text-sm font-medium text-slate-700">No order resolved yet</p>
            <p className="mt-1 text-xs text-slate-500">
              Click a 1-click demo order. Each fragment is matched to a catalog SKU with a confidence score, then priced in integer cents.
            </p>
          </div>
        </div>
      )}
    </Panel>
  );
}
