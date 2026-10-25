"use client";

import { Fragment, useState } from "react";
import { api, useApi } from "@/lib/api";
import { Badge, Banner, Button, Card, ErrorNote, JsonBlock, KeyValue, Loading, PageHeader, StatusBadge, Table, Tabs, Td, Th } from "@/components/ui";
import { fmtDate, titleCase } from "@/lib/format";

type Tab = "tasks" | "audit" | "reports" | "board" | "environment";
const TABS: { id: Tab; label: string }[] = [
  { id: "tasks", label: "Tasks (Layer A)" }, { id: "reports", label: "Reports & CEO checks (Layer B)" },
  { id: "board", label: "Raw board" }, { id: "audit", label: "Audit log" }, { id: "environment", label: "Environment & backups" },
];

interface TaskDetail {
  id: number; title: string; status: string; task_type: string; owner: string; attempts: number; max_attempts: number;
  result: unknown; error: { message: string; traceback?: string } | null; blocker: string | null;
  evidence: { id: number; kind: string; ref: string; verification_status: string; verification_detail: string | null }[];
  input_refs: unknown; output_refs: unknown; created_by: string; updated_at: string;
}

function TasksTab() {
  const [status, setStatus] = useState("");
  const { data, error, loading } = useApi<TaskDetail[]>(`/api/diagnostics/tasks${status ? `?status=${status}` : ""}`);
  const [open, setOpen] = useState<number | null>(null);
  return (
    <Card title="Task records" subtitle="Raw results, errors and evidence. Secrets are redacted before they are stored.">
      <div className="mb-3"><select aria-label="Filter by status" className="rounded-xl border border-line bg-surface px-3 py-2 text-sm" value={status} onChange={(e) => setStatus(e.target.value)}>
        <option value="">All statuses</option>
        {["QUEUED", "RUNNING", "SUBMITTED", "COMPLETED", "FAILED", "BLOCKED", "RETRY_SCHEDULED", "WAITING_FOR_APPROVAL", "CANCELLED"].map((s) => <option key={s}>{s}</option>)}
      </select></div>
      <ErrorNote error={error} />
      {loading && !data ? <Loading /> : (
        <Table>
          <thead><tr><Th>#</Th><Th>Task</Th><Th>Type</Th><Th>Owner</Th><Th>Status</Th><Th>Attempts</Th><Th>Updated</Th></tr></thead>
          <tbody>
            {(data ?? []).map((t) => (
              <Fragment key={t.id}>
                <tr onClick={() => setOpen(open === t.id ? null : t.id)} className="cursor-pointer hover:bg-surface2">
                  <Td>{t.id}</Td><Td className="font-medium">{t.title}</Td><Td className="text-muted">{t.task_type}</Td><Td>{t.owner}</Td>
                  <Td><StatusBadge status={t.status} /></Td><Td>{t.attempts}/{t.max_attempts}</Td><Td className="text-muted">{fmtDate(t.updated_at)}</Td>
                </tr>
                {open === t.id && (
                  <tr><Td colSpan={7}>
                    <div className="space-y-3 py-1">
                      {t.error && <div><h4 className="text-sm font-medium text-danger">Error</h4><JsonBlock value={t.error} /></div>}
                      {t.blocker && <p className="text-sm text-danger">Blocker: {t.blocker}</p>}
                      <div><h4 className="text-sm font-medium">Result</h4><JsonBlock value={t.result} /></div>
                      <div><h4 className="text-sm font-medium">Evidence</h4>{t.evidence.length === 0 ? <p className="text-sm text-muted">None.</p> : (
                        <ul className="space-y-1 text-sm">{t.evidence.map((e) => <li key={e.id}>{e.kind}: <code className="text-xs">{e.ref}</code> <Badge tone={e.verification_status === "verified" ? "ok" : e.verification_status === "failed" ? "danger" : "warn"}>{e.verification_status}</Badge> {e.verification_detail && <span className="text-muted">— {e.verification_detail}</span>}</li>)}</ul>)}</div>
                      <details><summary className="cursor-pointer text-sm text-muted">Input and output references</summary><JsonBlock value={{ input_refs: t.input_refs, output_refs: t.output_refs, created_by: t.created_by }} /></details>
                    </div>
                  </Td></tr>
                )}
              </Fragment>
            ))}
            {data && data.length === 0 && <tr><Td colSpan={7} className="text-muted">No tasks.</Td></tr>}
          </tbody>
        </Table>
      )}
    </Card>
  );
}

