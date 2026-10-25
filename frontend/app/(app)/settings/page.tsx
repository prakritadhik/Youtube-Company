"use client";

import { FormEvent, useState } from "react";
import { api, useApi } from "@/lib/api";
import { Badge, Banner, Button, Card, ConfirmDialog, ErrorNote, Field, inputCls, Loading, PageHeader } from "@/components/ui";
import { fmtDate } from "@/lib/format";

interface Settings {
  settings: {
    operating_mode: string;
    budget: Record<string, number>;
    autonomy: { comment_reply_policy: string; autopublish_channels: string[]; max_publishes_per_day_per_channel: number };
    emergency_pause: { active: boolean; since: string | null; by: string | null; reason: string | null };
  };
  notes: Record<string, string>;
}
interface Channel { slug: string; name: string; kind: string }

const MODES: { id: string; title: string; text: string }[] = [
  { id: "DEVELOPMENT", title: "Development", text: "Real publishing and public replies are disabled. Safe for building and testing." },
  { id: "APPROVAL_FIRST", title: "Approval-first", text: "Videos are produced and prepared, then wait for your approval before anything is published." },
  { id: "CONTROLLED_AUTONOMY", title: "Controlled autonomy", text: "Channels you list below may publish cleared videos without per-video approval. Medical review, rights gates and budgets always apply." },
  { id: "PAUSED", title: "Paused", text: "No new publishing or external actions are started." },
];
const BUDGET_FIELDS: [string, string, string][] = [
  ["daily_usd", "Daily spend limit (USD)", "0 = no paid operations permitted"],
  ["monthly_usd", "Monthly spend limit (USD)", ""],
  ["per_video_usd", "Per-video cost limit (USD)", ""],
  ["daily_provider_requests", "Provider requests per day", "Counts every paid or subscription-metered call"],
  ["daily_model_requests", "Language-model requests per day", ""],
  ["daily_image_generations", "Image generations per day", ""],
  ["daily_audio_generations", "Audio generations per day", ""],
  ["max_task_attempts", "Maximum attempts per task", "Bounds automatic retries"],
  ["max_concurrent_tasks", "Concurrent tasks", ""],
];

