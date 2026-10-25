"use client";

import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { FindingList } from "@/components/findings";
import { Badge, Button, Card, ErrorNote, inputCls, List, Loading, StatusBadge, Unavailable } from "@/components/ui";
import { fmtDate } from "@/lib/format";
import type { ConceptView, Finding, MetadataSetView, Packaging } from "@/lib/types";

function ts(s: number): string {
  const t = Math.round(s), m = Math.floor(t / 60), sec = t % 60;
  return `${m}:${String(sec).padStart(2, "0")}`;
}

/** What a manager must do about a set before it can be approved: waive each 'revise' finding with a reason, or fix it. */
function ApprovePanel({ findings, url, onDone, label }: { findings: Finding[]; url: string; onDone: () => Promise<void>; label: string }) {
  const [note, setNote] = useState("");
  const [waive, setWaive] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string } | null>(null);
  const blocking = findings.filter((f) => f.severity === "block");
  const revise = findings.filter((f) => f.severity === "revise");
  const submit = async () => {
    setBusy(true); setErr(null);
    try { await api(url, { method: "POST", body: { note, waive: Object.fromEntries(Object.entries(waive).filter(([, v]) => v.trim())) } }); await onDone(); }
    catch (e) { setErr(e as Error); } finally { setBusy(false); }
  };
  return (
    <div className="mt-3 space-y-3 rounded-xl border border-line p-3 text-sm">
      <ErrorNote error={err} />
      {blocking.length > 0 && <p className="text-danger">{blocking.length} item(s) must be fixed first; they cannot be waived. Create a new version.</p>}
      {revise.length > 0 && blocking.length === 0 && (
        <div className="space-y-2">
          <p className="text-muted">Each item below must be fixed in a new version, or accepted here with a reason that is kept on record.</p>
          {revise.map((f) => (
            <label key={f.code} className="block"><span className="mb-1 block font-medium">{f.message}</span>
              <input className={inputCls} placeholder="Accept because… (leave empty to leave it unresolved)" value={waive[f.code] ?? ""} onChange={(e) => setWaive({ ...waive, [f.code]: e.target.value })} /></label>
          ))}
        </div>
      )}
      <label className="block"><span className="mb-1 block font-medium">Reason for approving (kept with the approval)</span>
        <input className={inputCls} value={note} onChange={(e) => setNote(e.target.value)} /></label>
      <Button variant="primary" busy={busy} disabled={!note.trim() || blocking.length > 0} onClick={submit}>{label}</Button>
    </div>
  );
}

function MetadataBlock({ set, canWrite, reload, approved }: { set: MetadataSetView; canWrite: boolean; reload: () => Promise<void>; approved: boolean }) {
  const [asking, setAsking] = useState(false);
  const f = set.findings ?? [];
  const approvable = canWrite && ["CHECKED", "NEEDS_REVISION"].includes(set.status);
  return (
    <div className="space-y-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={set.status} /><Badge>version {set.version}</Badge><Badge>{set.generated_by === "template-v1" ? "template draft" : set.generated_by}</Badge>
        {set.approved_by && <span className="text-muted">approved by {set.approved_by} {set.approved_at ? fmtDate(set.approved_at) : ""}</span>}
        {!approved && <span className="text-muted">written by {set.created_by}</span>}
      </div>
      <div><h4 className="mb-1 font-medium">Title</h4><p className="text-base">{set.chosen_title}</p></div>
      {set.title_candidates && set.title_candidates.length > 1 && (
        <details><summary className="cursor-pointer font-medium">Title options ({set.title_candidates.length}), each a hypothesis</summary>
          <ul className="mt-2 space-y-2">{set.title_candidates.map((c) => (
            <li key={c.title} className="rounded-xl border border-line p-2.5">
              <div className={c.title === set.chosen_title ? "font-medium" : ""}>{c.title}{c.title === set.chosen_title && <Badge tone="accent"> chosen</Badge>}</div>
              <div className="mt-1 flex flex-wrap gap-1">{Object.entries(c.criteria).map(([k, v]) => (
                <Badge key={k} tone={v.state === "ok" ? "ok" : v.state === "warn" ? "warn" : "neutral"} title={v.detail}>{k.replace(/_/g, " ")}: {v.state}</Badge>))}</div>
            </li>))}</ul></details>
      )}
      <div><h4 className="mb-1 font-medium">Description</h4><pre className="whitespace-pre-wrap rounded-xl bg-surface2 p-3 font-sans text-sm">{set.description}</pre></div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div><h4 className="mb-1 font-medium">Tags</h4><div className="flex flex-wrap gap-1">{(set.tags ?? []).map((t) => <Badge key={t}>{t}</Badge>)}{(set.tags ?? []).length === 0 && <span className="text-muted">None</span>}</div></div>
        <div><h4 className="mb-1 font-medium">Hashtags</h4><div className="flex flex-wrap gap-1">{(set.hashtags ?? []).map((t) => <Badge key={t} tone="info">{t}</Badge>)}{(set.hashtags ?? []).length === 0 && <span className="text-muted">None</span>}</div></div>
      </div>
      <div><h4 className="mb-1 font-medium">Chapters</h4>
        {(set.chapters ?? []).length === 0 ? <p className="text-muted">None. Chapters are offered only from timestamps measured in the finished audio.</p> :
          <List items={(set.chapters ?? []).map((c) => <span key={c.start_s}><code>{ts(c.start_s)}</code> {c.title}</span>)} />}</div>
      <div><h4 className="mb-1 font-medium">Recommendation</h4><p className="text-muted">{set.recommendation}</p></div>
      <div><h4 className="mb-1 font-medium">Checks</h4><FindingList findings={f} /></div>
      {approvable && !asking && <Button variant="primary" onClick={() => setAsking(true)}>Approve this metadata…</Button>}
      {approvable && asking && <ApprovePanel findings={f} url={`/api/metadata/${set.id}/approve`} label="Approve and apply to the video" onDone={async () => { setAsking(false); await reload(); }} />}
    </div>
  );
}

