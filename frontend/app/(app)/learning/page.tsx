"use client";

import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { useAuth } from "@/components/auth";
import { Badge, Banner, Button, Card, Empty, ErrorNote, inputCls, Loading, PageHeader, StatusBadge, Table, Td, Th, Tabs } from "@/components/ui";
import { fmtDate, titleCase } from "@/lib/format";
import type { BoardEntry, CompareResponse, ExperimentView, ImprovementOverview, Tone, VideoItem } from "@/lib/types";

const CHANNELS: [string, string][] = [["happy-birthday-songs", "Birthday Songs"], ["medicine-information", "Medicine Info"], ["ai-story-videos", "AI Stories"]];
const METRICS: [string, string, string[]][] = [
  ["views", "Views", ["sum", "mean"]], ["estimatedMinutesWatched", "Minutes watched", ["sum", "mean"]], ["averageViewPercentage", "Average percentage viewed", ["mean"]],
  ["averageViewDuration", "Average view duration", ["mean"]], ["likes", "Likes", ["sum", "mean"]], ["comments", "Comments", ["sum", "mean"]],
  ["subscribersGained", "Subscribers gained", ["sum", "mean"]], ["impressions", "Impressions", ["sum", "mean"]], ["impressionsClickThroughRate", "Click-through rate", ["mean"]],
  ["views_total", "Total views (reading)", ["latest"]],
];
const nf = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const conclusionTone: Record<string, Tone> = { supported_improvement: "ok", supported_harm: "danger", no_clear_difference: "warn", insufficient_data: "neutral", cannot_reach_threshold: "warn" };

// ---------- lessons ----------

const NEXT: Record<string, { action: string; label: string; needsReason: boolean }[]> = {
  OBSERVED: [{ action: "investigate", label: "Start investigating", needsReason: true }, { action: "reject", label: "Reject", needsReason: true }],
  UNDER_INVESTIGATION: [{ action: "verify", label: "Ask the CEO to verify the evidence", needsReason: false }, { action: "reject", label: "Reject", needsReason: true }],
  VERIFIED: [{ action: "activate", label: "Approve as an instruction", needsReason: false }, { action: "reject", label: "Reject", needsReason: true }],
  ACTIVE_INSTRUCTION: [{ action: "expire", label: "Retire", needsReason: true }],
  APPROVED_FOR_TESTING: [{ action: "expire", label: "Retire", needsReason: true }],
};

function LessonCard({ e, canWrite, onChanged }: { e: BoardEntry; canWrite: boolean; onChanged: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<{ message: string } | null>(null);
  const [reason, setReason] = useState("");
  const act = async (action: string) => {
    setBusy(action); setErr(null);
    try { await api(`/api/board/${e.public_id}/${action}`, { method: "POST", body: { reason } }); setReason(""); onChanged(); } catch (x) { setErr(x as Error); } finally { setBusy(null); }
  };
  const steps = NEXT[e.status] ?? [];
  const needs = steps.some((s) => s.needsReason);
  return (
    <li className="rounded-xl border border-line p-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{e.public_id}</span><StatusBadge status={e.status} /><Badge>{titleCase(e.category)}</Badge>
        <Badge tone={e.confidence === "high" ? "ok" : e.confidence === "medium" ? "info" : "warn"}>{e.confidence} confidence</Badge>
        <span className="text-xs text-muted">{e.channel ? titleCase(e.channel) : "company-wide"} · {fmtDate(e.created_at)}</span>
      </div>
      <div className="mt-1 font-medium">{e.title}</div>
      <p className="mt-1 whitespace-pre-line break-words text-muted">{e.description}</p>
      {(e.evidence ?? []).length > 0 && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs font-medium">Evidence ({e.evidence!.length}); the CEO re-checks it before verifying</summary>
          <ul className="mt-1 space-y-0.5 text-xs">
            {e.evidence!.map((x) => (
              <li key={x.id}>{x.kind}: <code>{x.ref}</code>{" "}
                <Badge tone={x.verification_status === "verified" ? "ok" : x.verification_status === "failed" ? "danger" : "warn"}>{x.verification_status}</Badge>{" "}
                <span className="text-muted">{x.note}</span></li>
            ))}
          </ul>
        </details>
      )}
      {e.status_reason && <p className="mt-1 text-xs text-muted">Last reason: {e.status_reason}</p>}
      <ErrorNote error={err} />
      {canWrite && steps.length > 0 && (
        <div className="mt-3 space-y-2">
          {needs && <input aria-label={`Reason for ${e.public_id}`} className={inputCls} placeholder="Reason (kept with the lesson)" value={reason} onChange={(x) => setReason(x.target.value)} />}
          <div className="flex flex-wrap gap-2">
            {steps.map((s) => (
              <Button key={s.action} variant={s.action === "activate" ? "primary" : "secondary"} busy={busy === s.action} disabled={s.needsReason && !reason.trim()} onClick={() => act(s.action)}>{s.label}</Button>
            ))}
          </div>
        </div>
      )}
    </li>
  );
}