function AuditTab() {
  const { data, error, loading, reload } = useApi<{ id: number; ts: string; actor: string; action: string; entity: string | null; details: unknown; hash: string }[]>("/api/diagnostics/audit?limit=300");
  const [verify, setVerify] = useState<{ ok: boolean; checked: number; first_bad_id: number | null } | null>(null);
  return (
    <Card title="Audit log" subtitle="Append-only and hash-chained: any edit or deletion breaks the chain."
      action={<div className="flex gap-2"><Button onClick={reload}>Refresh</Button><Button variant="primary" onClick={async () => setVerify(await api("/api/diagnostics/audit/verify"))}>Verify integrity</Button></div>}>
      {verify && <div className="mb-3"><Banner tone={verify.ok ? "ok" : "danger"}>{verify.ok ? `Chain intact — ${verify.checked} events verified.` : `Chain BROKEN at event ${verify.first_bad_id}.`}</Banner></div>}
      <ErrorNote error={error} />
      {loading && !data ? <Loading /> : (
        <Table>
          <thead><tr><Th>Time</Th><Th>Actor</Th><Th>Action</Th><Th>Entity</Th><Th>Details</Th></tr></thead>
          <tbody>{(data ?? []).map((e) => (
            <tr key={e.id}><Td className="whitespace-nowrap text-muted">{fmtDate(e.ts)}</Td><Td>{e.actor}</Td><Td className="font-medium">{e.action}</Td><Td className="text-muted">{e.entity ?? "—"}</Td>
              <Td><details><summary className="cursor-pointer text-xs text-muted">show</summary><JsonBlock value={e.details} /></details></Td></tr>
          ))}</tbody>
        </Table>
      )}
    </Card>
  );
}

interface RepRow { id: number; kind: string; title: string; author: string; status: string; created_at: string; summary: string }
function ReportsTab() {
  const { data, error, loading } = useApi<RepRow[]>("/api/diagnostics/reports?limit=60");
  const [detail, setDetail] = useState<unknown>(null);
  return (
    <Card title="Department, operations and executive reports" subtitle="Includes the CEO's per-claim verification results.">
      <ErrorNote error={error} />
      {loading && !data ? <Loading /> : (
        <Table>
          <thead><tr><Th>#</Th><Th>Kind</Th><Th>Title</Th><Th>Author</Th><Th>Status</Th><Th>Summary</Th></tr></thead>
          <tbody>{(data ?? []).map((r) => (
            <tr key={r.id} className="cursor-pointer hover:bg-surface2" onClick={async () => setDetail(await api(`/api/diagnostics/reports/${r.id}`))}>
              <Td>{r.id}</Td><Td><Badge>{r.kind}</Badge></Td><Td className="font-medium">{r.title}</Td><Td className="text-muted">{r.author}</Td><Td><StatusBadge status={r.status} /></Td><Td className="text-muted">{r.summary}</Td>
            </tr>
          ))}</tbody>
        </Table>
      )}
      {detail !== null && <div className="mt-4"><JsonBlock value={detail} /></div>}
    </Card>
  );
}

