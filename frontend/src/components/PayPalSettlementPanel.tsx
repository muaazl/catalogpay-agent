import {
  AlertTriangle,
  BadgeCheck,
  CircleDollarSign,
  ExternalLink,
  RefreshCw,
  RotateCcw,
  Send,
  ShieldCheck,
  Wallet,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import type {
  MatchPreviewResponse,
  OrderDetail,
  OrderStatus,
  PayoutStatus,
  PayPalMode,
} from "../api/types";
import { formatCents } from "../utils/money";
import { Button } from "./ui/Button";
import { Panel } from "./ui/Panel";
import { Pill, type PillTone } from "./ui/Pill";

const HAPPY_PATH: OrderStatus[] = [
  "DRAFT",
  "PAYPAL_ORDER_CREATED",
  "CAPTURED",
  "PAYOUTS_PENDING",
  "COMPLETED",
];

/** Only plain http(s) URLs may become links: never `javascript:`, `data:` or malformed URIs. */
export function safeHttpUrl(url: string | null | undefined): string | null {
  if (!url || !/^https?:\/\//i.test(url.trim())) return null;
  try {
    const parsed = new URL(url.trim());
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : null;
  } catch {
    return null;
  }
}

/** The statuses to walk through, in order, to get from ``from`` to ``to``. */
export function statusPath(from: OrderStatus | null, to: OrderStatus): OrderStatus[] {
  const target = HAPPY_PATH.includes(to) ? to : "CAPTURED"; // failures branch off after capture
  const start = from && HAPPY_PATH.includes(from) ? HAPPY_PATH.indexOf(from) : -1;
  const end = HAPPY_PATH.indexOf(target);
  const path = end > start ? HAPPY_PATH.slice(start + 1, end + 1) : [];
  if (to !== target) path.push(to);
  return path.length ? path : [to];
}

/** Animates a status change step by step so the audience sees every state transition. */
export function useStagedStatus(target: OrderStatus | null, stepMs: number): OrderStatus | null {
  const [shown, setShown] = useState<OrderStatus | null>(target);
  const shownRef = useRef(shown);
  shownRef.current = shown;

  useEffect(() => {
    if (target === null) {
      setShown(null);
      return;
    }
    const path = statusPath(shownRef.current, target);
    if (stepMs <= 0) {
      setShown(target);
      return;
    }
    const timers = path.map((status, i) => window.setTimeout(() => setShown(status), i * stepMs));
    return () => timers.forEach((t) => window.clearTimeout(t));
  }, [target, stepMs]);

  return shown;
}

const PAYOUT_TONE: Record<PayoutStatus, PillTone> = {
  PENDING: "slate",
  PROCESSING: "amber",
  SUCCESS: "green",
  FAILED: "red",
};

const STATUS_LABEL: Record<OrderStatus, string> = {
  DRAFT: "Draft",
  PAYPAL_ORDER_CREATED: "PayPal order created",
  CAPTURED: "Funds captured",
  PAYOUTS_PENDING: "Payouts settling",
  COMPLETED: "Completed",
  PAYOUTS_PARTIAL_FAILED: "Payout failed",
  CANCELLED: "Cancelled",
};

interface TimelineStep {
  key: string;
  title: string;
  detail: ReactNode;
  state: "done" | "current" | "todo" | "failed";
}

function rank(status: OrderStatus | null): number {
  if (!status) return -1;
  if (status === "PAYOUTS_PARTIAL_FAILED") return 3;
  return HAPPY_PATH.indexOf(status);
}

interface PayPalSettlementPanelProps {
  preview: MatchPreviewResponse | null;
  order: OrderDetail | null;
  busy: "creating" | "capturing" | "retrying" | null;
  /** True while *any* request is in flight (parse, re-price, load): actions must wait. */
  blocked?: boolean;
  idempotencyKey: string;
  paypalMode: PayPalMode | null;
  animationStepMs?: number;
  active?: boolean;
  onCreateOrder: () => void;
  onCapture: () => void;
  onRetry: () => void;
  onReset: () => void;
}

export function PayPalSettlementPanel({
  preview,
  order,
  busy,
  blocked = false,
  idempotencyKey,
  paypalMode,
  animationStepMs = 450,
  active,
  onCreateOrder,
  onCapture,
  onRetry,
  onReset,
}: PayPalSettlementPanelProps) {
  const shown = useStagedStatus(order?.status ?? null, animationStepMs);
  const currency = order?.currency ?? preview?.currency ?? "USD";
  const billable = preview?.lines.filter((l) => l.is_billable).length ?? 0;
  const excluded = (preview?.lines.length ?? 0) - billable;
  const simulated = (order?.paypal_mode ?? paypalMode) === "sandbox_simulation";
  const failed = order?.payouts.some((p) => p.status === "FAILED") ?? false;
  const r = rank(shown);
  const approvalUrl = safeHttpUrl(order?.approval_url);

  const stepState = (index: number, failedHere = false): TimelineStep["state"] => {
    if (failedHere) return "failed";
    if (shown === "COMPLETED" || r > index) return "done";
    return r === index ? "current" : "todo";
  };

  const steps: TimelineStep[] = [
    {
      key: "create",
      title: "PayPal order created",
      state: stepState(1),
      detail: order ? (
        <span className="font-mono">
          {order.public_order_id} · {order.paypal_order_id ?? "pending"}
        </span>
      ) : (
        "Orders v2 · intent CAPTURE · itemized breakdown"
      ),
    },
    {
      key: "approve",
      title: "Buyer approval",
      state: r >= 2 ? "done" : r === 1 ? "current" : "todo",
      detail: simulated ? (
        "Auto-approved in sandbox simulation"
      ) : approvalUrl ? (
        <a
          href={approvalUrl}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 font-medium text-brand-700 hover:underline"
        >
          Open PayPal Sandbox checkout <ExternalLink className="size-3" aria-hidden />
        </a>
      ) : order?.approval_url ? (
        <span title={order.approval_url}>Approval link withheld: not an http(s) URL</span>
      ) : (
        "Buyer approves on PayPal"
      ),
    },
    {
      key: "capture",
      title: "Funds captured · stock deducted",
      state: stepState(2),
      detail: order?.paypal_capture_id ? (
        <span className="font-mono">{order.paypal_capture_id}</span>
      ) : (
        "Verified: amount + currency must match the ledger"
      ),
    },
    {
      key: "payouts",
      title: "Split payouts dispatched",
      state: stepState(3, shown === "PAYOUTS_PARTIAL_FAILED"),
      detail: order
        ? `${order.payouts.filter((p) => p.paypal_payout_batch_id).length}/${order.payouts.length} PayPal payout batches`
        : "One Payouts v1 batch per vendor",
    },
    {
      key: "done",
      title: "All vendors paid",
      state: shown === "COMPLETED" ? "done" : "todo",
      detail: order ? `${formatCents(order.total_vendor_payout_cents, currency)} disbursed` : "—",
    },
  ];

  return (
    <Panel
      step={3}
      title="PayPal checkout & split payouts"
      subtitle={simulated ? "Sandbox simulation (no credentials needed)" : "PayPal Sandbox"}
      active={active}
      testId="settlement-panel"
      className="h-full"
      actions={
        shown ? (
          <Pill
            tone={
              shown === "COMPLETED"
                ? "green"
                : shown === "PAYOUTS_PARTIAL_FAILED"
                  ? "red"
                  : "blue"
            }
          >
            <span data-testid="order-status">{STATUS_LABEL[shown]}</span>
          </Pill>
        ) : undefined
      }
    >
      <ol className="space-y-0" aria-label="Settlement timeline">
        {steps.map((step, i) => (
          <li key={step.key} className="relative flex gap-3 pb-4 last:pb-0">
            {i < steps.length - 1 && (
              <span
                aria-hidden
                className={`absolute left-[11px] top-6 h-[calc(100%-16px)] w-0.5 ${
                  step.state === "done" ? "bg-emerald-300" : "bg-slate-200"
                }`}
              />
            )}
            <span
              aria-hidden
              className={`relative z-10 mt-0.5 grid size-6 shrink-0 place-items-center rounded-full text-[11px] font-bold transition-all duration-300 ${
                step.state === "done"
                  ? "bg-emerald-500 text-white"
                  : step.state === "current"
                    ? "bg-brand-600 text-white ring-4 ring-brand-100"
                    : step.state === "failed"
                      ? "bg-rose-500 text-white ring-4 ring-rose-100"
                      : "bg-slate-200 text-slate-500"
              }`}
            >
              {step.state === "done" ? "✓" : step.state === "failed" ? "!" : i + 1}
            </span>
            <div className="min-w-0">
              <p
                className={`text-sm font-medium ${
                  step.state === "todo" ? "text-slate-400" : "text-slate-800"
                }`}
              >
                {step.title}
              </p>
              <p className="truncate text-xs text-slate-500">{step.detail}</p>
            </div>
          </li>
        ))}
      </ol>

      {order && order.payouts.length > 0 && (
        <div className="mt-4 space-y-1.5" data-testid="payout-list">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Vendor payouts
          </p>
          {order.payouts.map((p) => (
            <div
              key={p.id}
              className="flex items-center justify-between gap-2 rounded-lg border border-slate-200 px-3 py-2"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-slate-800">{p.vendor_name}</p>
                <p className="truncate font-mono text-[11px] text-slate-500">
                  {p.paypal_payout_batch_id ?? p.sender_batch_id}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <span className="font-mono text-sm font-semibold tabular-nums text-slate-800">
                  {formatCents(p.payout_amount_cents, p.currency)}
                </span>
                <Pill tone={PAYOUT_TONE[p.status]} title={p.error_message ?? undefined}>
                  <span data-testid={`payout-status-${p.vendor_id}`}>{p.status}</span>
                </Pill>
              </div>
            </div>
          ))}
        </div>
      )}

      {order?.payout_hold_reason && (
        <div className="mt-3 flex gap-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
          <span>
            <strong>Payouts on hold:</strong> {order.payout_hold_reason}
          </span>
        </div>
      )}

      {shown === "COMPLETED" && order && (
        <div
          className="mt-3 flex gap-2 rounded-lg bg-emerald-50 px-3 py-2.5 text-sm text-emerald-900"
          data-testid="settled-banner"
        >
          <BadgeCheck className="mt-0.5 size-5 shrink-0" aria-hidden />
          <span>
            Settled. {order.payouts.length} vendors paid{" "}
            <strong>{formatCents(order.total_vendor_payout_cents, currency)}</strong>; platform
            earned <strong>{formatCents(order.total_platform_fee_cents, currency)}</strong>.
          </span>
        </div>
      )}

      <div className="mt-auto space-y-2 pt-5">
        {!order && (
          <>
            <Button
              variant="paypal"
              className="w-full"
              onClick={onCreateOrder}
              loading={busy === "creating"}
              disabled={billable === 0 || blocked}
              icon={<Wallet className="size-4" aria-hidden />}
            >
              Create PayPal Order
              {preview && billable > 0 && ` · ${formatCents(preview.totals.subtotal_cents, currency)}`}
            </Button>
            <p className="text-center text-[11px] text-slate-500">
              <ShieldCheck className="mr-1 inline size-3" aria-hidden />
              Idempotency-Key <span className="font-mono">{idempotencyKey.slice(0, 15)}…</span>
              {excluded > 0 && ` · ${excluded} unresolved line${excluded === 1 ? "" : "s"} excluded`}
            </p>
          </>
        )}
        {order?.status === "PAYPAL_ORDER_CREATED" && (
          <Button
            className="w-full"
            onClick={onCapture}
            loading={busy === "capturing"}
            disabled={blocked}
            icon={<CircleDollarSign className="size-4" aria-hidden />}
          >
            Approve & Capture + Trigger Split Payouts
          </Button>
        )}
        {order && (order.status === "PAYOUTS_PARTIAL_FAILED" || failed) && (
          <Button
            variant="danger"
            className="w-full"
            onClick={onRetry}
            loading={busy === "retrying"}
            disabled={blocked}
            icon={<RotateCcw className="size-4" aria-hidden />}
          >
            Retry Failed Payouts
          </Button>
        )}
        {order?.status === "PAYOUTS_PENDING" && !failed && (
          <Button
            variant="secondary"
            className="w-full"
            onClick={onRetry}
            loading={busy === "retrying"}
            disabled={blocked}
            icon={<RefreshCw className="size-4" aria-hidden />}
          >
            Sync payout status
          </Button>
        )}
        {order && (
          <Button
            variant="ghost"
            className="w-full"
            onClick={onReset}
            icon={<Send className="size-4" aria-hidden />}
          >
            Start a new order
          </Button>
        )}
      </div>
    </Panel>
  );
}
