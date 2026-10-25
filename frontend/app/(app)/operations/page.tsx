"use client";

import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { useAuth } from "@/components/auth";
import { Badge, Banner, Button, Card, Empty, ErrorNote, inputCls, List, Loading, PageHeader, Table, Td, Th } from "@/components/ui";
import { fmtDate, titleCase } from "@/lib/format";
import type { CyclePhase, OpsAlert, OpsSchedule, OpsStatus, Tone } from "@/lib/types";

const sevTone: Record<string, Tone> = { critical: "danger", warning: "warn", info: "neutral" };
const phaseTone: Record<string, Tone> = { done: "ok", nothing_to_do: "neutral", unavailable: "warn", skipped: "neutral" };
const checkTone: Record<string, Tone> = { ok: "ok", warn: "warn", fail: "danger" };

function cadenceText(s: OpsSchedule): string {
  const c = s.cadence;
  if (c.kind === "interval") return `Every ${c.every_minutes} minutes`;
  if (c.kind === "daily") return `Daily at ${c.at} (${s.timezone})`;
  return `Weekly on ${(c.days ?? []).map((d) => ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d]).join(", ")} at ${c.at} (${s.timezone})`;
}

function ScheduleRow({ s, canWrite, onChanged, onError }: { s: OpsSchedule; canWrite: boolean; onChanged: () => void; onError: (e: { message: string }) => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [at, setAt] = useState(s.cadence.kind === "daily" ? s.cadence.at ?? "" : "");
  const act = async (key: string, fn: () => Promise<unknown>) => { setBusy(key); try { await fn(); onChanged(); } catch (e) { onError(e as Error); } finally { setBusy(null); } };
  return (
    <tr>
      <Td><div className="font-medium">{s.title}</div><div className="text-xs text-muted">{s.description}</div></Td>
      <Td className="whitespace-nowrap">{cadenceText(s)}</Td>
      <Td className="whitespace-nowrap">{s.enabled ? (s.due_now ? <Badge tone="warn">due now</Badge> : fmtDate(s.next_run)) : <Badge>off</Badge>}<div className="text-xs text-muted">last: {s.last_run_at ? fmtDate(s.last_run_at) : "never"}</div></Td>
      <Td>
        {canWrite && (
          <div className="flex flex-wrap items-center gap-1.5">
            <Button busy={busy === "run"} onClick={() => act("run", () => api(`/api/ops/schedules/${s.slug}/run-now`, { method: "POST", body: {} }))}>Run now</Button>
            <Button busy={busy === "toggle"} onClick={() => act("toggle", () => api(`/api/ops/schedules/${s.slug}/${s.enabled ? "disable" : "enable"}`, { method: "POST", body: {} }))}>{s.enabled ? "Turn off" : "Turn on"}</Button>
            {s.cadence.kind === "daily" && (<>
              <input aria-label={`Time for ${s.title}`} type="time" className={`${inputCls} w-28`} value={at} onChange={(e) => setAt(e.target.value)} />
              <Button busy={busy === "time"} disabled={at === s.cadence.at || !at} onClick={() => act("time", () => api(`/api/ops/schedules/${s.slug}`, { method: "PUT", body: { cadence: { kind: "daily", at } } }))}>Set</Button></>)}
          </div>
        )}
      </Td>
    </tr>
  );
}

function PhaseRow({ p }: { p: CyclePhase }) {
  const lists: [string, unknown][] = Object.entries(p).filter(([k, v]) => !["phase", "status", "summary"].includes(k) && Array.isArray(v) && (v as unknown[]).length > 0);
  return (
    <li className="rounded-xl border border-line p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2"><span className="font-medium">{titleCase(p.phase)}</span><Badge tone={phaseTone[p.status] ?? "neutral"}>{p.status.replace(/_/g, " ")}</Badge></div>
      <p className="mt-1 text-muted">{p.summary}</p>
      {lists.map(([k, v]) => (
        <details key={k} className="mt-1"><summary className="cursor-pointer text-xs font-medium">{titleCase(k.replace(/_/g, " "))} ({(v as unknown[]).length})</summary>
          <List items={(v as Record<string, unknown>[]).map((x) => <span key={JSON.stringify(x)}>{typeof x === "object" ? Object.entries(x).map(([a, b]) => `${a}: ${String(b)}`).join(" · ") : String(x)}</span>)} /></details>
      ))}
    </li>
  );
}

function AlertRow({ a, canWrite, onChanged, onError }: { a: OpsAlert; canWrite: boolean; onChanged: () => void; onError: (e: { message: string }) => void }) {
  const [busy, setBusy] = useState(false);
  const ack = async () => { setBusy(true); try { await api(`/api/ops/alerts/${a.id}/acknowledge`, { method: "POST", body: {} }); onChanged(); } catch (e) { onError(e as Error); } finally { setBusy(false); } };
  return (
    <li className="flex flex-wrap items-start justify-between gap-3 rounded-xl border border-line p-3 text-sm">
      <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><Badge tone={sevTone[a.severity]}>{a.severity}</Badge><span className="font-medium">{a.title}</span>
        {a.status === "ACKNOWLEDGED" && <Badge>acknowledged</Badge>}{a.count > 1 && <span className="text-xs text-muted">seen {a.count} times</span>}</div>
        {a.detail && a.detail !== a.title && <p className="mt-1 text-muted">{a.detail}</p>}
        <p className="mt-1 text-xs text-muted">since {fmtDate(a.first_seen)} · last {fmtDate(a.last_seen)}</p></div>
      {canWrite && a.status === "OPEN" && <Button busy={busy} onClick={ack}>Acknowledge</Button>}
    </li>
  );
}

export default function OperationsPage() {
  const { can } = useAuth();
  const { data, error, loading, reload } = useApi<OpsStatus>("/api/ops/status", { refreshMs: 20000 });
  const [err, setErr] = useState<{ message: string } | null>(null);
  const canWrite = can("settings:write");
  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorNote error={error} />;
  if (!data) return null;
  const cy = data.daily_cycle;
  return (
    <>
      <PageHeader title="Operations" description="Whether the company is running, what is scheduled, what needs your attention, and what the last daily cycle actually did." />
      <ErrorNote error={err} />
      <div className="space-y-6">
        <Card title="Worker" subtitle={data.worker.note}>
          {data.worker.alive ? <Banner tone="ok">The background worker is running.</Banner> : <Banner tone="warn">No worker has reported in the last two minutes. Queued and scheduled work is waiting. Start everything with <code>python launcher.py</code>.</Banner>}
          {data.worker.workers.length > 0 && <Table className="mt-3"><thead><tr><Th>Worker</Th><Th>Host</Th><Th>Started</Th><Th>Last beat</Th></tr></thead>
            <tbody>{data.worker.workers.map((w) => <tr key={w.worker_id}><Td className="font-medium">{w.worker_id} {w.alive ? <Badge tone="ok">alive</Badge> : <Badge>stopped</Badge>}</Td><Td>{w.host} · pid {w.pid}</Td><Td>{fmtDate(w.started_at)}</Td><Td>{w.seconds_since_beat}s ago</Td></tr>)}</tbody></Table>}
        </Card>

        <Card title="Alerts" subtitle="The same problem is one alert with a count. Alerts clear themselves when the check passes again; acknowledging only says you have seen it.">
          {data.alerts.length === 0 ? <Empty title="No open alerts" hint="Nothing needs your attention right now." /> :
            <ul className="space-y-2">{data.alerts.map((a) => <AlertRow key={a.id} a={a} canWrite={canWrite} onChanged={reload} onError={setErr} />)}</ul>}
          <p className="mt-3 text-xs text-muted">{data.notes[0]}</p>
        </Card>

        <Card title="Schedules" subtitle={`Read in ${data.timezone} (change the time zone in Settings). If the app was off, a schedule runs its most recent missed slot once, not every one.`}>
          <Table><thead><tr><Th>Job</Th><Th>When</Th><Th>Next run</Th><Th>{canWrite ? "Controls" : ""}</Th></tr></thead>
            <tbody>{data.schedules.map((s) => <ScheduleRow key={s.slug} s={s} canWrite={canWrite} onChanged={reload} onError={setErr} />)}
              {data.schedules.length === 0 && <tr><Td colSpan={4} className="text-muted">No schedules yet. They are created by <code>python -m app.cli setup</code>.</Td></tr>}</tbody></Table>
        </Card>

        <Card title="Last daily cycle" subtitle={cy ? `${fmtDate(cy.ran_at)} · ${cy.quiet ? "a quiet day: nothing needed doing" : `${cy.needs_attention} item(s) need you`}` : "It has not run yet."}>
          {!cy ? <Empty title="No daily cycle has run" hint="It runs every morning while the worker is running, or press Run now above." /> : <ul className="space-y-2">{cy.phases.map((p) => <PhaseRow key={p.phase} p={p} />)}</ul>}
        </Card>

        <Card title="Health checks" subtitle={data.health ? `Checked ${fmtDate(data.health.checked_at)}` : "No health check has run yet."}>
          {data.health && <Table><thead><tr><Th>Check</Th><Th>Result</Th><Th>Detail</Th></tr></thead>
            <tbody>{data.health.checks.map((c) => <tr key={c.id}><Td className="font-medium">{c.name}</Td><Td><Badge tone={checkTone[c.status]}>{c.status === "ok" ? "ok" : c.status === "warn" ? "attention" : "failing"}</Badge></Td><Td className="break-words text-muted">{c.detail}</Td></tr>)}</tbody></Table>}
        </Card>
      </div>
    </>
  );
}
