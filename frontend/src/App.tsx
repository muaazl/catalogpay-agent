import "./lib/agGrid";

import { XCircle } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { useApi } from "./api/context";
import type { HealthResponse } from "./api/types";
import { CatalogDrawer } from "./components/CatalogDrawer";
import { FlowStepper } from "./components/FlowStepper";
import { KpiCards } from "./components/KpiCards";
import { OrderIntakePanel } from "./components/OrderIntakePanel";
import { PayPalSettlementPanel } from "./components/PayPalSettlementPanel";
import { SkuResolutionGrid } from "./components/SkuResolutionGrid";
import { StatusBar } from "./components/StatusBar";
import { VendorSettlementGrid } from "./components/VendorSettlementGrid";
import { useCommandCenter } from "./hooks/useCommandCenter";

const HEALTH_POLL_MS = 15_000;

export default function App({ animationStepMs = 450 }: { animationStepMs?: number }) {
  const client = useApi();
  const { state, stage, actions } = useCommandCenter(client);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthError, setHealthError] = useState(false);
  const [catalogOpen, setCatalogOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const poll = () =>
      client
        .health()
        .then((h) => {
          if (cancelled) return;
          setHealth(h);
          setHealthError(false);
        })
        .catch(() => !cancelled && setHealthError(true));
    void poll();
    const id = window.setInterval(poll, HEALTH_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [client]);

  const closeCatalog = useCallback(() => setCatalogOpen(false), []);
  const error = state.error ?? notice;
  const settlementBusy =
    state.busy === "creating" || state.busy === "capturing" || state.busy === "retrying"
      ? state.busy
      : null;

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <StatusBar
        health={health}
        healthError={healthError}
        runDurationMs={state.runDurationMs}
        onOpenCatalog={() => setCatalogOpen(true)}
      />

      <main className="mx-auto max-w-[1680px] space-y-5 px-4 py-5 sm:px-6">
        <FlowStepper stage={stage} />

        {error && (
          <div
            role="alert"
            className="flex items-start justify-between gap-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800"
          >
            <span>{error}</span>
            <button
              type="button"
              aria-label="Dismiss error"
              onClick={() => {
                actions.dismissError();
                setNotice(null);
              }}
              className="text-rose-500 hover:text-rose-700"
            >
              <XCircle className="size-4" />
            </button>
          </div>
        )}

        <div className="grid gap-5 xl:grid-cols-12">
          <div className="xl:col-span-4 2xl:col-span-3">
            <OrderIntakePanel
              text={state.text}
              busy={state.busy === "parsing"}
              locked={state.order !== null}
              parse={state.parse}
              sourceLabel={state.sourceLabel}
              active={stage === "intake"}
              onTextChange={actions.setText}
              onParseText={(text, label) => void actions.parseText(text, label)}
              onParseMedia={(file) => void actions.parseMedia(file)}
              onReject={setNotice}
            />
          </div>

          <div className="min-w-0 space-y-5 xl:col-span-8 2xl:col-span-9">
            <KpiCards preview={state.preview} order={state.order} />
            <SkuResolutionGrid
              preview={state.preview}
              locked={state.order !== null}
              lockedOrderId={state.order?.public_order_id}
              items={state.items}
              busy={state.busy === "previewing"}
              active={stage === "resolve"}
              onInvalidQuantity={actions.reportError}
              onQuantityChange={actions.updateQuantity}
              onPinSku={actions.pinSku}
            />
          </div>

          <div className="xl:col-span-4 2xl:col-span-3">
            <PayPalSettlementPanel
              preview={state.preview}
              order={state.order}
              busy={settlementBusy}
              blocked={state.busy !== null}
              idempotencyKey={state.idempotencyKey}
              paypalMode={health?.integrations.paypal_mode ?? null}
              animationStepMs={animationStepMs}
              active={stage === "checkout" || stage === "settle"}
              onCreateOrder={() => void actions.createOrder()}
              onCapture={() => void actions.capture()}
              onRetry={() => void actions.retryPayouts()}
              onReset={actions.reset}
            />
          </div>

          <div className="min-w-0 xl:col-span-8 2xl:col-span-9">
            <VendorSettlementGrid
              order={state.order}
              preview={state.preview}
              historyVersion={state.historyVersion}
              active={stage === "done"}
              onSelectOrder={(id) => void actions.loadOrder(id)}
            />
          </div>
        </div>

        <footer className="pb-4 text-center text-xs text-slate-400">
          CatalogPay Agent · amounts are integer cents end to end · PayPal Orders v2 + Payouts v1
        </footer>
      </main>

      <CatalogDrawer open={catalogOpen} onClose={closeCatalog} />
    </div>
  );
}
