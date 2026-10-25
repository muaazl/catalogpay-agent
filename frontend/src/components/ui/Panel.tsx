import type { ReactNode } from "react";

interface PanelProps {
  step?: number;
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  active?: boolean;
  testId?: string;
}

/** A numbered card. The step numbers walk judges through the zero-friction flow. */
export function Panel({ step, title, subtitle, actions, children, className = "", active, testId }: PanelProps) {
  return (
    <section
      data-testid={testId}
      className={`flex min-w-0 flex-col rounded-2xl border bg-white shadow-sm transition-shadow ${
        active ? "border-brand-300 shadow-brand-100 ring-2 ring-brand-100" : "border-slate-200"
      } ${className}`}
    >
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 px-5 py-4">
        <div className="flex min-w-0 items-start gap-3">
          {step !== undefined && (
            <span
              aria-hidden
              className={`mt-0.5 grid size-7 shrink-0 place-items-center rounded-full text-sm font-bold ${
                active ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-600"
              }`}
            >
              {step}
            </span>
          )}
          <div className="min-w-0">
            <h2 className="text-[15px] font-semibold text-slate-900">{title}</h2>
            {subtitle && <p className="mt-0.5 text-[13px] text-slate-500">{subtitle}</p>}
          </div>
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </header>
      <div className="flex min-h-0 flex-1 flex-col p-5">{children}</div>
    </section>
  );
}
