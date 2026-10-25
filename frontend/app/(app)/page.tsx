"use client";

import Link from "next/link";
import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { useAuth } from "@/components/auth";
import { Badge, Button, Card, ConfirmDialog, Empty, ErrorNote, inputCls, List, Loading, PageHeader, Severity, Stat, StatusBadge, Unavailable } from "@/components/ui";
import { fmtDate, fmtUsd } from "@/lib/format";
import type { Overview } from "@/lib/types";

export default function OverviewPage() {
  const { can } = useAuth();
  const { data, error, loading, reload } = useApi<Overview>("/api/executive/overview", { refreshMs: 20000 });
  const [busy, setBusy] = useState(false);
  const [actionErr, setActionErr] = useState<{ message: string } | null>(null);
  const [pauseOpen, setPauseOpen] = useState(false);
  const [reason, setReason] = useState("");

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorNote error={error} />;
  if (!data) return null;
  const { company_status: cs, pipeline: p, costs: c, latest_executive_report: rep } = data;

  const generate = async () => {
    setBusy(true); setActionErr(null);
    try { await api("/api/executive/reports/generate", { method: "POST", body: {} }); await reload(); }
    catch (e) { setActionErr(e as Error); } finally { setBusy(false); }
  };
  const pause = async () => {
    setBusy(true); setActionErr(null);
    try { await api("/api/control/pause", { method: "POST", body: { reason } }); setPauseOpen(false); setReason(""); window.dispatchEvent(new Event("control:changed")); await reload(); }
    catch (e) { setActionErr(e as Error); } finally { setBusy(false); }
  };

  return (
    <>
      <PageHeader
        title="Executive overview"
        description="What the AI CEO has verified, what needs your decision, and what is unavailable. Measurements, estimates and missing data are labelled separately."
        actions={
          <>
            <Badge tone={cs.emergency_pause ? "danger" : cs.operating_mode === "DEVELOPMENT" ? "info" : "ok"}>{cs.label.replace("_", " ")}</Badge>
            {can("control:pause") && !cs.emergency_pause && (
              <Button variant="danger" onClick={() => setPauseOpen(true)}>Emergency pause</Button>
            )}
          </>
        }
      />
      <ErrorNote error={actionErr} />

      <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label="In production" value={p.in_production} />
        <Stat label="Awaiting your approval" value={p.awaiting_owner_approval} tone={p.awaiting_owner_approval ? "warn" : "neutral"} />
        <Stat label="Cleared to publish" value={p.cleared_for_publishing} />
        <Stat label="Published" value={p.published} hint="Counts real uploads only" />
        <Stat label="Blocked by rights" value={p.blocked_by_rights} tone={p.blocked_by_rights ? "danger" : "neutral"} />
        <Stat label="Pending decisions" value={data.approvals_pending.count} tone={data.approvals_pending.count ? "warn" : "neutral"} />
      </div>

      <div className="grid gap-6 xl:grid-cols-3">
        <div className="space-y-6 xl:col-span-2">
          <Card
            title="AI CEO — latest verified report"
            subtitle={rep ? `Generated ${fmtDate(rep.created_at)}` : undefined}
            action={can("system:maintenance") && rep ? <Button variant="primary" busy={busy} onClick={generate}>Generate new</Button> : undefined}
          >
            {rep ? (
              <div className="space-y-4">
                <p className="text-base">{rep.headline}</p>
                <div className="flex flex-wrap gap-2 text-sm">
                  <Badge tone="ok">{rep.check_summary.confirmed} claims confirmed</Badge>
                  <Badge tone={rep.check_summary.contradicted ? "danger" : "neutral"}>{rep.check_summary.contradicted} rejected</Badge>
                  <Badge tone={rep.check_summary.unverifiable ? "warn" : "neutral"}>{rep.check_summary.unverifiable} unverifiable</Badge>
                </div>
                <div>
                  <h3 className="mb-1.5 text-sm font-medium">Next priorities</h3>
                  <List items={rep.next_priorities} />
                </div>
                <Link href="/reports" className="text-sm font-medium text-accent hover:underline">Open the full report →</Link>
              </div>
            ) : (
              <Empty title="No executive report yet" hint={data.executive_report_note ?? undefined}
                action={can("system:maintenance") ? <Button variant="primary" busy={busy} onClick={generate}>Generate report</Button> : undefined} />
            )}
          </Card>

          <Card title="Risks and items that need attention" subtitle="Computed live from records, not from agent opinion.">
            {data.risks.length === 0 ? (
              <p className="text-sm text-muted">No open risks detected.</p>
            ) : (
              <ul className="divide-y divide-line">
                {data.risks.map((r, i) => (
                  <li key={i} className="flex items-start gap-3 py-2.5 text-sm"><Severity level={r.severity} /><span>{r.text}</span></li>
                ))}
              </ul>
            )}
          </Card>

          <Card title="Waiting for your decision" action={<Link href="/approvals" className="text-sm font-medium text-accent hover:underline">Approval Center →</Link>}>
            {data.approvals_pending.items.length === 0 ? (
              <p className="text-sm text-muted">Nothing is waiting for you.</p>
            ) : (
              <ul className="divide-y divide-line">
                {data.approvals_pending.items.map((a) => (
                  <li key={a.id} className="py-2.5 text-sm">
                    <div className="font-medium">{a.title}</div>
                    <div className="text-muted">{a.summary.slice(0, 160)}</div>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          {data.blocked_tasks.length > 0 && (
            <Card title="Blocked tasks" action={<Link href="/organization" className="text-sm font-medium text-accent hover:underline">Organization →</Link>}>
              <ul className="divide-y divide-line">
                {data.blocked_tasks.map((t) => (
                  <li key={t.id} className="flex flex-wrap items-center gap-2 py-2.5 text-sm">
                    <StatusBadge status={t.status} /><span className="font-medium">{t.title}</span>
                    <span className="text-muted">— {t.blocker ?? "no reason recorded"}</span>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>

        <div className="space-y-6">
          <Card title="Cost and usage" subtitle="Actual charges and estimates are never added together.">
            <div className="space-y-3 text-sm">
              {([["Today", c.today], ["This month", c.month]] as const).map(([label, v]) => (
                <div key={label} className="rounded-xl bg-surface2 p-3">
                  <div className="mb-1 font-medium">{label}</div>
                  <div className="flex justify-between"><span className="text-muted">Actual charges</span><span className="tabular-nums">{fmtUsd(v.actual_usd)}</span></div>
                  <div className="flex justify-between"><span className="text-muted">Estimated</span><span className="tabular-nums">{fmtUsd(v.estimated_usd)}</span></div>
                  <div className="flex justify-between"><span className="text-muted">Subscription-included uses</span><span className="tabular-nums">{v.included_in_subscription_units}</span></div>
                </div>
              ))}
              <p className="text-xs text-muted">
                Limits: {fmtUsd(c.limits.daily_usd)}/day · {fmtUsd(c.limits.monthly_usd)}/month · {fmtUsd(c.limits.per_video_usd)}/video.
                {c.limits.daily_usd === 0 && c.limits.monthly_usd === 0 && " A limit of $0 means no paid operation is permitted."}
              </p>
            </div>
          </Card>

          <Card title="Data availability">
            <div className="space-y-2">
              {Object.entries(data.metrics_availability).map(([k, v]) =>
                v.available ? <Badge key={k} tone="ok">{k}</Badge> : <Unavailable key={k} title={k.replace(/_/g, " ")} reason={v.reason} />
              )}
            </div>
          </Card>

          <Card title="Recent verified improvements">
            {data.recent_verified_improvements.length === 0 ? (
              <p className="text-sm text-muted">No lessons have been verified yet.</p>
            ) : (
              <ul className="space-y-2 text-sm">
                {data.recent_verified_improvements.map((i) => (
                  <li key={i.public_id} className="flex items-start justify-between gap-2"><span>{i.title}</span><StatusBadge status={i.status} /></li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>

      <ConfirmDialog open={pauseOpen} title="Pause the whole company?" danger confirmLabel="Pause now" busy={busy}
        body="This stops new production tasks, paid generation, uploads and automated replies. Work already running finishes its current step; a request already sent to a provider cannot be recalled."
        onConfirm={pause} onCancel={() => setPauseOpen(false)}>
        <label className="mb-1 block text-sm font-medium" htmlFor="reason">Reason (recorded in the audit log)</label>
        <input id="reason" className={inputCls} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. unexpected spending" />
        {reason.trim().length > 0 && reason.trim().length < 3 && <p className="mt-1 text-xs text-danger">Please enter at least 3 characters.</p>}
      </ConfirmDialog>
    </>
  );
}
