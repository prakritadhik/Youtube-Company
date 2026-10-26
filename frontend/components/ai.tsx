"use client";

import Link from "next/link";
import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { Badge, Banner, Button, Card, Drawer, Empty, ErrorNote, Field, inputCls, Loading, StatusBadge, Table, Td, Th } from "@/components/ui";
import type { Tone, VideoProgress } from "@/lib/types";
import { fmtDate } from "@/lib/format";

export interface AiStatus {
  brain: { ready: boolean; code: string; detail: string };
  capability: { state: string; detail: string };
  settings: { model: string; effort: string; timeout_seconds: number };
  program: { found: string | null; configured_path: boolean };
  allowance: { daily_model_requests: number; daily_provider_requests: number; used_today: number };
  attested: boolean;
  test: { state: "idle" | "running" | "done"; started_at: string | null; report: DoctorReport | null };
  last_test: null | { id: number; at: string; status: string; error: string | null; model: string | null; report: DoctorReport | null };
  notes: string[];
}
export interface DoctorReport {
  ok: boolean; installed: boolean; path: string | null; version: string | null; supported: boolean; signed_in: boolean | null; tool_free: boolean | null;
  model: string | null; duration_ms: number | null; problems: string[]; api_key_in_environment_ignored?: boolean;
}
export interface AiCall {
  id: number; at: string; purpose: string; role: string; task_id: number | null; video_id: number | null; attempt: number; status: string;
  error_code: string | null; error: string | null; model: string | null; input_tokens: number | null; output_tokens: number | null;
  list_price_usd: number | null; duration_ms: number | null;
}

const PURPOSE: Record<string, string> = {
  "brief.draft": "Wrote a creative brief", "script.draft": "Wrote lyrics / a script", "ideas.propose": "Proposed video ideas",
  "review.brief": "Manager reviewed a brief", "review.script": "Manager reviewed lyrics / a script", doctor: "Connection test",
};
export const purposeLabel = (p: string) => PURPOSE[p] ?? p;
const callTone: Record<string, Tone> = { OK: "ok", INVALID: "warn", FAILED: "danger" };

const WHO: Record<string, { tone: Tone; label: string }> = {
  company: { tone: "info", label: "The company is working on this" },
  you: { tone: "warn", label: "Waiting for you" },
  nobody: { tone: "neutral", label: "Nothing more to do here" },
};

/** Where a video started with the AI company is, and who has to move next. Shown only for such videos. */
export function ProgressBanner({ progress }: { progress?: VideoProgress }) {
  if (!progress?.conducted || !progress.who) return null;
  const w = WHO[progress.who];
  return (
    <div className="mb-6">
      <Banner tone={w.tone}><strong>{w.label}.</strong> {progress.detail}</Banner>
    </div>
  );
}

export function CallDrawer({ id, onClose }: { id: number | null; onClose: () => void }) {
  const { data, error } = useApi<{ id: number; purpose: string; status: string; error: string | null; record: null | { prompt: string; response: string | null; problems: string[] } }>(
    id ? `/api/ai/generations/${id}` : null);
  return (
    <Drawer open={id !== null} onClose={onClose} title={id ? `Call #${id}` : ""}>
      {error && <ErrorNote error={error} />}
      {!data && !error && <Loading />}
      {data && (
        <div className="space-y-4 text-sm">
          <div className="flex flex-wrap items-center gap-2"><Badge tone={callTone[data.status] ?? "neutral"}>{data.status}</Badge><span>{purposeLabel(data.purpose)}</span></div>
          {data.error && <p className="rounded-xl bg-danger-soft p-3 text-danger">{data.error}</p>}
          {data.record?.problems && data.record.problems.length > 0 && (
            <div><h3 className="mb-1 font-medium">Problems found in the answer</h3><ul className="list-disc pl-5">{data.record.problems.map((p, i) => <li key={i}>{p}</li>)}</ul></div>
          )}
          {data.record ? (
            <>
              <div><h3 className="mb-1 font-medium">Exactly what was sent</h3><pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-xl bg-surface2 p-3 font-mono text-xs">{data.record.prompt}</pre></div>
              <div><h3 className="mb-1 font-medium">What came back</h3><pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-xl bg-surface2 p-3 font-mono text-xs">{data.record.response ?? "(nothing)"}</pre></div>
            </>
          ) : <p className="text-muted">The saved record of this call is not on disk.</p>}
        </div>
      )}
    </Drawer>
  );
}

