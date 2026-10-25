import { Check } from "lucide-react";

import type { Stage } from "../hooks/useCommandCenter";

const STEPS: { stage: Stage; label: string }[] = [
  { stage: "intake", label: "Drop messy order" },
  { stage: "resolve", label: "Verify SKUs & splits" },
  { stage: "checkout", label: "PayPal checkout" },
  { stage: "settle", label: "Capture & split payouts" },
  { stage: "done", label: "Settled" },
];

export function FlowStepper({ stage }: { stage: Stage }) {
  const current = STEPS.findIndex((s) => s.stage === stage);
  return (
    <ol className="flex flex-wrap items-center gap-2 text-xs" aria-label="Order flow progress">
      {STEPS.map((step, i) => {
        const done = i < current || stage === "done";
        const isCurrent = i === current && stage !== "done";
        return (
          <li
            key={step.stage}
            className="flex items-center gap-2"
            aria-current={isCurrent ? "step" : undefined}
          >
            <span
              className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 font-medium transition-colors ${
                done
                  ? "bg-emerald-50 text-emerald-700"
                  : isCurrent
                    ? "bg-brand-600 text-white shadow-sm"
                    : "bg-slate-100 text-slate-500"
              }`}
            >
              {done ? (
                <Check className="size-3" aria-hidden />
              ) : (
                <span className="font-mono">{i + 1}</span>
              )}
              {step.label}
            </span>
            {i < STEPS.length - 1 && <span className="h-px w-4 bg-slate-300" aria-hidden />}
          </li>
        );
      })}
    </ol>
  );
}
