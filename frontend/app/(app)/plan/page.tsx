"use client";

import { useState } from "react";
import Link from "next/link";
import { api, useApi } from "@/lib/api";
import { useAuth } from "@/components/auth";
import { Badge, Button, Card, Drawer, Empty, ErrorNote, inputCls, KeyValue, List, Loading, PageHeader, StatusBadge, Table, Td, Th, Tabs, Unavailable } from "@/components/ui";
import { fmtDate, titleCase } from "@/lib/format";
import type { IdeaDetail, IdeaSummary, PlanResponse, Tone } from "@/lib/types";

const verdictTone: Record<string, Tone> = { distinct: "ok", overlaps: "warn", duplicate: "danger", ready: "ok", needs_provider: "warn" };
const channelNames: Record<string, string> = { "happy-birthday-songs": "Birthday Songs", "medicine-information": "Medicine Info", "ai-story-videos": "AI Stories" };

export default function PlanPage() {
  const { can } = useAuth();
  const { data, error, loading, reload } = useApi<PlanResponse>("/api/plan");
  const [tab, setTab] = useState<string>("happy-birthday-songs");
  const [open, setOpen] = useState<number | null>(null);
  const [showForm, setShowForm] = useState(false);
  const canWrite = can("task:create");

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorNote error={error} />;
  if (!data) return null;
  const channel = data.channels.find((c) => c.channel === tab) ?? data.channels[0];

  return (
    <>
      <PageHeader
        title="Content plan"
        description="Ideas for each channel, ordered by a score whose working is shown. The score is an ordering aid, not a forecast: search demand and past performance are unavailable until they are recorded with a source."
        actions={canWrite && <Button variant="primary" onClick={() => setShowForm((s) => !s)}>{showForm ? "Close form" : "Propose an idea"}</Button>}
      />
      {showForm && canWrite && <ProposeForm plan={data} defaultChannel={tab} onDone={(id) => { setShowForm(false); reload(); setOpen(id); }} />}

      <Tabs tabs={data.channels.map((c) => ({ id: c.channel, label: channelNames[c.channel] ?? c.channel_name }))} value={channel.channel} onChange={setTab} />
      <Card title={channel.channel_name} subtitle="Approved ideas first, then assessed ideas by score. Ideas that need a capability the company does not have yet are parked.">
        {channel.items.length === 0 ? (
          <Empty title="No ideas yet" hint="Propose one above. Each idea is checked against this channel's existing videos and what can be produced today." />
        ) : (
          <Table>
            <thead><tr><Th>#</Th><Th>Idea</Th><Th>Status</Th><Th>Score</Th><Th>Checks</Th></tr></thead>
            <tbody>{channel.items.map((i) => <Row key={i.id} item={i} onOpen={() => setOpen(i.id)} />)}</tbody>
          </Table>
        )}
      </Card>
      <Drawer open={open !== null} onClose={() => setOpen(null)} title="Idea">
        {open !== null && <IdeaPanel id={open} canWrite={canWrite} onChanged={reload} />}
      </Drawer>
    </>
  );
}

function Row({ item, onOpen }: { item: IdeaSummary; onOpen: () => void }) {
  const s = item.summary;
  return (
    <tr className="cursor-pointer hover:bg-surface2/60" onClick={onOpen}>
      <Td className="text-muted">{item.rank}</Td>
      <Td><button className="text-left font-medium text-accent hover:underline" onClick={onOpen}>{item.title}</button>
        <div className="text-xs text-muted">{item.audience}{item.planned_for ? ` · planned ${item.planned_for}` : ""}</div></Td>
      <Td><StatusBadge status={item.status} /></Td>
      <Td>{item.priority_score == null ? <span className="text-muted">—</span> : item.priority_score.toFixed(0)}</Td>
      <Td>
        <div className="flex flex-wrap gap-1">
          {s.distinctness && <Badge tone={verdictTone[s.distinctness]} title="Distinctness against this channel's existing work">{s.distinctness}</Badge>}
          {s.feasibility && <Badge tone={verdictTone[s.feasibility]} title="Can it be produced with what exists today?">{s.feasibility === "ready" ? "producible now" : "needs a provider"}</Badge>}
          <Badge title="Recorded sources">{s.sources ?? 0} source{s.sources === 1 ? "" : "s"}</Badge>
          <Badge tone={s.demand === "recorded" ? "info" : "neutral"} title="Search or audience demand">{s.demand === "recorded" ? "demand noted" : "demand unavailable"}</Badge>
        </div>
      </Td>
    </tr>
  );
}