export function ActivityTable({ limit = 25 }: { limit?: number }) {
  const { data, error, loading } = useApi<{ today: Record<string, number>; calls: AiCall[] }>(`/api/ai/activity?limit=${limit}`, { refreshMs: 15000 });
  const [open, setOpen] = useState<number | null>(null);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorNote error={error} />;
  if (!data || data.calls.length === 0) return <Empty title="The brain has not been used yet" hint="Every call is listed here with what it was for, how it ended and what it cost in tokens." />;
  return (
    <>
      <Table>
        <thead><tr><Th>When</Th><Th>What</Th><Th>Role</Th><Th>Result</Th><Th>Tokens in / out</Th><Th>Took</Th><Th /></tr></thead>
        <tbody>
          {data.calls.map((c) => (
            <tr key={c.id}>
              <Td className="whitespace-nowrap">{fmtDate(c.at)}</Td>
              <Td>{purposeLabel(c.purpose)}{c.attempt > 1 && <span className="text-muted"> (correction {c.attempt - 1})</span>}
                {c.video_id && <> · <Link className="text-accent hover:underline" href={`/videos/${c.video_id}`}>video #{c.video_id}</Link></>}</Td>
              <Td className="text-muted">{c.role}</Td>
              <Td><Badge tone={callTone[c.status] ?? "neutral"}>{c.status === "OK" ? "Used" : c.status === "INVALID" ? "Sent back" : "Failed"}</Badge>
                {c.error && <div className="mt-1 max-w-xs text-xs text-muted">{c.error.slice(0, 160)}</div>}</Td>
              <Td className="tabular-nums">{c.input_tokens ?? "–"} / {c.output_tokens ?? "–"}</Td>
              <Td className="tabular-nums">{c.duration_ms ? `${(c.duration_ms / 1000).toFixed(1)} s` : "–"}</Td>
              <Td>{c.purpose !== "doctor" && <Button variant="ghost" onClick={() => setOpen(c.id)}>Open</Button>}</Td>
            </tr>
          ))}
        </tbody>
      </Table>
      <CallDrawer id={open} onClose={() => setOpen(null)} />
    </>
  );
}

function Check({ ok, title, detail }: { ok: boolean | null; title: string; detail?: React.ReactNode }) {
  return (
    <li className="flex gap-3">
      <span aria-hidden className={`mt-0.5 w-5 text-center ${ok ? "text-ok" : ok === false ? "text-danger" : "text-muted"}`}>{ok ? "✓" : ok === false ? "✕" : "•"}</span>
      <span><span className="font-medium">{title}</span>{detail && <span className="block text-muted">{detail}</span>}</span>
    </li>
  );
}

export function BrainCard() {
  const { data: s, error, reload } = useApi<AiStatus>("/api/ai/status", { refreshMs: 4000 });
  const [form, setForm] = useState<{ model: string; effort: string; timeout: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<{ message: string } | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  if (error && !s) return <ErrorNote error={error} />;
  if (!s) return <Card title="AI brain"><Loading /></Card>;

  const f = form ?? { model: s.settings.model, effort: s.settings.effort, timeout: String(s.settings.timeout_seconds) };
  const running = s.test.state === "running";
  const report = s.test.state === "done" ? s.test.report : s.last_test?.report ?? null;
  const act = async (key: string, fn: () => Promise<unknown>, ok?: string) => {
    setBusy(key); setErr(null); setMsg(null);
    try { await fn(); if (ok) setMsg(ok); await reload(); } catch (e) { setErr(e as Error); } finally { setBusy(null); }
  };
  const a = s.allowance;

  return (
    <div className="space-y-6">
      <Card title="Is the brain ready?" subtitle="Claude Code, running on this computer under your own Claude account. No API key is used."
        action={<Badge tone={s.brain.ready ? "ok" : "warn"}>{s.brain.ready ? "Ready" : "Not ready"}</Badge>}>
        <ErrorNote error={err} />
        {msg && <div className="mb-3"><Banner tone="ok">{msg}</Banner></div>}
        {!s.brain.ready && <div className="mb-4"><Banner tone="warn">{s.brain.detail}</Banner></div>}
        <ul className="space-y-3 text-sm">
          <Check ok={!!s.program.found} title="The Claude Code program is installed"
            detail={s.program.found ? <>Found at <code className="break-all">{s.program.found}</code></> : <>Not found. Install Claude Code, run <code>claude</code> once and sign in, or put its full path in <code>.env</code> as <code>YTC_CLAUDE_CODE_BIN</code>.</>} />
          <Check ok={report ? report.signed_in : null} title="It is signed in to your Claude account"
            detail={report ? (report.signed_in ? "The test question was answered." : report.problems.join(" ")) : "Run the connection test below to find out."} />
          <Check ok={report ? report.tool_free : null} title="It has no tools" detail="The company runs it with every tool switched off and in an empty folder, so it can only return text." />
          <Check ok={s.attested} title="You confirmed your plan allows this"
            detail={s.attested ? undefined : <>Tick the confirmation for “Claude Code on this computer” under Providers in <Link className="text-accent hover:underline" href="/settings">Settings</Link>. Check your own plan’s current terms first.</>} />
          <Check ok={a.daily_model_requests > 0 && a.daily_provider_requests > 0} title="You gave it a daily allowance"
            detail={a.daily_model_requests > 0 ? <>{a.used_today} used today of {a.daily_model_requests} model requests per day (and {a.daily_provider_requests} provider requests). A request is one call, including a correction round.</>
              : <>The limits are 0, which means “not permitted”. Set “Language-model requests per day” and “Provider requests per day” in <Link className="text-accent hover:underline" href="/settings">Settings</Link>. A video needs about 4 to 8 requests.</>} />
        </ul>
        <div className="mt-5 flex flex-wrap items-center gap-3">
          <Button variant="primary" busy={running || busy === "test"} onClick={() => act("test", () => api("/api/ai/test", { method: "POST", body: {} }))}>Run the connection test</Button>
          <span className="text-xs text-muted">Asks Claude Code one harmless question (it uses a small amount of your plan). Takes up to a minute.</span>
        </div>
        {report && (
          <div className="mt-4 rounded-xl border border-line p-4 text-sm">
            <div className="flex flex-wrap items-center gap-2"><Badge tone={report.ok ? "ok" : "danger"}>{report.ok ? "Test passed" : "Test failed"}</Badge>
              {report.version && <span className="text-muted">{report.version}</span>}{report.model && <span className="text-muted">· answered by {report.model}</span>}
              {report.duration_ms && <span className="text-muted">· {(report.duration_ms / 1000).toFixed(1)} s</span>}</div>
            {report.problems.length > 0 && <ul className="mt-2 list-disc pl-5 text-danger">{report.problems.map((p, i) => <li key={i}>{p}</li>)}</ul>}
            {report.api_key_in_environment_ignored && <p className="mt-2 text-muted">An API key was found in this computer’s settings. It is deliberately ignored so your plan is used, never that key.</p>}
          </div>
        )}
        <ul className="mt-5 list-disc space-y-1 pl-5 text-xs text-muted">{s.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
      </Card>

      <Card title="Model and effort" subtitle="Leave empty to use Claude Code’s own defaults.">
        <form className="grid gap-4 md:grid-cols-3" onSubmit={(e) => {
          e.preventDefault();
          act("save", () => api("/api/settings/ai", { method: "PUT", body: { value: { model: f.model.trim(), effort: f.effort, timeout_seconds: Number(f.timeout) } } }), "Saved.").then(() => setForm(null));
        }}>
          <Field label="Model" hint="A name or alias such as sonnet or opus"><input className={inputCls} value={f.model} onChange={(e) => setForm({ ...f, model: e.target.value })} placeholder="default" /></Field>
          <Field label="Effort" hint="Higher thinks longer and uses more of your plan">
            <select className={inputCls} value={f.effort} onChange={(e) => setForm({ ...f, effort: e.target.value })}>
              {["", "low", "medium", "high", "xhigh", "max"].map((x) => <option key={x} value={x}>{x || "default"}</option>)}
            </select></Field>
          <Field label="Give up on one call after (seconds)" hint="30 to 1800"><input type="number" min={30} max={1800} className={inputCls} value={f.timeout} onChange={(e) => setForm({ ...f, timeout: e.target.value })} /></Field>
          <div className="md:col-span-3"><Button variant="primary" type="submit" busy={busy === "save"}>Save</Button></div>
        </form>
      </Card>
    </div>
  );
}

export function AskForIdeas({ channels }: { channels: { slug: string; name: string }[] }) {
  const [channel, setChannel] = useState(channels[0]?.slug ?? "");
  const [count, setCount] = useState("3");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string } | null>(null);
  const [done, setDone] = useState<number | null>(null);
  const go = async () => {
    setBusy(true); setErr(null); setDone(null);
    try { const r = await api<{ task_id: number }>("/api/ai/ideas", { method: "POST", body: { channel, count: Number(count) } }); setDone(r.task_id); }
    catch (e) { setErr(e as Error); } finally { setBusy(false); }
  };
  return (
    <Card title="Ask for ideas" subtitle="The content strategist proposes ideas that fit the channel and what can be produced today. They arrive in the Content Plan as proposals; you choose which to make.">
      <ErrorNote error={err} />
      {done !== null && <div className="mb-3"><Banner tone="ok">Requested (task #{done}). The ideas appear in the <Link className="underline" href="/plan">Content Plan</Link> within a minute or two.</Banner></div>}
      <div className="grid gap-4 md:grid-cols-3">
        <Field label="Channel"><select className={inputCls} value={channel} onChange={(e) => setChannel(e.target.value)}>{channels.map((c) => <option key={c.slug} value={c.slug}>{c.name}</option>)}</select></Field>
        <Field label="How many"><select className={inputCls} value={count} onChange={(e) => setCount(e.target.value)}>{[1, 2, 3, 4, 5].map((n) => <option key={n}>{n}</option>)}</select></Field>
        <div className="flex items-end"><Button variant="primary" busy={busy} onClick={go}>Ask the AI</Button></div>
      </div>
    </Card>
  );
}

export { StatusBadge };
