import { Bot, Boxes, Cpu, CreditCard, Gauge, PanelRightOpen, Wifi, WifiOff } from "lucide-react";

import type { HealthResponse } from "../api/types";
import { Button } from "./ui/Button";
import { Pill } from "./ui/Pill";

interface StatusBarProps {
  health: HealthResponse | null;
  healthError: boolean;
  runDurationMs: number | null;
  onOpenCatalog: () => void;
}

export function StatusBar({ health, healthError, runDurationMs, onOpenCatalog }: StatusBarProps) {
  const online = !healthError && health?.status === "ok";
  const integrations = health?.integrations;
  const paypalLabel =
    integrations?.paypal_mode === "live_sandbox"
      ? "PayPal Live Sandbox"
      : integrations?.paypal_mode === "sandbox_simulation"
        ? "PayPal Sandbox Simulation"
        : "PayPal not configured";

  return (
    <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/90 backdrop-blur">
      <div className="mx-auto flex max-w-[1680px] flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 sm:px-6">
        <div className="flex items-center gap-2.5">
          <span className="grid size-9 place-items-center rounded-xl bg-[#003087] text-white shadow-sm">
            <Bot className="size-5" aria-hidden />
          </span>
          <div>
            <h1 className="text-[17px] font-bold leading-tight tracking-tight text-slate-900">
              CatalogPay Agent
            </h1>
            <p className="text-xs text-slate-500">
              Messy order → SKU-verified PayPal checkout → split payouts
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-1.5" aria-live="polite">
          <Pill tone={online ? "green" : healthError ? "red" : "slate"}>
            {online ? (
              <Wifi className="size-3" aria-hidden />
            ) : (
              <WifiOff className="size-3" aria-hidden />
            )}
            {online ? "Backend online" : healthError ? "Backend offline" : "Connecting…"}
          </Pill>
          {health && (
            <Pill tone="slate">
              <Boxes className="size-3" aria-hidden /> {health.active_sku_count} active SKUs
            </Pill>
          )}
          {integrations && (
            <Pill
              tone={integrations.parser_mode === "gemini" ? "violet" : "slate"}
              title={`Parser model: ${integrations.parser_model}`}
            >
              <Cpu className="size-3" aria-hidden />
              {integrations.parser_mode === "gemini" ? "Gemini 2.5 Flash" : "Deterministic fallback"}
            </Pill>
          )}
          {integrations && (
            <Pill
              tone={
                integrations.paypal_mode === "live_sandbox"
                  ? "blue"
                  : integrations.paypal_mode
                    ? "amber"
                    : "red"
              }
            >
              <CreditCard className="size-3" aria-hidden />
              {paypalLabel}
            </Pill>
          )}
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          <div
            data-testid="roi-pill"
            className="flex items-center gap-2 rounded-full bg-gradient-to-r from-[#003087] to-brand-600 px-3.5 py-1.5 text-xs text-white shadow-sm"
          >
            <Gauge className="size-3.5 shrink-0" aria-hidden />
            <span>
              Manual wholesale order & split payouts: <s className="opacity-75">~45 mins</s>{" "}
              <span aria-hidden>→</span> CatalogPay Agent: <strong>&lt;10 seconds</strong>
            </span>
            {runDurationMs !== null && (
              <span
                className="rounded-full bg-white/20 px-2 py-0.5 font-mono font-semibold"
                data-testid="run-duration"
              >
                this run {(runDurationMs / 1000).toFixed(1)}s
              </span>
            )}
          </div>
          <Button
            variant="secondary"
            size="sm"
            onClick={onOpenCatalog}
            icon={<PanelRightOpen className="size-3.5" aria-hidden />}
          >
            Catalog & agent tools
          </Button>
        </div>
      </div>
    </header>
  );
}
