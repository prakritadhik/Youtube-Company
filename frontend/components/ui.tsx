"use client";

import { ReactNode, useEffect, useId, useRef } from "react";
import type { Tone } from "@/lib/types";
import { titleCase } from "@/lib/format";

const toneClasses: Record<Tone, string> = {
  neutral: "bg-surface2 text-muted",
  ok: "bg-ok-soft text-ok",
  warn: "bg-warn-soft text-warn",
  danger: "bg-danger-soft text-danger",
  info: "bg-info-soft text-info",
  accent: "bg-accent-soft text-accent",
};

export function Badge({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap ${toneClasses[tone]}`}>
      {children}
    </span>
  );
}

const statusTone: Record<string, Tone> = {
  COMPLETED: "ok", PASS: "ok", VERIFIED: "ok", APPROVED: "ok", ACTIVE_INSTRUCTION: "ok", RUNNING: "info", QUEUED: "neutral",
  SUBMITTED: "info", WAITING_FOR_APPROVAL: "warn", WAITING_FOR_DEPENDENCY: "neutral", RETRY_SCHEDULED: "warn",
  REVISION_REQUESTED: "warn", BLOCKED: "danger", FAILED: "danger", CANCELLED: "neutral", REJECTED: "danger",
  EXPIRED: "neutral", PENDING: "warn", NEEDS_REVISION: "warn", HUMAN_REVIEW_REQUIRED: "warn", NOT_REVIEWED: "neutral",
  UNDER_INVESTIGATION: "info", OBSERVED: "neutral", APPROVED_FOR_TESTING: "accent", CREATED: "neutral",
};
export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={statusTone[status] ?? "neutral"}>{titleCase(status.toLowerCase())}</Badge>;
}

const sevTone: Record<string, Tone> = { critical: "danger", high: "danger", medium: "warn", low: "info", info: "neutral" };
export function Severity({ level }: { level: string }) {
  return <Badge tone={sevTone[level] ?? "neutral"}>{level}</Badge>;
}

export function Card({ title, subtitle, action, children, className = "", id }: {
  title?: ReactNode; subtitle?: ReactNode; action?: ReactNode; children: ReactNode; className?: string; id?: string;
}) {
  return (
    <section id={id} className={`rounded-2xl border border-line bg-surface p-5 ${className}`}>
      {(title || action) && (
        <header className="mb-4 flex items-start justify-between gap-3">
          <div>
            {title && <h2 className="text-base font-semibold tracking-tight">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-sm text-muted">{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, hint, tone = "neutral" }: { label: string; value: ReactNode; hint?: ReactNode; tone?: Tone }) {
  return (
    <div className="rounded-2xl border border-line bg-surface p-4">
      <div className="min-h-8 text-xs font-medium uppercase leading-4 tracking-wide text-muted">{label}</div>
      <div className={`mt-1.5 text-3xl font-semibold tabular-nums ${tone === "danger" ? "text-danger" : tone === "warn" ? "text-warn" : ""}`}>{value}</div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </div>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
      <div className="max-w-3xl">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

type BtnVariant = "primary" | "secondary" | "danger" | "ghost";
const btn: Record<BtnVariant, string> = {
  primary: "bg-accent text-white hover:opacity-90",
  secondary: "border border-line bg-surface hover:bg-surface2",
  danger: "bg-danger text-white hover:opacity-90",
  ghost: "hover:bg-surface2",
};
export function Button({ variant = "secondary", busy, children, className = "", ...rest }: {
  variant?: BtnVariant; busy?: boolean; children: ReactNode; className?: string;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...rest}
      disabled={rest.disabled || busy}
      className={`inline-flex items-center justify-center gap-2 rounded-xl px-3.5 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${btn[variant]} ${className}`}
    >
      {busy && <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" aria-hidden />}
      {children}
    </button>
  );
}

export function Unavailable({ title, reason }: { title: string; reason?: string }) {
  return (
    <div className="rounded-xl border border-dashed border-line bg-surface2/50 p-4" role="note">
      <div className="text-sm font-medium">{title}</div>
      <div className="mt-0.5 text-sm text-muted">{reason ?? "Not available."} <span className="whitespace-nowrap">Shown as unavailable, never as zero.</span></div>
    </div>
  );
}

export function Empty({ title, hint, action }: { title: string; hint?: ReactNode; action?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-line px-4 py-8 text-center">
      <div className="text-sm font-medium">{title}</div>
      {hint && <div className="mx-auto mt-1 max-w-md text-sm text-muted">{hint}</div>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}

export function ErrorNote({ error }: { error: { message: string } | null }) {
  if (!error) return null;
  return <div role="alert" className="rounded-xl bg-danger-soft px-4 py-3 text-sm text-danger">{error.message}</div>;
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return <div className="py-10 text-center text-sm text-muted" role="status">{label}</div>;
}

export function Banner({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <div role="status" className={`rounded-xl px-4 py-3 text-sm ${toneClasses[tone]}`}>{children}</div>;
}

export function Table({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`overflow-x-auto ${className}`}><table className="w-full min-w-[32rem] text-left text-sm">{children}</table></div>;
}
export const Th = ({ children, className = "" }: { children?: ReactNode; className?: string }) => (
  <th className={`border-b border-line px-3 py-2 text-xs font-medium uppercase tracking-wide text-muted ${className}`}>{children}</th>
);
export const Td = ({ children, className = "", colSpan }: { children?: ReactNode; className?: string; colSpan?: number }) => (
  <td colSpan={colSpan} className={`border-b border-line/60 px-3 py-2.5 align-top ${className}`}>{children}</td>
);

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-sm font-medium">{label}</label>
      <div id={id}>{children}</div>
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </div>
  );
}

export const inputCls =
  "w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm placeholder:text-muted/70 focus:border-accent";

export function Drawer({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    ref.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onClick={onClose}>
      <div
        ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label={title}
        onClick={(e) => e.stopPropagation()}
        className="h-full w-full max-w-xl overflow-y-auto border-l border-line bg-surface p-6 shadow-xl outline-none"
      >
        <div className="mb-4 flex items-start justify-between gap-3">
          <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
          <Button variant="ghost" onClick={onClose} aria-label="Close">✕</Button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function ConfirmDialog({ open, title, body, confirmLabel, danger, busy, onConfirm, onCancel, children }: {
  open: boolean; title: string; body: ReactNode; confirmLabel: string; danger?: boolean; busy?: boolean;
  onConfirm: () => void; onCancel: () => void; children?: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onCancel}>
      <div role="alertdialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md rounded-2xl border border-line bg-surface p-6 shadow-xl">
        <h2 className="text-lg font-semibold">{title}</h2>
        <div className="mt-2 text-sm text-muted">{body}</div>
        {children && <div className="mt-4">{children}</div>}
        <div className="mt-6 flex justify-end gap-2">
          <Button onClick={onCancel}>Cancel</Button>
          <Button variant={danger ? "danger" : "primary"} busy={busy} onClick={onConfirm}>{confirmLabel}</Button>
        </div>
      </div>
    </div>
  );
}

export function KeyValue({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-[10rem_1fr]">
      {items.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted">{k}</dt>
          <dd className="min-w-0 break-words">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function List({ items, empty }: { items: ReactNode[]; empty?: string }) {
  if (!items.length) return <p className="text-sm text-muted">{empty ?? "None."}</p>;
  return <ul className="space-y-1.5 text-sm">{items.map((it, i) => <li key={i} className="flex gap-2"><span aria-hidden className="mt-2 h-1 w-1 shrink-0 rounded-full bg-muted" /><span className="min-w-0">{it}</span></li>)}</ul>;
}

export function JsonBlock({ value }: { value: unknown }) {
  return <pre className="max-h-96 overflow-auto rounded-xl bg-surface2 p-3 font-mono text-xs leading-relaxed">{JSON.stringify(value, null, 2)}</pre>;
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: string }[]; value: T; onChange: (t: T) => void }) {
  return (
    <div role="tablist" className="mb-4 flex flex-wrap gap-1 rounded-xl bg-surface2 p-1">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={value === t.id} onClick={() => onChange(t.id)}
          className={`rounded-lg px-3 py-1.5 text-sm font-medium transition ${value === t.id ? "bg-surface shadow-sm" : "text-muted hover:text-ink"}`}>
          {t.label}
        </button>
      ))}
    </div>
  );
}
