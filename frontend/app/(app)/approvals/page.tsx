"use client";

import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { Badge, Button, Card, Empty, ErrorNote, inputCls, List, Loading, PageHeader, StatusBadge } from "@/components/ui";
import { fmtDate, titleCase } from "@/lib/format";
import type { ApprovalItem } from "@/lib/types";

export default function ApprovalsPage() {
  const { data, error, loading, reload } = useApi<{ pending: ApprovalItem[]; recent: ApprovalItem[] }>("/api/approvals");
  const [note, setNote] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState<number | null>(null);
  const [err, setErr] = useState<{ message: string } | null>(null);

  const decide = async (id: number, approve: boolean) => {
    setBusy(id); setErr(null);
    try { await api(`/api/approvals/${id}/decide`, { method: "POST", body: { approve, note: note[id] ?? "" } }); await reload(); }
    catch (e) { setErr(e as Error); } finally { setBusy(null); }
  };

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorNote error={error} />;
  if (!data) return null;

  return (
    <>
      <PageHeader title="Approval Center" description="Everything that needs your decision, with what it is, why it matters, the evidence, and what happens either way. Only you can decide — no agent, including the CEO, can approve for you." />
      <ErrorNote error={err} />
      <div className="space-y-4">
        {data.pending.length === 0 && <Empty title="Nothing is waiting for you" hint="When an agent needs a decision — publishing permission, a budget change, medical review, a rights concern — it appears here." />}
        {data.pending.map((a) => (
          <Card key={a.id} title={a.title} subtitle={<>Requested by {a.requested_by} · {fmtDate(a.created_at)}</>} action={<Badge tone="warn">{titleCase(a.kind)}</Badge>}>
            <div className="grid gap-5 lg:grid-cols-2">
              <div className="space-y-4 text-sm">
                <div><h3 className="mb-1 font-medium">What is being requested</h3><p className="text-muted">{a.summary}</p></div>
                <div><h3 className="mb-1 font-medium">Why it matters</h3><p className="text-muted">{a.why_it_matters}</p></div>
                <div>
                  <h3 className="mb-1 font-medium">Evidence available</h3>
                  {a.evidence.length === 0 ? <p className="text-muted">No evidence attached.</p> :
                    <List items={a.evidence.map((e, i) => <span key={i}>{e.kind}: <code className="text-xs">{e.ref}</code> {e.status && <Badge tone={e.status === "verified" ? "ok" : "warn"}>{e.status}</Badge>}</span>)} />}
                </div>
              </div>
              <div className="space-y-4 text-sm">
                <div className="rounded-xl bg-ok-soft p-3"><h3 className="font-medium text-ok">If you approve</h3><p className="mt-0.5">{a.if_approved}</p></div>
                <div className="rounded-xl bg-surface2 p-3"><h3 className="font-medium">If you reject</h3><p className="mt-0.5 text-muted">{a.if_rejected}</p></div>
                <div>
                  <label htmlFor={`n${a.id}`} className="mb-1 block font-medium">Note (optional, recorded)</label>
                  <input id={`n${a.id}`} className={inputCls} value={note[a.id] ?? ""} onChange={(e) => setNote({ ...note, [a.id]: e.target.value })} />
                </div>
                <div className="flex gap-2">
                  <Button variant="primary" busy={busy === a.id} onClick={() => decide(a.id, true)}>Approve</Button>
                  <Button busy={busy === a.id} onClick={() => decide(a.id, false)}>Reject</Button>
                </div>
              </div>
            </div>
          </Card>
        ))}
      </div>

      <h2 className="mb-3 mt-10 text-lg font-semibold">Recent decisions</h2>
      <Card>
        {data.recent.length === 0 ? <p className="text-sm text-muted">No decisions yet.</p> : (
          <ul className="divide-y divide-line">
            {data.recent.map((a) => (
              <li key={a.id} className="flex flex-wrap items-center gap-3 py-2.5 text-sm">
                <StatusBadge status={a.status} /><span className="font-medium">{a.title}</span>
                <span className="text-muted">{a.decided_by ? `by ${a.decided_by} · ${fmtDate(a.decided_at)}` : ""}{a.decision_note ? ` — ${a.decision_note}` : ""}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}