function BoardTab() {
  const { data, error, loading } = useApi<{ sections: Record<string, Record<string, { public_id: string; title: string; confidence: string; created_by: string }[]>> }>("/api/diagnostics/board");
  if (loading && !data) return <Loading />;
  return (
    <Card title="Every board entry, including raw observations">
      <ErrorNote error={error} />
      {Object.entries(data?.sections ?? {}).map(([scope, byStatus]) => (
        <div key={scope} className="mb-4"><h3 className="mb-2 font-medium">{titleCase(scope)}</h3>
          {Object.entries(byStatus).map(([status, rows]) => (
            <div key={status} className="mb-2"><StatusBadge status={status} />
              <ul className="mt-1 space-y-0.5 text-sm">{rows.map((r) => <li key={r.public_id}><code className="text-xs">{r.public_id}</code> {r.title} <span className="text-muted">({r.confidence}, {r.created_by})</span></li>)}</ul></div>
          ))}
        </div>
      ))}
      {data && Object.keys(data.sections).length === 0 && <p className="text-sm text-muted">No entries.</p>}
    </Card>
  );
}

interface Env {
  environment: { os: string; python: string; cpu_count: number; memory_gib: number | null; gpu: { detected: boolean; detail: string | null };
    disk_free_gib: number; disk_total_gib: number; tools: Record<string, { ok?: boolean; version?: string | null; path?: string | null }>;
    providers_configured: Record<string, boolean>; data_dir: string };
  backups: { name: string; bytes: number }[];
}
function EnvTab() {
  const { data, error, loading, reload } = useApi<Env>("/api/diagnostics/environment");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<{ message: string } | null>(null);
  if (loading && !data) return <Loading />;
  const e = data?.environment;
  return (
    <div className="space-y-6">
      <ErrorNote error={error ?? err} />
      {e && <Card title="This computer">
        <KeyValue items={[
          ["Operating system", e.os], ["Python", e.python], ["CPU cores", String(e.cpu_count)], ["Memory", e.memory_gib ? `${e.memory_gib} GiB` : "unknown"],
          ["GPU", e.gpu.detected ? (e.gpu.detail ?? "detected") : "None detected — CPU-only rendering"],
          ["Disk", `${e.disk_free_gib} GiB free of ${e.disk_total_gib} GiB`],
          ["FFmpeg", e.tools.ffmpeg?.ok ? (e.tools.ffmpeg.version ?? "found") : <Badge tone="warn">Not found — required for video rendering</Badge>],
          ["Data folder", <code key="d" className="text-xs">{e.data_dir}</code>],
          ["Providers configured", Object.entries(e.providers_configured).map(([k, v]) => <Badge key={k} tone={v ? "ok" : "neutral"}>{k}: {v ? "key present" : "not configured"}</Badge>)],
        ]} />
      </Card>}
      <Card title="Database backups" action={<Button variant="primary" busy={busy} onClick={async () => { setBusy(true); setErr(null); try { await api("/api/diagnostics/backup", { method: "POST", body: {} }); setMsg("Backup created and verified."); await reload(); } catch (x) { setErr(x as Error); } finally { setBusy(false); } }}>Back up now</Button>}>
        {msg && <div className="mb-3"><Banner tone="ok">{msg}</Banner></div>}
        {(data?.backups ?? []).length === 0 ? <p className="text-sm text-muted">No backups yet.</p> :
          <ul className="space-y-1 text-sm">{data!.backups.map((b) => <li key={b.name}><code className="text-xs">{b.name}</code> <span className="text-muted">{(b.bytes / 1024).toFixed(0)} KiB</span></li>)}</ul>}
        <p className="mt-3 text-xs text-muted">Restore from the command line with the app stopped: <code>python -m app.cli restore &lt;file&gt;</code>.</p>
      </Card>
    </div>
  );
}

export default function DiagnosticsPage() {
  const [tab, setTab] = useState<Tab>("tasks");
  return (
    <>
      <PageHeader title="Diagnostics" description="Raw operational detail (Layer A) and CEO verification detail (Layer B). Not needed for normal oversight." />
      <div className="mb-4"><Banner tone="warn">Owner-only view. Every page you open here is recorded in the audit log.</Banner></div>
      <Tabs tabs={TABS} value={tab} onChange={setTab} />
      {tab === "tasks" && <TasksTab />}{tab === "reports" && <ReportsTab />}{tab === "board" && <BoardTab />}
      {tab === "audit" && <AuditTab />}{tab === "environment" && <EnvTab />}
    </>
  );
}