function ConceptBlock({ c, canWrite, reload }: { c: ConceptView; canWrite: boolean; reload: () => Promise<void> }) {
  const [asking, setAsking] = useState(false);
  return (
    <div className="space-y-3 text-sm">
      <div className="flex flex-wrap items-center gap-2"><StatusBadge status={c.status} /><Badge>version {c.version}</Badge>{c.approved_by && <span className="text-muted">approved by {c.approved_by}</span>}</div>
      <div className="flex items-center gap-3">
        <div className="flex h-16 w-28 shrink-0 items-center justify-center rounded-lg px-1 text-center text-xs font-bold"
          style={{ background: c.palette[1] ?? "#333", color: c.palette[0] ?? "#fff" }} aria-label="Colour preview">{c.overlay_text || "No text"}</div>
        <div className="min-w-0"><p>{c.composition}</p><p className="text-muted">{c.relation_to_content}</p></div>
      </div>
      <FindingList findings={c.findings} />
      {canWrite && ["CHECKED", "NEEDS_REVISION"].includes(c.status) && (asking
        ? <ApprovePanel findings={c.findings} url={`/api/thumbnail-concepts/${c.id}/approve`} label="Approve this concept" onDone={async () => { setAsking(false); await reload(); }} />
        : <Button variant="primary" onClick={() => setAsking(true)}>Approve this concept…</Button>)}
    </div>
  );
}

function ConceptForm({ videoId, reload }: { videoId: number; reload: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ overlay_text: "", composition: "", text: "#FFFFFF", bg: "#1B3A57", relation: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string } | null>(null);
  if (!open) return <Button onClick={() => setOpen(true)}>Propose a thumbnail concept</Button>;
  const submit = async () => {
    setBusy(true); setErr(null);
    try { await api(`/api/videos/${videoId}/thumbnail-concepts`, { method: "POST", body: { overlay_text: f.overlay_text, composition: f.composition, palette: [f.text, f.bg], relation_to_content: f.relation } }); setOpen(false); await reload(); }
    catch (e) { setErr(e as Error); } finally { setBusy(false); }
  };
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setF({ ...f, [k]: e.target.value });
  return (
    <div className="space-y-3 rounded-xl border border-line p-3 text-sm">
      <ErrorNote error={err} />
      <label className="block"><span className="mb-1 block font-medium">Text on the thumbnail (5 words at most)</span><input className={inputCls} value={f.overlay_text} onChange={set("overlay_text")} /></label>
      <label className="block"><span className="mb-1 block font-medium">What it shows</span><textarea className={inputCls} rows={2} value={f.composition} onChange={set("composition")} /></label>
      <label className="block"><span className="mb-1 block font-medium">How it reflects what is in the video</span><input className={inputCls} value={f.relation} onChange={set("relation")} /></label>
      <div className="flex gap-4">
        <label className="block"><span className="mb-1 block font-medium">Text colour</span><input type="color" className="h-9 w-16" value={f.text} onChange={set("text")} /></label>
        <label className="block"><span className="mb-1 block font-medium">Background colour</span><input type="color" className="h-9 w-16" value={f.bg} onChange={set("bg")} /></label>
      </div>
      <div className="flex gap-2"><Button variant="primary" busy={busy} onClick={submit}>Check this concept</Button><Button onClick={() => setOpen(false)}>Cancel</Button></div>
    </div>
  );
}

