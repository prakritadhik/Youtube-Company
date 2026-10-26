"use client";

import Link from "next/link";
import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { Badge, Banner, Button, Card, ErrorNote, inputCls, Loading } from "@/components/ui";
import type { Tone } from "@/lib/types";
import { fmtDate } from "@/lib/format";

interface Cred { name: string; label: string; secret: boolean; set: boolean; source: "dashboard" | "env" | null; shown: string | null; problem: string | null; updated_at: string | null }
interface TestInfo { label: string; path: string; note: string; last: null | { ok: boolean; detail: string; at: string; tier?: string | null; characters_used?: number | null; character_limit?: number | null } }
interface Service {
  id: string; name: string; kind: string; summary: string; state: "connected" | "partial" | "needs_setup" | "manual"; state_detail: string;
  credentials: Cred[]; capabilities: { id: string; description: string; state: string; detail: string; implemented: boolean }[]; attested: boolean;
  test: TestInfo | null; steps: string[]; links?: { label: string; url: string }[]; open?: string; program?: string | null;
}

const STATE: Record<Service["state"], { tone: Tone; label: string }> = {
  connected: { tone: "ok", label: "Connected" }, partial: { tone: "warn", label: "Half way" }, needs_setup: { tone: "warn", label: "Not connected" }, manual: { tone: "neutral", label: "Manual" },
};

function CredentialField({ c, onChanged }: { c: Cred; onChanged: () => void }) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<{ message: string } | null>(null);
  const act = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key); setErr(null);
    try { await fn(); setValue(""); onChanged(); } catch (e) { setErr(e as Error); } finally { setBusy(null); }
  };
  return (
    <div className="rounded-xl border border-line p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium">{c.label}</span>
        <span className="flex items-center gap-2 text-sm">
          {c.set ? <Badge tone="ok">{c.source === "dashboard" ? "Saved in the dashboard" : "Set in .env"}{c.shown ? ` · ${c.shown}` : ""}</Badge> : <Badge tone="warn">Not set</Badge>}
        </span>
      </div>
      {c.problem && <p className="mt-2 text-sm text-danger">{c.problem}</p>}
      <form className="mt-3 flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); act("save", () => api(`/api/connections/credentials/${c.name}`, { method: "PUT", body: { value } })); }}>
        <input className={`${inputCls} min-w-0 flex-1`} type={c.secret ? "password" : "text"} autoComplete="off" spellCheck={false} value={value}
          onChange={(e) => setValue(e.target.value)} placeholder={c.set ? "Paste a new value to replace it" : "Paste it here"} aria-label={c.label} />
        <Button type="submit" variant="primary" busy={busy === "save"} disabled={!value.trim()}>Save</Button>
        {c.source === "dashboard" && <Button type="button" busy={busy === "remove"} onClick={() => act("remove", () => api(`/api/connections/credentials/${c.name}`, { method: "DELETE" }))}>Remove</Button>}
      </form>
      {c.secret && <p className="mt-1 text-xs text-muted">Stored encrypted on this computer. It is never shown again, only its last four characters.</p>}
      <ErrorNote error={err} />
    </div>
  );
}

function ServiceCard({ s, onChanged }: { s: Service; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string } | null>(null);
  const last = s.test?.last;
  const run = async () => {
    if (!s.test) return;
    setBusy(true); setErr(null);
    try { await api(s.test.path, { method: "POST", body: {} }); onChanged(); } catch (e) { setErr(e as Error); } finally { setBusy(false); }
  };
  const st = STATE[s.state];
  return (
    <Card title={s.name} subtitle={s.summary} action={<Badge tone={st.tone}>{st.label}</Badge>}>
      <p className="mb-4 text-sm">{s.state_detail}</p>
      {s.credentials.length > 0 && <div className="mb-4 space-y-3">{s.credentials.map((c) => <CredentialField key={c.name} c={c} onChanged={onChanged} />)}</div>}
      {s.test && (
        <div className="mb-4">
          <div className="flex flex-wrap items-center gap-3">
            <Button busy={busy} onClick={run} disabled={s.credentials.some((c) => !c.set)}>{s.test.label}</Button>
            <span className="text-xs text-muted">{s.test.note}</span>
          </div>
          <ErrorNote error={err} />
          {last && (
            <div className="mt-3"><Banner tone={last.ok ? "ok" : "danger"}>
              <strong>{last.ok ? "Passed" : "Failed"}</strong> · {fmtDate(last.at)} · {last.detail}
              {last.ok && last.tier && <> Plan: {last.tier}.</>}
              {last.ok && last.characters_used != null && last.character_limit != null && <> Characters used: {last.characters_used.toLocaleString()} of {last.character_limit.toLocaleString()}.</>}
            </Banner></div>
          )}
        </div>
      )}
      {s.capabilities.length > 0 && (
        <ul className="mb-4 space-y-1.5 text-sm">
          {s.capabilities.map((c) => (
            <li key={c.id} className="flex flex-wrap items-start justify-between gap-2">
              <span>{c.description}{c.state !== "ready" && c.implemented && c.detail !== s.state_detail && <span className="block text-xs text-muted">{c.detail}</span>}</span>
              <Badge tone={c.state === "ready" ? "ok" : c.implemented ? "warn" : "neutral"}>{c.state === "ready" ? "Ready" : c.implemented ? "Not ready" : "Not built yet"}</Badge>
            </li>
          ))}
        </ul>
      )}
      <details className="text-sm">
        <summary className="cursor-pointer font-medium">How to connect it</summary>
        <ol className="mt-2 list-decimal space-y-1 pl-5">{s.steps.map((t, i) => <li key={i}>{t}</li>)}</ol>
        {s.links && <p className="mt-2 flex flex-wrap gap-3">{s.links.map((l) => <a key={l.url} className="text-accent hover:underline" href={l.url} target="_blank" rel="noreferrer noopener">{l.label} ↗</a>)}</p>}
      </details>
      {s.open && <p className="mt-3 text-sm"><Link className="text-accent hover:underline" href={s.open}>{s.id === "claude-code" ? "Open the AI Brain page" : "Open Settings"} →</Link></p>}
    </Card>
  );
}

export function ConnectionsList() {
  const { data, error, reload } = useApi<{ services: Service[] }>("/api/connections", { refreshMs: 10000 });
  if (error && !data) return <ErrorNote error={error} />;
  if (!data) return <Loading />;
  return <div className="space-y-6">{data.services.map((s) => <ServiceCard key={s.id} s={s} onChanged={reload} />)}</div>;
}

