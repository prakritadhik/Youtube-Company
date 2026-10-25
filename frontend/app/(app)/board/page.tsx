"use client";

import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { Badge, Card, Drawer, Empty, ErrorNote, KeyValue, List, Loading, PageHeader, StatusBadge } from "@/components/ui";
import { fmtDate, titleCase } from "@/lib/format";
import type { BoardEntry, BoardPoster } from "@/lib/types";

const COLUMNS: { status: string; title: string; hint: string }[] = [
  { status: "ACTIVE_INSTRUCTION", title: "Active instructions", hint: "Workers must follow these" },
  { status: "APPROVED_FOR_TESTING", title: "Being tested", hint: "Experiments, not rules" },
  { status: "VERIFIED", title: "Verified", hint: "True, not yet applied" },
  { status: "REJECTED", title: "Rejected", hint: "Disproved or declined" },
  { status: "EXPIRED", title: "Expired", hint: "No longer applicable" },
];

interface Detail { entry: BoardEntry; history: { version: number; snapshot: { status: string }; changed_by: string; change_note: string; created_at: string }[] }

export default function BoardPage() {
  const { data, error, loading } = useApi<BoardPoster>("/api/board", { refreshMs: 30000 });
  const [detail, setDetail] = useState<Detail | null>(null);
  const [derr, setDerr] = useState<{ message: string } | null>(null);

  const open = async (id: string) => {
    setDerr(null);
    try { setDetail(await api<Detail>(`/api/board/${id}`)); } catch (e) { setDerr(e as Error); }
  };

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorNote error={error} />;
  if (!data) return null;
  const sections = Object.entries(data.sections);
  const total = Object.values(data.counts).reduce((a, b) => a + b, 0);

  return (
    <>
      <PageHeader title="Company Intelligence Board" description={<>Version {data.current_version}. {data.note} Lessons are channel-specific and only verified findings appear here; raw observations are in Diagnostics.</>} />
      <ErrorNote error={derr} />
      <div className="mb-6 flex flex-wrap gap-3 text-sm">
        {COLUMNS.map((c) => <span key={c.status} className="flex items-center gap-1.5"><StatusBadge status={c.status} /><b className="tabular-nums">{data.counts[c.status] ?? 0}</b></span>)}
      </div>

      {total === 0 && <Empty title="The board is empty" hint="Lessons enter as observations, are investigated by a manager, verified against evidence by the CEO, and only then become instructions or experiments." />}

      <div className="space-y-8">
        {sections.map(([scope, byStatus]) => (
          <section key={scope} aria-labelledby={`s-${scope}`}>
            <h2 id={`s-${scope}`} className="mb-3 text-lg font-semibold">{scope === "company" ? "Company-wide" : titleCase(scope)}</h2>
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {COLUMNS.filter((c) => byStatus[c.status]?.length).map((c) => (
                <Card key={c.status} title={c.title} subtitle={c.hint}>
                  <ul className="space-y-2">
                    {byStatus[c.status].map((e) => (
                      <li key={e.public_id}>
                        <button onClick={() => open(e.public_id)} className="w-full rounded-xl border border-line p-3 text-left transition hover:border-accent">
                          <div className="text-xs text-muted">{e.public_id} · {titleCase(e.category)}</div>
                          <div className="mt-0.5 text-sm font-medium">{e.title}</div>
                          <div className="mt-1.5 flex gap-1.5"><Badge tone={e.confidence === "high" ? "ok" : e.confidence === "medium" ? "info" : "warn"}>{e.confidence} confidence</Badge><Badge>v{e.version}</Badge></div>
                        </button>
                      </li>
                    ))}
                  </ul>
                </Card>
              ))}
            </div>
          </section>
        ))}
      </div>

      <Card className="mt-10" title="Version history" subtitle="A new board version is published whenever the instructions workers must follow change.">
        {data.releases.length === 0 ? <p className="text-sm text-muted">No instruction changes yet.</p> :
          <List items={data.releases.map((r) => <span key={r.version}><b>v{r.version}</b> — {r.reason} <span className="text-muted">({r.created_by}, {fmtDate(r.created_at)})</span></span>)} />}
      </Card>

      <Drawer open={!!detail} onClose={() => setDetail(null)} title={detail ? `${detail.entry.public_id}: ${detail.entry.title}` : ""}>
        {detail && (
          <div className="space-y-5">
            <KeyValue items={[
              ["Status", <StatusBadge key="s" status={detail.entry.status} />],
              ["Channel", detail.entry.channel ? titleCase(detail.entry.channel) : "Company-wide"],
              ["Category", titleCase(detail.entry.category)],
              ["Confidence", detail.entry.confidence],
              ["Risk class", detail.entry.risk_class],
              ["Responsible", detail.entry.responsible_manager ?? "—"],
              ["Version", String(detail.entry.version)],
              ["Review date", detail.entry.review_date ?? "—"],
              ["Last changed", fmtDate(detail.entry.updated_at)],
            ]} />
            <p className="text-sm">{detail.entry.description}</p>
            {detail.entry.test_plan && <div><h3 className="mb-1 text-sm font-medium">Test plan</h3><KeyValue items={Object.entries(detail.entry.test_plan).map(([k, v]) => [titleCase(k), v] as [string, string])} /></div>}
            <div>
              <h3 className="mb-1 text-sm font-medium">Evidence</h3>
              {(detail.entry.evidence ?? []).length === 0 ? <p className="text-sm text-muted">None.</p> :
                <List items={(detail.entry.evidence ?? []).map((e) => <span key={e.id}>{e.kind}: <code className="text-xs">{e.ref}</code> <Badge tone={e.verification_status === "verified" ? "ok" : e.verification_status === "failed" ? "danger" : "warn"}>{e.verification_status}</Badge>{e.verification_detail && <span className="text-muted"> — {e.verification_detail}</span>}</span>)} />}
            </div>
            <div>
              <h3 className="mb-1 text-sm font-medium">History</h3>
              <List items={detail.history.map((h) => <span key={h.version}>v{h.version} · <StatusBadge status={h.snapshot.status} /> <span className="text-muted">{h.changed_by} — {h.change_note}</span></span>)} />
            </div>
          </div>
        )}
      </Drawer>
    </>
  );
}