export function PackagingCards({ videoId, canWrite }: { videoId: number; canWrite: boolean }) {
  const { data, error, loading, reload } = useApi<Packaging>(`/api/videos/${videoId}/packaging`, { refreshMs: 30000 });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string } | null>(null);
  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorNote error={error} />;
  if (!data) return null;
  const reloadAsync = async () => { await reload(); };
  const draft_ = async () => {
    setBusy(true); setErr(null);
    try { await api(`/api/videos/${videoId}/metadata/generate`, { method: "POST", body: {} }); await reload(); } catch (e) { setErr(e as Error); } finally { setBusy(false); }
  };
  const m = data.metadata;
  const draft = m.latest && !["SUPERSEDED", "APPROVED"].includes(m.latest.status) ? m.latest : null;
  const t = data.thumbnail_concept;
  const cDraft = t.latest && !["SUPERSEDED", "APPROVED"].includes(t.latest.status) ? t.latest : null;

  return (
    <>
      <Card title="Title, description and tags" subtitle="Drafted from the brief and script, checked by fixed rules, then approved by a manager. Nothing reaches the video until it is approved."
        action={<Badge tone={m.approved ? (m.matches_video ? "ok" : "danger") : "warn"}>{m.approved ? (m.matches_video ? "approved" : "edited since approval") : "not approved"}</Badge>}>
        <ErrorNote error={err} />
        {data.gate_issues.length > 0 && <ul className="mb-3 space-y-1 text-sm">{data.gate_issues.map((i) => <li key={i.code} className="text-warn">{i.message}</li>)}</ul>}
        {m.approved && <section><h3 className="mb-2 text-sm font-semibold">Approved version {m.approved.version} (this is what the video carries)</h3>
          <MetadataBlock set={m.approved} canWrite={canWrite} reload={reloadAsync} approved /></section>}
        {draft && <section className={m.approved ? "mt-6 border-t border-line pt-4" : ""}><h3 className="mb-2 text-sm font-semibold">{m.approved ? `Newer draft, version ${draft.version}` : `Draft, version ${draft.version}`}</h3>
          <MetadataBlock set={draft} canWrite={canWrite} reload={reloadAsync} approved={false} /></section>}
        {!m.approved && !draft && <p className="text-sm text-muted">No metadata has been drafted yet.</p>}
        {canWrite && <div className="mt-4"><Button busy={busy} onClick={draft_}>{m.approved || draft ? "Draft a new version" : "Draft metadata"}</Button>
          <span className="ml-3 text-xs text-muted">Template draft from the brief; no language model is connected, so wording is plain.</span></div>}
        <div className="mt-4"><Unavailable title="Search demand, keyword volume and past performance" reason="No search or analytics data exists, so no title is shown to perform better than another. Options are hypotheses." /></div>
        <details className="mt-3 text-xs text-muted"><summary className="cursor-pointer">Platform limits used by the checks</summary>
          <p className="mt-1">{data.limits_basis}.</p>
          <p>{Object.entries(data.limits).map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`).join(" · ")}</p></details>
      </Card>

      <Card title="Thumbnail concept" subtitle="Checked on paper before any image is made: text length, wording, measured contrast and resemblance to recent thumbnails.">
        {t.approved && <section><h3 className="mb-2 text-sm font-semibold">Approved concept, version {t.approved.version}</h3><ConceptBlock c={t.approved} canWrite={canWrite} reload={reloadAsync} /></section>}
        {cDraft && <section className={t.approved ? "mt-6 border-t border-line pt-4" : ""}><h3 className="mb-2 text-sm font-semibold">{t.approved ? "Newer proposal" : "Proposal"}, version {cDraft.version}</h3><ConceptBlock c={cDraft} canWrite={canWrite} reload={reloadAsync} /></section>}
        {!t.approved && !cDraft && <p className="text-sm text-muted">No concept yet.</p>}
        {canWrite && <div className="mt-4"><ConceptForm videoId={videoId} reload={reloadAsync} /></div>}
        <p className="mt-3 text-xs text-muted">{(t.latest ?? t.approved)?.note ?? "A concept on paper does not predict clicks; no performance data exists."}</p>
      </Card>
    </>
  );
}
