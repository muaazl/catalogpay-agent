import type { ColDef } from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { Bot, Boxes, Check, Copy, Download, Search, Store, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { useApi } from "../api/context";
import type { AgentToolsResponse, Sku } from "../api/types";
import { csvFileName, gridTheme } from "../lib/agGrid";
import { formatBps, formatCents } from "../utils/money";
import { Button } from "./ui/Button";
import { Pill } from "./ui/Pill";

export interface VendorRow {
  id: number;
  name: string;
  email: string;
  commissionBps: number;
  skuCount: number;
  categories: string;
  stockUnits: number;
}

export function vendorsFromSkus(skus: Sku[]): VendorRow[] {
  const byId = new Map<number, VendorRow & { cats: Set<string> }>();
  for (const sku of skus) {
    const v = sku.vendor;
    const row =
      byId.get(v.id) ??
      { id: v.id, name: v.name, email: v.paypal_merchant_email, commissionBps: v.commission_rate_bps, skuCount: 0, categories: "", stockUnits: 0, cats: new Set<string>() };
    row.skuCount += 1;
    row.stockUnits += sku.stock_quantity;
    row.cats.add(sku.category);
    byId.set(v.id, row);
  }
  return [...byId.values()]
    .map(({ cats, ...row }) => ({ ...row, categories: [...cats].sort().join(", ") }))
    .sort((a, b) => a.id - b.id);
}

type Tab = "skus" | "vendors" | "tools";

export function CatalogDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const client = useApi();
  const [tab, setTab] = useState<Tab>("skus");
  const [skus, setSkus] = useState<Sku[] | null>(null);
  const [tools, setTools] = useState<AgentToolsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [copied, setCopied] = useState(false);
  const skuGrid = useRef<AgGridReact<Sku>>(null);
  const vendorGrid = useRef<AgGridReact<VendorRow>>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    Promise.all([client.listSkus({ limit: 200 }), client.agentTools()])
      .then(([skuList, toolList]) => {
        if (cancelled) return;
        setSkus(skuList.items);
        setTools(toolList);
        setError(null);
      })
      .catch((e: unknown) => !cancelled && setError(e instanceof Error ? e.message : "Failed to load"));
    return () => {
      cancelled = true;
    };
  }, [client, open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const vendors = useMemo(() => vendorsFromSkus(skus ?? []), [skus]);

  const skuCols = useMemo<ColDef<Sku>[]>(
    () => [
      { field: "sku_code", headerName: "SKU", width: 180, pinned: "left", cellClass: "font-mono text-xs font-semibold" },
      { field: "name", headerName: "Product", minWidth: 230, flex: 1 },
      { field: "category", headerName: "Category", width: 150, filter: true },
      { field: "unit_of_measure", headerName: "Unit", width: 130 },
      {
        field: "unit_price_cents",
        headerName: "Price",
        width: 110,
        type: "rightAligned",
        valueFormatter: (p) => formatCents(p.value ?? 0, p.data?.currency ?? "USD"),
        useValueFormatterForExport: false,
        cellClass: "font-mono",
      },
      {
        field: "stock_quantity",
        headerName: "Stock",
        width: 96,
        type: "rightAligned",
        cellClassRules: { "text-rose-600 font-semibold": (p) => (p.value ?? 0) === 0 },
      },
      { headerName: "Vendor", valueGetter: (p) => p.data?.vendor.name, minWidth: 190 },
      { field: "aliases", headerName: "Aliases (buyer slang)", minWidth: 260, valueFormatter: (p) => (p.value ?? []).join(" · ") },
    ],
    [],
  );

  const vendorCols = useMemo<ColDef<VendorRow>[]>(
    () => [
      { field: "name", headerName: "Vendor", minWidth: 200, flex: 1, cellClass: "font-medium" },
      { field: "email", headerName: "PayPal sandbox receiver", minWidth: 250, flex: 1, cellClass: "font-mono text-xs" },
      { field: "commissionBps", headerName: "Commission", width: 120, type: "rightAligned", valueFormatter: (p) => formatBps(p.value ?? 0) },
      { field: "skuCount", headerName: "SKUs", width: 90, type: "rightAligned" },
      { field: "stockUnits", headerName: "Units in stock", width: 130, type: "rightAligned" },
      { field: "categories", headerName: "Categories", minWidth: 200, flex: 1 },
    ],
    [],
  );

  if (!open) return null;

  const copyTools = async () => {
    if (!tools) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(tools, null, 2));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable (e.g. insecure context) */
    }
  };

  const tabs: { id: Tab; label: string; Icon: typeof Boxes; count?: number }[] = [
    { id: "skus", label: "SKU catalog", Icon: Boxes, count: skus?.length },
    { id: "vendors", label: "Vendors", Icon: Store, count: vendors.length },
    { id: "tools", label: "Agent tools (MCP)", Icon: Bot, count: tools?.mcp_tools.length },
  ];

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label="Catalog and agent tools">
      <button type="button" aria-label="Close drawer" className="absolute inset-0 bg-slate-900/40 backdrop-blur-[1px]" onClick={onClose} />
      <aside className="relative flex h-full w-full max-w-5xl flex-col bg-white shadow-2xl">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-5 py-4">
          <div>
            <h2 className="text-base font-semibold text-slate-900">Catalog & agentic-commerce tools</h2>
            <p className="text-xs text-slate-500">The seeded multi-vendor catalog and the tool schemas an AI agent uses to transact</p>
          </div>
          <div className="flex items-center gap-2">
            <div role="tablist" className="flex gap-1 rounded-xl bg-slate-100 p-1">
              {tabs.map(({ id, label, Icon, count }) => (
                <button
                  key={id}
                  type="button"
                  role="tab"
                  aria-selected={tab === id}
                  onClick={() => setTab(id)}
                  className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium ${
                    tab === id ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-200"
                  }`}
                >
                  <Icon className="size-3.5" aria-hidden /> {label}
                  {count !== undefined && <span className="font-mono opacity-75">{count}</span>}
                </button>
              ))}
            </div>
            <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close" icon={<X className="size-4" aria-hidden />} />
          </div>
        </header>

        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-auto p-5">
          {error && <p className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p>}

          {tab !== "tools" && (
            <div className="flex items-center justify-between gap-2">
              <label className="relative">
                <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-slate-400" aria-hidden />
                <input
                  type="search"
                  aria-label="Filter catalog"
                  placeholder="Filter…"
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                  className="w-56 rounded-lg border border-slate-300 py-1.5 pl-7 pr-2 text-xs focus:border-brand-500 focus:outline-none"
                />
              </label>
              <Button
                variant="secondary"
                size="sm"
                icon={<Download className="size-3.5" aria-hidden />}
                onClick={() =>
                  tab === "skus"
                    ? skuGrid.current?.api.exportDataAsCsv({ fileName: csvFileName("catalog") })
                    : vendorGrid.current?.api.exportDataAsCsv({ fileName: csvFileName("vendors") })
                }
              >
                Export CSV
              </Button>
            </div>
          )}

          {tab === "skus" && (
            <div className="min-h-[420px] flex-1" data-testid="catalog-grid">
              <AgGridReact<Sku>
                ref={skuGrid}
                theme={gridTheme}
                rowData={skus ?? []}
                loading={skus === null && !error}
                columnDefs={skuCols}
                defaultColDef={{ sortable: true, resizable: true }}
                getRowId={(p) => p.data.sku_code}
                quickFilterText={filter}
              />
            </div>
          )}
          {tab === "vendors" && (
            <div data-testid="vendor-grid">
              <AgGridReact<VendorRow>
                ref={vendorGrid}
                theme={gridTheme}
                rowData={vendors}
                columnDefs={vendorCols}
                defaultColDef={{ sortable: true, resizable: true }}
                getRowId={(p) => String(p.data.id)}
                quickFilterText={filter}
                domLayout="autoHeight"
              />
            </div>
          )}
          {tab === "tools" && tools && (
            <div className="space-y-3" data-testid="agent-tools">
              <div className="flex items-center justify-between">
                <p className="text-sm text-slate-600">
                  Exposed as Gemini function declarations and MCP <span className="font-mono">tools/list</span> entries.
                  Prices are never agent inputs: they come from the catalog.
                </p>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={copyTools}
                  icon={copied ? <Check className="size-3.5" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
                >
                  {copied ? "Copied" : "Copy JSON"}
                </Button>
              </div>
              {tools.mcp_tools.map((tool) => {
                const props = tool.inputSchema.properties ?? {};
                const required = new Set(tool.inputSchema.required ?? []);
                return (
                  <article key={tool.name} className="rounded-xl border border-slate-200 p-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="font-mono text-sm font-semibold text-brand-700">{tool.name}</h3>
                      <Pill tone="violet">function</Pill>
                    </div>
                    <p className="mt-1 text-sm text-slate-600">{tool.description}</p>
                    <ul className="mt-2 flex flex-wrap gap-1.5">
                      {Object.entries(props).map(([name, schema]) => (
                        <li key={name}>
                          <Pill tone={required.has(name) ? "blue" : "slate"} title={schema.description}>
                            <span className="font-mono">{name}</span>: {schema.type ?? "any"}
                            {required.has(name) ? " *" : ""}
                          </Pill>
                        </li>
                      ))}
                    </ul>
                    <details className="mt-2">
                      <summary className="cursor-pointer text-xs font-medium text-slate-500">inputSchema JSON</summary>
                      <pre className="mt-2 max-h-72 overflow-auto rounded-lg bg-slate-900 p-3 text-[11px] leading-relaxed text-slate-100">
                        {JSON.stringify(tool.inputSchema, null, 2)}
                      </pre>
                    </details>
                  </article>
                );
              })}
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}
