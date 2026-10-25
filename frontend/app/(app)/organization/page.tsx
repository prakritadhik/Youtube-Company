"use client";

import { useMemo, useState } from "react";
import { useApi } from "@/lib/api";
import { Badge, Card, Drawer, ErrorNote, KeyValue, List, Loading, PageHeader, StatusBadge } from "@/components/ui";
import { titleCase } from "@/lib/format";
import type { AgentNode, OrgView, TaskSummary } from "@/lib/types";

function AgentCard({ a, onOpen }: { a: AgentNode; onOpen: (a: AgentNode) => void }) {
  return (
    <button onClick={() => onOpen(a)} className="w-full rounded-xl border border-line bg-surface p-3 text-left transition hover:border-accent">
      <div className="flex items-start justify-between gap-2">
        <span className="text-sm font-medium leading-snug">{a.name}</span>
        {a.actively_running ? <Badge tone="info">Running</Badge> : a.open_tasks.length ? <Badge tone="neutral">{a.open_tasks.length} open</Badge> : <Badge tone="neutral">Idle</Badge>}
      </div>
      <div className="mt-1 flex gap-1.5 text-xs text-muted">
        <span>{titleCase(a.level)}</span>{a.uses_ai_model && <span>· AI-assisted</span>}
      </div>
    </button>
  );
}

function TaskRow({ t }: { t: TaskSummary }) {
  return (
    <li className="rounded-xl border border-line p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2"><StatusBadge status={t.status} /><span className="font-medium">{t.title}</span></div>
      <div className="mt-1 space-y-0.5 text-xs text-muted">
        {t.depends_on.length > 0 && <div>Depends on task {t.depends_on.map((d) => `#${d}`).join(", ")}</div>}
        {t.blocker && <div className="text-danger">Blocker: {t.blocker}</div>}
        <div>Evidence items: {t.evidence_count} · attempts: {t.attempts}{t.board_version_used != null && ` · board v${t.board_version_used}`}</div>
        {(t.lessons_applied ?? []).length > 0 && <div>Lessons in force when it started: {t.lessons_applied!.join(", ")}</div>}
        {t.review_notes && <div>Manager review: {t.review_notes}</div>}
      </div>
    </li>
  );
}

export default function OrganizationPage() {
  const { data, error, loading } = useApi<OrgView>("/api/organization", { refreshMs: 15000 });
  const [sel, setSel] = useState<AgentNode | null>(null);
  const bySlug = useMemo(() => Object.fromEntries((data?.agents ?? []).map((a) => [a.slug, a])), [data]);
  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorNote error={error} />;
  if (!data) return null;

  const ceo = data.agents.find((a) => a.level === "ceo")!;
  const coo = data.agents.find((a) => a.level === "coo")!;
  const live = sel ? bySlug[sel.slug] : null;
  const counts = data.task_counts;

  return (
    <>
      <PageHeader title="Organization" description={`${data.agents.length} roles in a real reporting hierarchy. ${data.note}`} />
      <div className="mb-6 flex flex-wrap gap-2 text-sm">
        {Object.entries(counts).map(([k, v]) => <span key={k} className="flex items-center gap-1.5"><StatusBadge status={k} /><b className="tabular-nums">{v}</b></span>)}
        {Object.keys(counts).length === 0 && <span className="text-muted">No tasks have been created yet.</span>}
      </div>

      <div className="mb-6 flex flex-col items-center gap-3">
        <div className="rounded-xl border border-dashed border-line px-4 py-2 text-sm text-muted">Owner / Human Director (you)</div>
        <div className="w-full max-w-xs"><AgentCard a={ceo} onOpen={setSel} /></div>
        <div className="w-full max-w-xs"><AgentCard a={coo} onOpen={setSel} /></div>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {data.departments.map((d) => {
          const mgr = data.agents.find((a) => a.level === "manager" && a.department === d.slug);
          const workers = data.agents.filter((a) => a.level === "worker" && a.department === d.slug);
          return (
            <Card key={d.slug} title={d.name} subtitle={d.description}>
              <div className="space-y-2">
                {mgr && <AgentCard a={mgr} onOpen={setSel} />}
                <div className="ml-3 space-y-2 border-l border-line pl-3">{workers.map((w) => <AgentCard key={w.slug} a={w} onOpen={setSel} />)}</div>
              </div>
            </Card>
          );
        })}
      </div>

      {data.system_tasks.length > 0 && (
        <Card className="mt-6" title="System tasks" subtitle="Maintenance work with no assigned agent role.">
          <ul className="space-y-2">{data.system_tasks.map((t) => <TaskRow key={t.id} t={t} />)}</ul>
        </Card>
      )}

      <Drawer open={!!sel} onClose={() => setSel(null)} title={live?.name ?? ""}>
        {live && (
          <div className="space-y-5">
            <KeyValue items={[
              ["Level", titleCase(live.level)],
              ["Reports to", live.reports_to ? (bySlug[live.reports_to]?.name ?? live.reports_to) : "Owner"],
              ["Uses an AI model", live.uses_ai_model ? "Yes, through controlled calls" : "No — deterministic tools"],
              ["Status", live.actively_running ? "A task is genuinely running" : "Not running a task"],
            ]} />
            <div><h3 className="mb-1 text-sm font-medium">Responsibility</h3><p className="text-sm text-muted">{live.responsibilities}</p></div>
            <div><h3 className="mb-1 text-sm font-medium">Required inputs</h3><List items={live.required_inputs} /></div>
            <div><h3 className="mb-1 text-sm font-medium">Output format</h3><List items={live.output_format} /></div>
            <div><h3 className="mb-1 text-sm font-medium">Acceptance criteria</h3><List items={live.acceptance_criteria} /></div>
            <div><h3 className="mb-1 text-sm font-medium">Allowed tools</h3><div className="flex flex-wrap gap-1.5">{live.allowed_tools.map((t) => <Badge key={t}>{t}</Badge>)}</div></div>
            <div>
              <h3 className="mb-2 text-sm font-medium">Open tasks</h3>
              {live.open_tasks.length === 0 ? <p className="text-sm text-muted">No open tasks.</p> : <ul className="space-y-2">{live.open_tasks.map((t) => <TaskRow key={t.id} t={t} />)}</ul>}
            </div>
          </div>
        )}
      </Drawer>
    </>
  );
}