const STATUS_HINT: Record<string, string> = {
  OBSERVED: "Observations only. Nothing here is an instruction.",
  UNDER_INVESTIGATION: "Being checked.",
  VERIFIED: "The CEO re-checked the evidence. Not yet applied: your approval is the last step.",
  ACTIVE_INSTRUCTION: "Sent to the relevant workers of that channel; each task records which of these were in force.",
  REJECTED: "Declined.",
  EXPIRED: "No longer applicable.",
};

function Lessons({ data, canWrite, reload }: { data: ImprovementOverview; canWrite: boolean; reload: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const lessons = data.lessons.filter((l) => l.category !== "experiment");
  const order = ["VERIFIED", "UNDER_INVESTIGATION", "OBSERVED", "ACTIVE_INSTRUCTION", "REJECTED", "EXPIRED"];
  const by = (s: string) => lessons.filter((l) => l.status === s);
  const mine = async () => {
    setBusy(true); setErr(null); setNote(null);
    try { await api("/api/improvement/mine", { method: "POST", body: {} }); setNote("Queued. The worker will look through finished workflows within a few seconds."); reload(); } catch (e) { setErr(e as Error); } finally { setBusy(false); }
  };
  return (
    <div className="space-y-4">
      <Banner tone="info">{data.notes.join(" ")}</Banner>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span>{data.unexamined_workflows} finished workflow(s) not yet examined for lessons.</span>
        {canWrite && <Button busy={busy} onClick={mine}>Look for lessons now</Button>}
        {note && <span className="text-muted">{note}</span>}
      </div>
      <ErrorNote error={err} />
      {lessons.length === 0 && <Empty title="No lessons yet" hint="Lessons are proposed from what finished workflows recorded: a QA check that failed and was corrected, an originality finding that was resolved. A workflow that went smoothly teaches nothing." />}
      {order.map((s) => by(s).length > 0 && (
        <Card key={s} title={titleCase(s.toLowerCase().replace(/_/g, " "))} subtitle={STATUS_HINT[s]}>
          <ul className="space-y-3">{by(s).map((e) => <LessonCard key={e.public_id} e={e} canWrite={canWrite} onChanged={reload} />)}</ul>
        </Card>
      ))}
    </div>
  );
}

// ---------- experiments ----------

function NewExperiment({ onDone }: { onDone: () => void }) {
  const [f, setF] = useState({ channel: CHANNELS[0][0], title: "", hypothesis: "", change: "", metric: "views", aggregate: "sum", window_days: "7", higher: "yes", min: "5", confounders: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string } | null>(null);
  const spec = METRICS.find((m) => m[0] === f.metric)!;
  const set = (k: string, v: string) => setF((p) => ({ ...p, [k]: v, ...(k === "metric" ? { aggregate: METRICS.find((m) => m[0] === v)![2][0] } : {}) }));
  const save = async () => {
    setBusy(true); setErr(null);
    try {
      await api("/api/experiments", {
        method: "POST",
        body: {
          channel: f.channel, title: f.title, hypothesis: f.hypothesis, change: f.change, min_per_group: Number(f.min),
          metric: { metric: f.metric, aggregate: f.aggregate, window_days: Number(f.window_days), higher_is_better: f.higher === "yes" },
          confounders: f.confounders.split("\n").map((x) => x.trim()).filter(Boolean),
        },
      });
      onDone();
    } catch (e) { setErr(e as Error); } finally { setBusy(false); }
  };
  const aggLabel: Record<string, string> = { sum: "total over the window", mean: "daily average", latest: "reading at the end of the window" };
  return (
    <Card title="New experiment" subtitle="Plan it before any video is published. The plan, the measure and the number of videos are fixed in advance; that is what makes the result readable.">
      <ErrorNote error={err} />
      <div className="grid gap-3 text-sm sm:grid-cols-2">
        <label><span className="mb-1 block font-medium">Channel</span><select className={inputCls} value={f.channel} onChange={(e) => set("channel", e.target.value)}>{CHANNELS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
        <label><span className="mb-1 block font-medium">Title</span><input className={inputCls} value={f.title} onChange={(e) => set("title", e.target.value)} /></label>
        <label className="sm:col-span-2"><span className="mb-1 block font-medium">Hypothesis</span><textarea className={`${inputCls} min-h-16`} value={f.hypothesis} onChange={(e) => set("hypothesis", e.target.value)} placeholder="If we do X, then Y will be higher in the first week." /></label>
        <label className="sm:col-span-2"><span className="mb-1 block font-medium">The change</span><textarea className={`${inputCls} min-h-16`} value={f.change} onChange={(e) => set("change", e.target.value)} placeholder="Exactly what is done differently on treatment videos." /></label>
        <label><span className="mb-1 block font-medium">Success measure</span><select className={inputCls} value={f.metric} onChange={(e) => set("metric", e.target.value)}>{METRICS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
        <label><span className="mb-1 block font-medium">Combined as</span><select className={inputCls} value={f.aggregate} onChange={(e) => set("aggregate", e.target.value)}>{spec[2].map((a) => <option key={a} value={a}>{aggLabel[a]}</option>)}</select></label>
        <label><span className="mb-1 block font-medium">Days observed per video</span><input type="number" min="1" max="90" className={inputCls} value={f.window_days} onChange={(e) => set("window_days", e.target.value)} /></label>
        <label><span className="mb-1 block font-medium">Better is</span><select className={inputCls} value={f.higher} onChange={(e) => set("higher", e.target.value)}><option value="yes">Higher</option><option value="no">Lower</option></select></label>
        <label><span className="mb-1 block font-medium">Videos per arm (at least 5)</span><input type="number" min="5" max="50" className={inputCls} value={f.min} onChange={(e) => set("min", e.target.value)} /></label>
        <label className="sm:col-span-2"><span className="mb-1 block font-medium">Other confounders worth naming (one per line, optional)</span><textarea className={`${inputCls} min-h-12`} value={f.confounders} onChange={(e) => set("confounders", e.target.value)} /></label>
      </div>
      <div className="mt-3"><Button variant="primary" busy={busy} disabled={!f.title.trim() || !f.hypothesis.trim() || !f.change.trim()} onClick={save}>Save the plan</Button></div>
    </Card>
  );
}

function ExperimentCard({ summary, canWrite, isOwner, onChanged }: { summary: ExperimentView; canWrite: boolean; isOwner: boolean; onChanged: () => void }) {
  const [open, setOpen] = useState(summary.status === "RUNNING");
  const { data: full, reload } = useApi<ExperimentView>(open ? `/api/experiments/${summary.id}` : null);
  const { data: vids } = useApi<VideoItem[]>(open && canWrite && ["DESIGN", "RUNNING"].includes(summary.status) ? "/api/videos" : null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<{ message: string } | null>(null);
  const [pick, setPick] = useState("");
  const [decision, setDecision] = useState("");
  const [note, setNote] = useState("");
  const e = full ?? summary;
  const act = async (key: string, path: string, body: unknown = {}) => {
    setBusy(key); setErr(null);
    try { await api(path, { method: "POST", body }); await reload(); onChanged(); } catch (x) { setErr(x as Error); } finally { setBusy(null); }
  };
  const taken = new Set((full?.assignments ?? []).map((a) => a.video_id));
  const candidates = (vids ?? []).filter((v) => v.channel === e.channel && !taken.has(v.id) && !["SCHEDULED", "PUBLISHED", "REJECTED"].includes(v.stage) && !v.youtube_video_id);
  const a = e.analysis;
  return (
    <li className="rounded-xl border border-line p-4 text-sm">
      <button className="flex w-full flex-wrap items-center gap-2 text-left" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className="font-medium">{e.public_id}</span><StatusBadge status={e.status} /><span className="font-medium">{e.title}</span>
        <span className="text-xs text-muted">{e.channel ? titleCase(e.channel) : ""} · {e.enrolled.control} control · {e.enrolled.treatment} treatment · needs {e.min_per_group} finished per arm</span>
        {e.decision && <Badge tone={e.decision === "adopt" ? "ok" : "neutral"}>{e.decision}</Badge>}
      </button>
      {open && (
        <div className="mt-3 space-y-3">
          <ErrorNote error={err} />
          <dl className="grid gap-x-6 gap-y-1 sm:grid-cols-[10rem_1fr]">
            <dt className="text-muted">Hypothesis</dt><dd>{e.hypothesis}</dd>
            <dt className="text-muted">The change</dt><dd>{e.change}</dd>
            <dt className="text-muted">Measured by</dt><dd>{e.metric_description}</dd>
            <dt className="text-muted">Baseline</dt>
            <dd>{e.baseline ? (e.baseline.status === "available" ? `median ${nf.format(e.baseline.median!)} over the last ${e.baseline.n} public videos` : `unavailable: ${e.baseline.reason}`) : "measured when the experiment starts"}</dd>
            {e.board_entry && <><dt className="text-muted">On the board</dt><dd>{e.board_entry} <StatusBadge status={e.board_status ?? ""} /></dd></>}
          </dl>
          <details>
            <summary className="cursor-pointer text-xs font-medium">Confounders and limits that apply to any result</summary>
            <ul className="mt-1 list-disc pl-5 text-xs text-muted">{[...e.confounders, ...e.limitations].map((c) => <li key={c}>{c}</li>)}</ul>
          </details>

          {canWrite && ["DESIGN", "RUNNING"].includes(e.status) && (
            <div className="flex flex-wrap items-end gap-2 rounded-lg bg-surface2 p-3">
              <label className="min-w-0"><span className="mb-1 block text-xs font-medium">Add a video that has not been published</span>
                <select className={inputCls} value={pick} onChange={(x) => setPick(x.target.value)}>
                  <option value="">Choose…</option>
                  {candidates.map((v) => <option key={v.id} value={v.id}>{v.title} ({v.stage.toLowerCase()})</option>)}
                </select></label>
              <Button busy={busy === "enroll"} disabled={!pick} onClick={() => act("enroll", `/api/experiments/${e.id}/enroll`, { video_id: Number(pick) }).then(() => setPick(""))}>Assign an arm by random draw</Button>
              <p className="w-full text-xs text-muted">The arm is drawn from the plan&rsquo;s seed before publication and cannot be changed. The video&rsquo;s own page then tells whoever makes it what to do.</p>
            </div>
          )}
          {e.status === "DESIGN" && isOwner && (
            <div className="flex gap-2">
              <Button variant="primary" busy={busy === "start"} onClick={() => act("start", `/api/experiments/${e.id}/start`)}>Start (registers it on the board)</Button>
              <Button busy={busy === "cancel"} onClick={() => act("cancel", `/api/experiments/${e.id}/cancel`, { reason: "not going ahead" })}>Cancel</Button>
            </div>
          )}

          {full?.videos && full.videos.length > 0 && (
            <div className="overflow-x-auto">
              <Table>
                <thead><tr><Th>Video</Th><Th>Arm</Th><Th>Figure</Th></tr></thead>
                <tbody>
                  {full.videos.map((v) => (
                    <tr key={v.video_id}><Td>{v.title}</Td><Td>{v.arm}</Td>
                      <Td>{v.status === "complete" ? <b>{nf.format(v.value!)}</b> : <><Badge tone="warn">{v.status}</Badge> <span className="text-xs text-muted">{v.reason}</span></>}</Td></tr>
                  ))}
                </tbody>
              </Table>
            </div>
          )}

          {e.status === "RUNNING" && canWrite && <Button busy={busy === "analyse"} onClick={() => act("analyse", `/api/experiments/${e.id}/analyse`)}>Analyse now (every look is counted)</Button>}
          {a && (
            <div className="rounded-lg border border-line p-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={conclusionTone[a.conclusion] ?? "neutral"}>{titleCase(a.conclusion.replace(/_/g, " "))}</Badge>
                <span className="text-xs text-muted">look {a.looks} · {fmtDate(a.analysed_at)} · confidence {a.confidence}</span>
              </div>
              <p className="mt-1">{a.summary}</p>
              <p className="mt-1 text-xs text-muted">Finished videos: control {a.control.n}, treatment {a.treatment.n}. {a.p_value !== null && `p = ${a.p_value.toFixed(3)}. `}{a.method}</p>
              <ul className="mt-1 list-disc pl-5 text-xs text-muted">{a.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
            </div>
          )}
          {e.status === "RUNNING" && isOwner && a && (
            <div className="flex flex-wrap items-end gap-2 rounded-lg bg-surface2 p-3">
              <label><span className="mb-1 block text-xs font-medium">Your decision (suggested: {a.suggested_decision})</span>
                <select className={inputCls} value={decision} onChange={(x) => setDecision(x.target.value)}>
                  <option value="">Choose…</option><option value="adopt">Adopt as an instruction</option><option value="revise">Revise the idea</option><option value="repeat">Repeat with new videos</option><option value="reject">Reject</option>
                </select></label>
              <label className="min-w-0 grow"><span className="mb-1 block text-xs font-medium">Why</span><input className={inputCls} value={note} onChange={(x) => setNote(x.target.value)} /></label>
              <Button variant="primary" busy={busy === "decide"} disabled={!decision || !note.trim()} onClick={() => act("decide", `/api/experiments/${e.id}/decide`, { decision, note })}>Record decision</Button>
              <Button busy={busy === "cancel"} onClick={() => act("cancel", `/api/experiments/${e.id}/cancel`, { reason: note || "cancelled by the owner" })}>Cancel experiment</Button>
            </div>
          )}
          {e.decision_note && <p className="text-xs text-muted">Decision ({e.decision}) by {e.decided_by}: {e.decision_note}</p>}
        </div>
      )}
    </li>
  );
}

function Experiments({ data, canWrite, isOwner, reload }: { data: ImprovementOverview; canWrite: boolean; isOwner: boolean; reload: () => void }) {
  const [form, setForm] = useState(false);
  return (
    <div className="space-y-4">
      <Banner tone="info">An experiment shows whether a change moved a figure, if the arms were drawn at random before publication and made alike apart from the change. It never proves why. Below the planned number of finished videos no test is run, so there is nothing to misread early.</Banner>
      {canWrite && <div><Button variant="primary" onClick={() => setForm((f) => !f)}>{form ? "Close" : "New experiment"}</Button></div>}
      {form && <NewExperiment onDone={() => { setForm(false); reload(); }} />}
      {data.experiments.length === 0
        ? <Empty title="No experiments yet" hint="Each one needs at least five videos per arm, so plan them for a channel that publishes regularly." />
        : <ul className="space-y-3">{data.experiments.map((x) => <ExperimentCard key={x.id} summary={x} canWrite={canWrite} isOwner={isOwner} onChanged={reload} />)}</ul>}
    </div>
  );
}

// ---------- compare ----------

function Compare() {
  const [channel, setChannel] = useState(CHANNELS[0][0]);
  const [metric, setMetric] = useState("views");
  const [days, setDays] = useState("7");
  const m = METRICS.find((x) => x[0] === metric)!;
  const { data, error } = useApi<CompareResponse>(`/api/compare?channel=${channel}&metric=${metric}&aggregate=${m[2][0]}&window_days=${Number(days) || 7}`);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-3 text-sm">
        <label><span className="mr-1 text-muted">Channel</span><select className={inputCls} value={channel} onChange={(e) => setChannel(e.target.value)}>{CHANNELS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
        <label><span className="mr-1 text-muted">Figure</span><select className={inputCls} value={metric} onChange={(e) => setMetric(e.target.value)}>{METRICS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
        <label><span className="mr-1 text-muted">First</span><input type="number" min="1" max="90" className={`${inputCls} w-20`} value={days} onChange={(e) => setDays(e.target.value)} /><span className="ml-1 text-muted">days</span></label>
      </div>
      <ErrorNote error={error} />
      {data && (
        <>
          <Banner tone="warn">{data.caveats[0]}</Banner>
          <p className="text-sm">
            {data.metric_description}. {data.summary.n} of {data.summary.of} video(s) have complete figures
            {data.summary.median !== undefined && <>; median {nf.format(data.summary.median)}, range {nf.format(data.summary.min!)} to {nf.format(data.summary.max!)}</>}.
          </p>
          {data.summary.note && <p className="text-sm text-muted">{data.summary.note}</p>}
          {data.videos.length === 0 ? <Empty title="No published videos" hint="Videos appear here once they are public." /> : (
            <div className="overflow-x-auto">
              <Table>
                <thead><tr><Th>#</Th><Th>Video</Th><Th>Figure</Th><Th>Versus the channel median</Th><Th>Experiment</Th></tr></thead>
                <tbody>
                  {[...data.videos].sort((a, b) => (a.rank ?? 999) - (b.rank ?? 999)).map((v) => (
                    <tr key={v.video_id}>
                      <Td>{v.rank ?? ""}</Td><Td>{v.title}</Td>
                      <Td>{v.status === "complete" ? <b>{nf.format(v.value!)}</b> : <><Badge tone="warn">unavailable</Badge> <span className="text-xs text-muted">{v.reason}</span></>}</Td>
                      <Td>{v.versus_median_percent !== undefined ? `${v.versus_median_percent > 0 ? "+" : ""}${v.versus_median_percent}%` : ""}</Td>
                      <Td>{v.experiment ? `${v.experiment} (${v.arm})` : ""}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </div>
          )}
          <ul className="list-disc space-y-0.5 pl-5 text-xs text-muted">{data.caveats.slice(1).map((c) => <li key={c}>{c}</li>)}</ul>
        </>
      )}
    </div>
  );
}

export default function LearningPage() {
  const { can, user } = useAuth();
  const allowed = can("diagnostics:read");
  const { data, error, loading, reload } = useApi<ImprovementOverview>(allowed ? "/api/improvement/overview" : null, { refreshMs: 60000 });
  const [tab, setTab] = useState<"lessons" | "experiments" | "compare">(allowed ? "lessons" : "compare");
  const tabs: { id: "lessons" | "experiments" | "compare"; label: string }[] = [
    ...(allowed ? [{ id: "lessons" as const, label: "Lessons" }, { id: "experiments" as const, label: "Experiments" }] : []),
    { id: "compare" as const, label: "Compare videos" },
  ];
  return (
    <>
      <PageHeader title="Learning" description="What the company has learned from its own work, what it is testing, and how its videos compare. A lesson becomes an instruction only after investigation, the CEO's check of its evidence and your approval, and it only ever reaches the workers of its own channel." />
      <Tabs tabs={tabs} value={tab} onChange={setTab} />
      {tab !== "compare" && loading && !data && <Loading />}
      {tab !== "compare" && error && !data && <ErrorNote error={error} />}
      {tab === "lessons" && data && <Lessons data={data} canWrite={can("board:activate")} reload={reload} />}
      {tab === "experiments" && data && <Experiments data={data} canWrite={can("video:manage")} isOwner={user?.role === "owner"} reload={reload} />}
      {tab === "compare" && <Compare />}
    </>
  );
}
