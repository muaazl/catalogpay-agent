import { Banknote, Landmark, ListChecks, Store } from "lucide-react";

import type { MatchPreviewResponse, OrderDetail } from "../api/types";
import { formatCents } from "../utils/money";
import { Stat } from "./ui/Stat";

/** Headline economics. Once an order exists its persisted ledger is the source of truth. */
export function KpiCards({
  preview,
  order,
}: {
  preview: MatchPreviewResponse | null;
  order: OrderDetail | null;
}) {
  const currency = order?.currency ?? preview?.currency ?? "USD";
  const gross = order?.gross_subtotal_cents ?? preview?.totals.subtotal_cents ?? 0;
  const fee = order?.total_platform_fee_cents ?? preview?.totals.platform_fee_cents ?? 0;
  const net = order?.total_vendor_payout_cents ?? preview?.totals.net_vendor_payout_cents ?? 0;
  const vendors = order?.payouts.length ?? preview?.vendor_payouts.length ?? 0;
  const billable = preview?.lines.filter((l) => l.is_billable).length ?? 0;
  const lines = preview?.lines.length ?? 0;
  const balanced = gross === fee + net;

  return (
    <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      <Stat
        testId="kpi-gmv"
        label="Gross GMV"
        icon={<Banknote className="size-3.5" aria-hidden />}
        value={formatCents(gross, currency)}
        hint={order ? "Persisted order ledger" : "Live preview"}
      />
      <Stat
        testId="kpi-fee"
        label="Platform net revenue"
        icon={<Landmark className="size-3.5" aria-hidden />}
        value={formatCents(fee, currency)}
        accent="text-brand-700"
        hint="Commission charged once per vendor"
      />
      <Stat
        testId="kpi-net"
        label="Vendor disbursements"
        icon={<Store className="size-3.5" aria-hidden />}
        value={formatCents(net, currency)}
        accent="text-emerald-700"
        hint={`${vendors} vendor${vendors === 1 ? "" : "s"} · ${
          balanced ? "GMV = fee + payouts ✓" : "ledger imbalance!"
        }`}
      />
      <Stat
        testId="kpi-lines"
        label="Lines ready"
        icon={<ListChecks className="size-3.5" aria-hidden />}
        value={`${billable}/${lines}`}
        accent={lines && billable === lines ? "text-emerald-700" : "text-amber-700"}
        hint={lines && billable < lines ? `${lines - billable} need review` : "Every line verified"}
      />
    </div>
  );
}
