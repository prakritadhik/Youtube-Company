"use client";

import { useEffect, useState } from "react";
import { api, useApi } from "@/lib/api";
import { useAuth } from "@/components/auth";
import { Badge, Button, Card, Empty, ErrorNote, List, Loading, PageHeader, Severity, Unavailable } from "@/components/ui";
import { fmtDate, titleCase } from "@/lib/format";
import type { ExecutiveReport } from "@/lib/types";

interface ListItem { id: number; created_at: string; headline: string }

const SECTION_TITLES: Record<string, string> = {
  verified_facts: "Verified facts", unverified_claims: "Unverified claims", rejected_claims: "Rejected claims",
  estimates: "Estimates", forecasts: "Forecasts", hypotheses: "Hypotheses", recommendations: "Recommendations",
  implemented_decisions: "Decisions already implemented", needs_owner_approval: "Needs your approval",
};
const TONES: Record<string, "ok" | "warn" | "danger" | "info" | "neutral"> = {
  verified_facts: "ok", unverified_claims: "warn", rejected_claims: "danger", estimates: "info", forecasts: "info", hypotheses: "info",
};

export default function ReportsPage() {
  const { can } = useAuth();
  const list = useApi<ListItem[]>("/api/executive/reports");
  const [selected, setSelected] = useState<number | null>(null);
  const [report, setReport] = useState<ExecutiveReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string } | null>(null);

  useEffect(() => { if (selected === null && list.data?.length) setSelected(list.data[0].id); }, [list.data, selected]);
  useEffect(() => {
    if (selected === null) return;
    api<ExecutiveReport>(`/api/executive/reports/${selected}`).then(setReport).catch((e) => setErr(e));
  }, [selected]);

  const generate = async () => {
    setBusy(true); setErr(null);
    try {
      const r = await api<{ report: ExecutiveReport }>("/api/executive/reports/generate", { method: "POST", body: {} });
      await list.reload(); setSelected(r.report.id); setReport(r.report);
    } catch (e) { setErr(e as Error); } finally { setBusy(false); }
  };

  if (list.loading && !list.data) return <Loading />;
  const c = report?.content;

  return (
    <>
      <PageHeader title="Executive reports" description="The AI CEO's verified reports. Each claim from the managers was re-checked against the records before it appears as a fact."
        actions={can("system:maintenance") ? <Button variant="primary" busy={busy} onClick={generate}>Generate report now</Button> : undefined} />
      <ErrorNote error={err} />
      {!list.data?.length && <Empty title="No reports yet" hint="Generate the first report to see the verified summary of what actually happened." />}

      {list.data && list.data.length > 0 && (
        <div className="grid gap-6 lg:grid-cols-[16rem_1fr]">
          <nav aria-label="Report history" className="space-y-1">
            {list.data.map((r) => (
              <button key={r.id} onClick={() => setSelected(r.id)} aria-current={selected === r.id}
                className={`w-full rounded-xl px-3 py-2 text-left text-sm transition ${selected === r.id ? "bg-accent-soft text-accent" : "hover:bg-surface2"}`}>
                <div className="font-medium">Report #{r.id}</div>
                <div className="text-xs text-muted">{fmtDate(r.created_at)}</div>
              </button>
            ))}
          </nav>

          {c && report ? (
            <div className="min-w-0 space-y-6">
              <Card title={c.headline} subtitle={`Period ${fmtDate(c.period.start)} → ${fmtDate(c.period.end)} · board v${report.board_version ?? 0}`}>
                <div className="flex flex-wrap gap-2 text-sm">
                  <Badge tone="ok">{c.check_summary.confirmed} confirmed</Badge>
                  <Badge tone={c.check_summary.contradicted ? "danger" : "neutral"}>{c.check_summary.contradicted} rejected</Badge>
                  <Badge tone={c.check_summary.unverifiable ? "warn" : "neutral"}>{c.check_summary.unverifiable} unverifiable</Badge>
                  <Badge>{c.department_coverage.reported.length}/{c.department_coverage.reported.length + c.department_coverage.missing.length} departments reported</Badge>
                </div>
                <p className="mt-3 text-xs text-muted">Generation method: {c.method}.</p>
              </Card>

              <Card title="Risks"><ul className="divide-y divide-line">{c.risks.length ? c.risks.map((r, i) => <li key={i} className="flex gap-3 py-2 text-sm"><Severity level={r.severity} />{r.text}</li>) : <li className="py-1 text-sm text-muted">No risks detected.</li>}</ul></Card>
              <Card title="Next priorities"><List items={c.next_priorities} /></Card>

              {Object.entries(SECTION_TITLES).map(([key, title]) => {
                const rows = c.sections[key] ?? [];
                if (!rows.length) return null;
                return (
                  <Card key={key} title={title} subtitle={c.legend[key]} action={<Badge tone={TONES[key] ?? "neutral"}>{rows.length}</Badge>}>
                    <ul className="space-y-2 text-sm">
                      {rows.map((row, i) => (
                        <li key={i} className="rounded-xl bg-surface2 p-3">
                          <div>{String(row.text ?? row.statement ?? "")}</div>
                          {key === "rejected_claims" && <div className="mt-1 text-xs text-danger">Reported {String(row.reported_value)}, records show {String(row.database_value)}.</div>}
                          {row.department != null && <div className="mt-1 text-xs text-muted">{titleCase(String(row.department))}</div>}
                        </li>
                      ))}
                    </ul>
                  </Card>
                );
              })}

              {(c.quiet_departments ?? []).length > 0 && (
                <Card title="Departments with no activity" subtitle="Verified from task records: nothing created, completed, failed, blocked or waiting.">
                  <div className="flex flex-wrap gap-2">{c.quiet_departments.map((q) => <Badge key={q.department}>{titleCase(q.department)}</Badge>)}</div>
                </Card>
              )}
              {(c.sections.unavailable_data ?? []).length > 0 && (
                <Card title="Unavailable data">
                  <div className="space-y-2">{c.sections.unavailable_data.map((u, i) => <Unavailable key={i} title={String(u.statement)} />)}</div>
                </Card>
              )}
              {c.disagreements.length > 0 && (
                <Card title="Disagreements between reports and records" subtitle="The records were treated as the most reliable source.">
                  <ul className="space-y-2 text-sm">{c.disagreements.map((d, i) => <li key={i} className="rounded-xl bg-warn-soft p-3">{String(d.topic)} — reported {JSON.stringify(d.reported ?? d.values)}, verified {JSON.stringify(d.verified ?? "n/a")}</li>)}</ul>
                </Card>
              )}
              {c.department_coverage.missing.length > 0 && (
                <Card title="Departments with no report"><p className="text-sm">{c.department_coverage.missing.map(titleCase).join(", ")}</p></Card>
              )}
            </div>
          ) : <Loading />}
        </div>
      )}
    </>
  );
}