function IdeaPanel({ id, canWrite, onChanged }: { id: number; canWrite: boolean; onChanged: () => void }) {
  const { data: d, error, loading, reload } = useApi<IdeaDetail>(`/api/ideas/${id}`);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<{ message: string } | null>(null);
  const [note, setNote] = useState("");
  const [planned, setPlanned] = useState("");
  const [started, setStarted] = useState<number | null>(null);

  if (loading && !d) return <Loading />;
  if (error) return <ErrorNote error={error} />;
  if (!d) return null;
  const a = d.assessment;

  const act = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key); setErr(null);
    try { await fn(); await reload(); onChanged(); } catch (e) { setErr(e as Error); } finally { setBusy(null); }
  };
  const decide = (approve: boolean) => act(approve ? "approve" : "reject", () =>
    api(`/api/ideas/${id}/decision`, { method: "POST", body: { approve, note, planned_for: approve && planned ? planned : null } }));
  const start = () => act("start", async () => { const r = await api<{ video_id: number }>(`/api/ideas/${id}/start`, { method: "POST", body: {} }); setStarted(r.video_id); });
  const startWithAi = () => act("start-ai", async () => { const r = await api<{ video_id: number }>(`/api/ideas/${id}/start-with-ai`, { method: "POST", body: {} }); setStarted(r.video_id); });

  return (
    <div className="space-y-5 text-sm">
      <div>
        <h3 className="text-base font-semibold">{d.title}</h3>
        <div className="mt-1 flex flex-wrap gap-1.5"><StatusBadge status={d.status} /><Badge>{d.origin}</Badge>{d.priority_score != null && <Badge tone="accent">score {d.priority_score.toFixed(0)}</Badge>}</div>
        <p className="mt-2">{d.concept}</p>
        <p className="mt-1 text-muted">For {d.audience}</p>
        {d.rationale && <p className="mt-1 text-muted">Why: {d.rationale}</p>}
      </div>
      <ErrorNote error={err} />
      {started !== null && <p className="rounded-xl bg-ok-soft p-3 text-ok">Started. <Link className="underline" href={`/videos/${started}`}>Open video #{started}</Link></p>}
      {d.video_id && started === null && <p><Link className="text-accent hover:underline" href={`/videos/${d.video_id}`}>Open video #{d.video_id}</Link></p>}
      {d.decision_note && <p className="rounded-xl bg-surface2 p-3"><span className="font-medium">Decision ({d.decided_by}):</span> {d.decision_note}</p>}

      <section>
        <h4 className="mb-1.5 font-medium">Distinctness: <Badge tone={verdictTone[a.distinctness.verdict]}>{a.distinctness.verdict}</Badge></h4>
        <p className="text-muted">Compared with {a.distinctness.compared_against} existing video(s) and open idea(s) on this channel. {a.distinctness.basis.charAt(0).toUpperCase() + a.distinctness.basis.slice(1)}.</p>
        {a.distinctness.nearest.length > 0 && <List items={a.distinctness.nearest.map((n) => <span key={`${n.kind}${n.id}`}>{n.kind} #{n.id} “{n.title}” — topic overlap {Math.round(n.topic_overlap * 100)}%</span>)} />}
      </section>

      <section>
        <h4 className="mb-1.5 font-medium">Can it be produced today? <Badge tone={verdictTone[a.feasibility.verdict]}>{a.feasibility.verdict === "ready" ? "yes" : "not yet"}</Badge></h4>
        {a.feasibility.requirements.length === 0 ? <p className="text-muted">No requirements were stated.</p> :
          <List items={a.feasibility.requirements.map((r) => <span key={r.requirement}>{r.label}: <Badge tone={r.ready ? "ok" : "warn"}>{r.ready ? "ready" : r.state.replace(/_/g, " ")}</Badge> <span className="text-muted">{r.ready ? "" : r.detail}</span></span>)} />}
        {a.feasibility.note && <p className="mt-1 text-muted">{a.feasibility.note}</p>}
      </section>

      <section>
        <h4 className="mb-1.5 font-medium">Evidence</h4>
        <KeyValue items={[
          ["Sources", `${a.evidence.sources} (${Object.entries(a.evidence.by_class).filter(([, n]) => n > 0).map(([k, n]) => `${n} ${k}`).join(", ") || "none"})`],
          ["Demand", a.evidence.demand.state === "recorded" ? a.evidence.demand.note : "Unavailable — no trend, search or audience data has been recorded."],
        ]} />
        {a.evidence.problems.length > 0 && <div className="mt-2"><List items={a.evidence.problems.map((p) => <span key={p} className="text-danger">{p}</span>)} /></div>}
        {a.evidence.flagged_notes > 0 && <p className="mt-2 text-warn">{a.evidence.flagged_notes} note(s) contain instruction-like text. It is stored as data and never followed.</p>}
      </section>

      <section>
        <h4 className="mb-1.5 font-medium">How the score was worked out</h4>
        {a.priority.ineligible ? <p className="text-danger">Score 0: {a.priority.ineligible_reason}.</p> : (
          <Table>
            <thead><tr><Th>Part</Th><Th>Value</Th><Th>Weight</Th><Th>Basis</Th></tr></thead>
            <tbody>{Object.entries(a.priority.components).map(([k, c]) => (
              <tr key={k}><Td className="font-medium">{titleCase(k.replace(/_/g, " "))}</Td>
                <Td>{c.value == null ? <span className="text-muted">{c.basis.startsWith("not required") ? "not applicable" : "unavailable"}</span> : `${Math.round(c.value * 100)}%`}</Td><Td>{c.weight}</Td>
                <Td className="text-muted">{c.basis}<div className="text-xs">{c.detail}</div></Td></tr>
            ))}</tbody>
          </Table>
        )}
        <p className="mt-1 text-xs text-muted">{a.priority.note}</p>
      </section>

      {[...a.automatic_risks, ...a.risks].length > 0 && <section><h4 className="mb-1.5 font-medium">Risks</h4><List items={[...a.automatic_risks, ...a.risks]} /></section>}

      <section>
        <h4 className="mb-1.5 font-medium">Research notes</h4>
        {d.notes.length === 0 ? <p className="text-muted">None recorded yet.</p> : (
          <ul className="space-y-2">{d.notes.map((n) => (
            <li key={n.id} className="rounded-xl border border-line p-3">
              <div className="flex flex-wrap items-center gap-1.5"><Badge>{titleCase(n.kind.replace(/_/g, " "))}</Badge><Badge tone={n.source_class === "authoritative" ? "ok" : "neutral"} title={n.source_class_basis}>{n.source_class}</Badge><Badge>{n.method.replace(/_/g, " ")}</Badge>
                {n.injection_flags.length > 0 && <Badge tone="warn" title={n.injection_flags.join(", ")}>contains instruction-like text</Badge>}</div>
              {n.url && <a className="mt-1 block break-all text-accent hover:underline" href={n.url} target="_blank" rel="noopener noreferrer">{n.title || n.url}</a>}
              <div className="mt-1 text-xs text-muted">{n.publisher} · retrieved {fmtDate(n.retrieved_at)}{n.supports ? ` · supports: ${n.supports}` : ""}</div>
              {n.excerpt && <blockquote className="mt-2 border-l-2 border-line pl-3 text-xs text-muted">{n.excerpt}</blockquote>}
            </li>
          ))}</ul>
        )}
        {canWrite && !["REJECTED", "IN_PRODUCTION"].includes(d.status) && <NoteForm ideaId={d.id} onAdded={async () => { await reload(); onChanged(); }} />}
      </section>

      {canWrite && !["REJECTED", "IN_PRODUCTION"].includes(d.status) && (
        <section className="space-y-3 border-t border-line pt-4">
          <h4 className="font-medium">Decide</h4>
          <label className="block"><span className="mb-1 block font-medium">Reason (kept with the decision)</span>
            <input className={inputCls} value={note} onChange={(e) => setNote(e.target.value)} /></label>
          <label className="block"><span className="mb-1 block font-medium">Planned for (optional)</span>
            <input className={inputCls} type="date" value={planned} onChange={(e) => setPlanned(e.target.value)} /></label>
          <div className="flex flex-wrap gap-2">
            <Button busy={busy === "assess"} onClick={() => act("assess", () => api(`/api/ideas/${id}/assess`, { method: "POST", body: {} }))}>Re-assess</Button>
            <Button variant="primary" busy={busy === "approve"} disabled={!note.trim()} onClick={() => decide(true)}>Approve</Button>
            <Button variant="danger" busy={busy === "reject"} disabled={!note.trim()} onClick={() => decide(false)}>Reject</Button>
            {d.status === "APPROVED" && <Button variant="primary" busy={busy === "start-ai"} onClick={startWithAi}>Start with the AI company</Button>}
            {d.status === "APPROVED" && <Button busy={busy === "start"} onClick={start} title="Creates the video only; you run each step yourself">Start by hand</Button>}
          </div>
          {d.status === "APPROVED" && <p className="text-xs text-muted">“Start with the AI company” has the AI write the brief and the lyrics or script, managers check each step, and the video is built and checked. It stops at every decision that is yours: rights concerns, medical review and publication approval. It uses your Claude plan (see the AI Brain page).</p>}
          <p className="text-xs text-muted">Approval re-runs the assessment first, so it rests on what is true now. An idea that needs a capability that does not exist yet is parked instead of approved.</p>
        </section>
      )}
    </div>
  );
}

function NoteForm({ ideaId, onAdded }: { ideaId: number; onAdded: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ kind: "source", url: "", title: "", supports: "", text: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string } | null>(null);
  if (!open) return <Button className="mt-2" onClick={() => setOpen(true)}>Add a note</Button>;
  const submit = async () => {
    setBusy(true); setErr(null);
    try { await api(`/api/ideas/${ideaId}/notes`, { method: "POST", body: f }); setF({ kind: "source", url: "", title: "", supports: "", text: "" }); setOpen(false); await onAdded(); }
    catch (e) { setErr(e as Error); } finally { setBusy(false); }
  };
  return (
    <div className="mt-3 space-y-2 rounded-xl border border-line p-3">
      <ErrorNote error={err} />
      <label className="block"><span className="mb-1 block font-medium">Kind</span>
        <select className={inputCls} value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
          {["source", "topic_info", "trend", "competitor", "audience_question"].map((k) => <option key={k} value={k}>{titleCase(k.replace(/_/g, " "))}</option>)}</select></label>
      <label className="block"><span className="mb-1 block font-medium">Web address</span><input className={inputCls} value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} placeholder="https://" /></label>
      <label className="block"><span className="mb-1 block font-medium">Title</span><input className={inputCls} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></label>
      <label className="block"><span className="mb-1 block font-medium">The claim it supports</span><input className={inputCls} value={f.supports} onChange={(e) => setF({ ...f, supports: e.target.value })} /></label>
      <label className="block"><span className="mb-1 block font-medium">Short excerpt or the question itself</span><textarea className={inputCls} rows={3} value={f.text} onChange={(e) => setF({ ...f, text: e.target.value })} /></label>
      <p className="text-xs text-muted">Recorded as yours, dated now. Text from outside is stored as data and never followed as an instruction. Numbers (views, search volume) can be added through the API with their own source and date.</p>
      <div className="flex gap-2"><Button variant="primary" busy={busy} onClick={submit}>Save note</Button><Button onClick={() => setOpen(false)}>Cancel</Button></div>
    </div>
  );
}

function ProposeForm({ plan, defaultChannel, onDone }: { plan: PlanResponse; defaultChannel: string; onDone: (id: number) => void }) {
  const [f, setF] = useState({ channel: defaultChannel, title: "", concept: "", audience: "", rationale: "", relevance: "", cost: "", minutes: "", risks: "" });
  const [reqs, setReqs] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string } | null>(null);
  const submit = async () => {
    setBusy(true); setErr(null);
    try {
      const d = await api<IdeaDetail>("/api/ideas", { method: "POST", body: {
        channel: f.channel, title: f.title, concept: f.concept, audience: f.audience, rationale: f.rationale,
        audience_relevance: f.relevance ? Number(f.relevance) : null, est_cost_usd: f.cost !== "" ? Number(f.cost) : null,
        est_production_minutes: f.minutes ? Number(f.minutes) : null, requires: reqs, risks: f.risks.split("\n").map((x) => x.trim()).filter(Boolean) } });
      onDone(d.id);
    } catch (e) { setErr(e as Error); } finally { setBusy(false); }
  };
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  return (
    <Card title="Propose an idea" subtitle="It is assessed straight away: distinctness, what can be produced today, and evidence." className="mb-6">
      <div className="grid gap-4 md:grid-cols-2">
        <ErrorNote error={err} />
        <label className="block"><span className="mb-1 block text-sm font-medium">Channel</span>
          <select className={inputCls} value={f.channel} onChange={set("channel")}>{plan.channels.map((c) => <option key={c.channel} value={c.channel}>{c.channel_name}</option>)}</select></label>
        <label className="block"><span className="mb-1 block text-sm font-medium">Title</span><input className={inputCls} value={f.title} onChange={set("title")} /></label>
        <label className="block md:col-span-2"><span className="mb-1 block text-sm font-medium">Concept</span><textarea className={inputCls} rows={3} value={f.concept} onChange={set("concept")} /></label>
        <label className="block"><span className="mb-1 block text-sm font-medium">Audience</span><input className={inputCls} value={f.audience} onChange={set("audience")} /></label>
        <label className="block"><span className="mb-1 block text-sm font-medium">Why this idea (optional)</span><input className={inputCls} value={f.rationale} onChange={set("rationale")} /></label>
        <label className="block"><span className="mb-1 block text-sm font-medium">Audience relevance, your judgement (1–5, optional)</span>
          <select className={inputCls} value={f.relevance} onChange={set("relevance")}><option value="">Not given</option>{[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
        <label className="block"><span className="mb-1 block text-sm font-medium">Estimated cost in USD (optional)</span><input className={inputCls} type="number" min="0" step="0.01" value={f.cost} onChange={set("cost")} /></label>
        <label className="block"><span className="mb-1 block text-sm font-medium">Estimated production minutes (optional)</span><input className={inputCls} type="number" min="1" value={f.minutes} onChange={set("minutes")} /></label>
        <label className="block"><span className="mb-1 block text-sm font-medium">Known risks, one per line (optional)</span><textarea className={inputCls} rows={2} value={f.risks} onChange={set("risks")} /></label>
        <fieldset className="md:col-span-2"><legend className="mb-1 text-sm font-medium">What it needs</legend>
          <div className="flex flex-wrap gap-x-5 gap-y-1.5 text-sm">{plan.requirements.map((r) => (
            <label key={r.key} className="flex items-center gap-1.5"><input type="checkbox" checked={reqs.includes(r.key)} onChange={(e) => setReqs(e.target.checked ? [...reqs, r.key] : reqs.filter((k) => k !== r.key))} />{r.label}</label>
          ))}</div></fieldset>
      </div>
      <div className="mt-4"><Button variant="primary" busy={busy} onClick={submit}>Propose and assess</Button></div>
      <div className="mt-4"><Unavailable title="Search demand and trends" reason="Nothing is estimated. Record observations with a source and a date on the idea; until then demand shows as unavailable." /></div>
    </Card>
  );
}