export default function SettingsPage() {
  const s = useApi<Settings>("/api/settings");
  const ch = useApi<Channel[]>("/api/channels");
  const [mode, setMode] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<{ message: string } | null>(null);
  const [budget, setBudget] = useState<Record<string, string>>({});
  const [resumeNote, setResumeNote] = useState("");
  const [pw, setPw] = useState({ current: "", next: "" });
  const [nu, setNu] = useState({ username: "", password: "" });

  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true); setErr(null); setMsg(null);
    try { await fn(); setMsg(ok); await s.reload(); } catch (e) { setErr(e as Error); } finally { setBusy(false); }
  };

  if (s.loading && !s.data) return <Loading />;
  if (s.error && !s.data) return <ErrorNote error={s.error} />;
  if (!s.data) return null;
  const cfg = s.data.settings;
  const bval = (k: string) => budget[k] ?? String(cfg.budget[k]);

  const saveBudget = (e: FormEvent) => {
    e.preventDefault();
    const value = Object.fromEntries(BUDGET_FIELDS.map(([k]) => [k, Number(bval(k))]));
    run(() => api("/api/settings/budget", { method: "PUT", body: { value } }), "Budget limits saved.");
  };
  const toggleChannel = (slug: string) => {
    const cur = cfg.autonomy.autopublish_channels;
    const next = cur.includes(slug) ? cur.filter((x) => x !== slug) : [...cur, slug];
    run(() => api("/api/settings/autonomy", { method: "PUT", body: { value: { autopublish_channels: next } } }), "Autonomy channels updated.");
  };

  return (
    <>
      <PageHeader title="Settings & control" description="Owner-controlled. The AI CEO cannot change anything on this page." />
      <ErrorNote error={err} />
      {msg && <div className="mb-4"><Banner tone="ok">{msg}</Banner></div>}

      <div className="space-y-6">
        <Card title="Emergency control" subtitle="Pause stops new tasks, paid generation, uploads and automated replies.">
          {cfg.emergency_pause.active ? (
            <div className="space-y-3">
              <Banner tone="danger">Paused by {cfg.emergency_pause.by} on {fmtDate(cfg.emergency_pause.since)}. Reason: {cfg.emergency_pause.reason}</Banner>
              <Field label="Note (optional)"><input className={inputCls} value={resumeNote} onChange={(e) => setResumeNote(e.target.value)} /></Field>
              <Button variant="primary" busy={busy} onClick={() => run(() => api("/api/control/resume", { method: "POST", body: { note: resumeNote } }).then(() => window.dispatchEvent(new Event("control:changed"))), "Company resumed.")}>Resume company</Button>
            </div>
          ) : <p className="text-sm text-muted">Not paused. Use the “Emergency pause” button on the Overview page to stop everything immediately.</p>}
        </Card>

        <Card title="Operating mode" subtitle="Start in Development; use Approval-first when testing real integrations.">
          <div className="grid gap-3 md:grid-cols-2">
            {MODES.map((m) => (
              <button key={m.id} onClick={() => m.id !== cfg.operating_mode && setMode(m.id)} aria-pressed={cfg.operating_mode === m.id}
                className={`rounded-xl border p-4 text-left transition ${cfg.operating_mode === m.id ? "border-accent bg-accent-soft" : "border-line hover:border-accent"}`}>
                <div className="flex items-center justify-between"><span className="font-medium">{m.title}</span>{cfg.operating_mode === m.id && <Badge tone="accent">Current</Badge>}</div>
                <p className="mt-1 text-sm text-muted">{m.text}</p>
              </button>
            ))}
          </div>
        </Card>

        <Card title="Spending and usage limits" subtitle={s.data.notes.budget}>
          <form onSubmit={saveBudget} className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {BUDGET_FIELDS.map(([k, label, hint]) => (
              <Field key={k} label={label} hint={hint || undefined}>
                <input type="number" min={0} step="any" className={inputCls} value={bval(k)} onChange={(e) => setBudget({ ...budget, [k]: e.target.value })} aria-label={label} />
              </Field>
            ))}
            <div className="md:col-span-2 xl:col-span-3"><Button variant="primary" type="submit" busy={busy}>Save limits</Button></div>
          </form>
        </Card>

        <Card title="Autonomy" subtitle="Applies only in Controlled autonomy mode.">
          <p className="mb-3 text-sm">Comment replies: <Badge>{cfg.autonomy.comment_reply_policy === "draft_only" ? "Draft only (recommended)" : "Auto-reply to low-risk comments"}</Badge></p>
          <h3 className="mb-2 text-sm font-medium">Channels allowed to publish without per-video approval</h3>
          <div className="space-y-2">
            {(ch.data ?? []).map((c) => {
              const medicine = c.kind === "medicine";
              return (
                <label key={c.slug} className="flex items-center gap-3 text-sm">
                  <input type="checkbox" disabled={medicine || busy} checked={cfg.autonomy.autopublish_channels.includes(c.slug)} onChange={() => toggleChannel(c.slug)} />
                  <span>{c.name}</span>{medicine && <Badge tone="warn">Always needs human medical review</Badge>}
                </label>
              );
            })}
          </div>
        </Card>

        <div className="grid gap-6 lg:grid-cols-2">
          <Card title="Add a read-only viewer" subtitle="Viewers see the executive layer only.">
            <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); run(() => api("/api/auth/users", { method: "POST", body: { ...nu, role: "viewer" } }).then(() => setNu({ username: "", password: "" })), "Viewer added."); }}>
              <Field label="Username"><input className={inputCls} value={nu.username} onChange={(e) => setNu({ ...nu, username: e.target.value })} required minLength={3} /></Field>
              <Field label="Password" hint="12+ characters, mixed case, with a digit"><input type="password" className={inputCls} value={nu.password} onChange={(e) => setNu({ ...nu, password: e.target.value })} required autoComplete="new-password" /></Field>
              <Button type="submit" busy={busy}>Add viewer</Button>
            </form>
          </Card>
          <Card title="Change your password" subtitle="All sessions are signed out afterwards.">
            <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); run(() => api("/api/auth/change-password", { method: "POST", body: { current_password: pw.current, new_password: pw.next } }).then(() => window.dispatchEvent(new Event("auth:expired"))), "Password changed."); }}>
              <Field label="Current password"><input type="password" className={inputCls} value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} required autoComplete="current-password" /></Field>
              <Field label="New password"><input type="password" className={inputCls} value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} required autoComplete="new-password" /></Field>
              <Button type="submit" busy={busy}>Change password</Button>
            </form>
          </Card>
        </div>
      </div>

      <ConfirmDialog open={!!mode} title={`Switch to ${MODES.find((m) => m.id === mode)?.title}?`} busy={busy}
        body={MODES.find((m) => m.id === mode)?.text} confirmLabel="Switch mode"
        onCancel={() => setMode(null)}
        onConfirm={async () => { await run(() => api("/api/settings/operating_mode", { method: "PUT", body: { value: mode } }), "Operating mode updated."); setMode(null); }} />
    </>
  );
}
