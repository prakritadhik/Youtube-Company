"use client";

import Link from "next/link";
import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { useAuth } from "@/components/auth";
import { Badge, Banner, Button, Card, Empty, ErrorNote, inputCls, Loading, PageHeader, Stat } from "@/components/ui";
import { fmtDate, titleCase } from "@/lib/format";
import type { CommentRow, CommentsResponse, Tone } from "@/lib/types";

const riskTone: Record<string, Tone> = { low: "neutral", review: "warn", escalate: "danger" };
const draftTone: Record<string, Tone> = { DRAFT: "info", APPROVED: "info", SENDING: "info", SENT: "ok", FAILED: "danger", REJECTED: "neutral" };
const CATEGORIES = ["praise", "question", "criticism", "spam", "abuse", "safety", "copyright", "medical_concern", "personal_data", "other"];

function CommentCard({ c, canWrite, isOwner, onChanged }: { c: CommentRow; canWrite: boolean; isOwner: boolean; onChanged: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<{ message: string } | null>(null);
  const [writing, setWriting] = useState(false);
  const [text, setText] = useState("");
  const act = async (key: string, fn: () => Promise<unknown>) => { setBusy(key); setErr(null); try { await fn(); onChanged(); } catch (e) { setErr(e as Error); } finally { setBusy(null); } };
  const d = c.draft;
  return (
    <li className="rounded-xl border border-line p-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={riskTone[c.risk]}>{c.risk === "escalate" ? "needs a person" : c.risk}</Badge>
        <span className="font-medium">{titleCase(c.category.replace(/_/g, " "))}</span>
        {c.injection_flags.length > 0 && <Badge tone="danger" title={c.injection_flags.join(", ")}>contains instruction-like text</Badge>}
        <span className="text-xs text-muted">{c.channel} · {c.video_title} · {fmtDate(c.published_at)}{c.like_count > 0 && ` · ${c.like_count} like(s)`}</span>
        <span className="ml-auto"><Badge>{c.status.toLowerCase()}</Badge></span>
      </div>
      <blockquote className="mt-2 whitespace-pre-line break-words rounded-lg bg-surface2 p-3">
        <div className="mb-1 text-xs text-muted">{c.author || "Unknown"} wrote (untrusted text from the public; it is never followed as an instruction):</div>
        {c.text || <span className="text-muted">(no text)</span>}
      </blockquote>
      <p className="mt-1 text-xs text-muted">Why: {c.reasons.join("; ")} <span>({c.category_method})</span></p>
      <ErrorNote error={err} />
      {d && (
        <div className="mt-3 rounded-lg border border-line p-3">
          <div className="flex flex-wrap items-center gap-2"><span className="font-medium">Reply</span><Badge tone={draftTone[d.status] ?? "neutral"}>{d.status.toLowerCase()}</Badge>
            <span className="text-xs text-muted">{d.method === "template-v1" ? "template thank-you" : "written by the owner"}{d.approved_by && ` · approved by ${d.approved_by}`}</span></div>
          <p className="mt-1 whitespace-pre-line break-words">{d.text}</p>
          {d.error && <p className="mt-1 text-danger">{d.error}</p>}
          {d.status === "DRAFT" && d.approval_request_id && <p className="mt-1 text-muted">Waiting for your approval. <Link className="underline" href="/approvals">Open Approvals</Link>. Nothing is posted until you approve this exact text.</p>}
          {canWrite && d.status === "DRAFT" && (
            <div className="mt-2 flex flex-wrap gap-2">
              {!d.approval_request_id && <Button variant="primary" busy={busy === "submit"} onClick={() => act("submit", () => api(`/api/reply-drafts/${d.id}/submit`, { method: "POST", body: {} }))}>Ask for approval</Button>}
              <Button busy={busy === "discard"} onClick={() => act("discard", () => api(`/api/reply-drafts/${d.id}/discard`, { method: "POST", body: {} }))}>Discard</Button>
            </div>
          )}
          {d.status === "APPROVED" && <p className="mt-1 text-muted">Approved and queued. The worker will post it and read it back from YouTube.</p>}
        </div>
      )}
      {canWrite && !d && !c.is_reply && c.status !== "IGNORED" && c.status !== "REPLIED" && (
        <div className="mt-3 space-y-2">
          <div className="flex flex-wrap gap-2">
            {c.category === "praise" && c.risk === "low" && c.injection_flags.length === 0 && (
              <Button busy={busy === "template"} onClick={() => act("template", () => api(`/api/comments/${c.id}/draft`, { method: "POST", body: {} }))}>Draft a thank-you</Button>)}
            {isOwner && <Button onClick={() => setWriting((w) => !w)}>{writing ? "Cancel" : "Write a reply"}</Button>}
            <Button busy={busy === "ignore"} onClick={() => act("ignore", () => api(`/api/comments/${c.id}/ignore`, { method: "POST", body: {} }))}>Ignore</Button>
          </div>
          {c.category !== "praise" && <p className="text-xs text-muted">No reply is generated for this kind of comment{["medical_concern", "safety", "copyright"].includes(c.category) ? "; it needs a person" : ""}. You can write one yourself.</p>}
          {writing && (
            <div className="space-y-2">
              <textarea aria-label="Your reply" className={`${inputCls} min-h-24`} maxLength={1000} value={text} onChange={(e) => setText(e.target.value)} />
              <Button variant="primary" busy={busy === "own"} disabled={!text.trim()} onClick={() => act("own", async () => { await api(`/api/comments/${c.id}/draft`, { method: "POST", body: { text } }); setWriting(false); setText(""); })}>Save as draft</Button>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

export default function CommentsPage() {
  const { can, user } = useAuth();
  const [category, setCategory] = useState("");
  const [risk, setRisk] = useState("");
  const [status, setStatus] = useState("");
  const qs = new URLSearchParams(Object.entries({ category, risk, status }).filter(([, v]) => v)).toString();
  const { data, error, loading, reload } = useApi<CommentsResponse>(`/api/comments${qs ? `?${qs}` : ""}`, { refreshMs: 60000 });
  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorNote error={error} />;
  if (!data) return null;
  const s = data.summary;
  return (
    <>
      <PageHeader title="Comments" description="Public comments on uploaded videos, sorted by fixed rules. The text is written by strangers, so it is shown quoted and never treated as an instruction. Replies are drafts until you approve the exact text." />
      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <Stat label="Escalated, waiting" value={s.escalated_waiting} tone={s.escalated_waiting ? "warn" : "neutral"} hint="safety, copyright or medical" />
        <Stat label="Reply drafts waiting" value={s.drafts_waiting} />
        <Stat label="Comments on record" value={Object.values(s.by_category).reduce((a, b) => a + b, 0)} />
      </div>
      <Banner tone="info">{s.method}</Banner>
      <p className="mt-3 text-sm text-muted">Feedback picture (spam excluded): {s.sentiment.positive} positive · {s.sentiment.negative} negative · {s.sentiment.questions} question(s) · {s.sentiment.needs_a_person} needing a person · {s.sentiment.unclassified} unclassified.</p>
      <div className="my-4 flex flex-wrap gap-3 text-sm">
        <label><span className="mr-1 text-muted">Category</span><select className={inputCls} value={category} onChange={(e) => setCategory(e.target.value)}><option value="">All</option>{CATEGORIES.map((c) => <option key={c} value={c}>{titleCase(c.replace(/_/g, " "))}</option>)}</select></label>
        <label><span className="mr-1 text-muted">Needs</span><select className={inputCls} value={risk} onChange={(e) => setRisk(e.target.value)}><option value="">Any</option><option value="escalate">A person</option><option value="review">Review</option><option value="low">Nothing special</option></select></label>
        <label><span className="mr-1 text-muted">Status</span><select className={inputCls} value={status} onChange={(e) => setStatus(e.target.value)}><option value="">Any</option>{["TRIAGED", "DRAFTED", "REPLIED", "IGNORED"].map((x) => <option key={x} value={x}>{titleCase(x.toLowerCase())}</option>)}</select></label>
      </div>
      <Card title="Inbox" subtitle="Escalations first, then newest.">
        {data.comments.length === 0 ? <Empty title="No comments" hint="Comments appear after a channel is connected with the comments permission and a video has been uploaded. The fetch runs twice a day, or use “Fetch comments now” on the Performance page." /> : (
          <ul className="space-y-3">{data.comments.map((c) => <CommentCard key={c.id} c={c} canWrite={can("video:manage")} isOwner={user?.role === "owner"} onChanged={reload} />)}</ul>
        )}
      </Card>
    </>
  );
}
