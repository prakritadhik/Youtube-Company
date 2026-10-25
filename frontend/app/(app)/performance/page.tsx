"use client";

import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { useAuth } from "@/components/auth";
import { Badge, Banner, Button, Card, Empty, ErrorNote, inputCls, Loading, PageHeader, Stat, Table, Td, Th } from "@/components/ui";
import { fmtDate } from "@/lib/format";
import type { AnalyticsChannel, AnalyticsOverview, PerfMetric, VideoPerformance } from "@/lib/types";

const nf = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });

function Retention({ points }: { points: VideoPerformance["retention"] }) {
  if (points.length < 2) return null;
  const w = 320, h = 90;
  const path = points.map((p, i) => `${i === 0 ? "M" : "L"}${(p.ratio * w).toFixed(1)},${(h - Math.min(1.5, p.audience_watch_ratio) * (h / 1.5)).toFixed(1)}`).join(" ");
  return (
    <figure className="mt-3">
      <svg viewBox={`0 0 ${w} ${h}`} className="w-full max-w-md" role="img" aria-label={`Audience retention from ${nf.format(100 * points[0].audience_watch_ratio)}% at the start to ${nf.format(100 * points[points.length - 1].audience_watch_ratio)}% at the end`}>
        <line x1="0" y1={h} x2={w} y2={h} stroke="currentColor" strokeOpacity="0.2" />
        <path d={path} fill="none" stroke="currentColor" strokeWidth="2" />
      </svg>
      <figcaption className="text-xs text-muted">Share of viewers still watching, from the start (left) to the end (right), as YouTube reports it.</figcaption>
    </figure>
  );
}

function MetricRow({ m }: { m: PerfMetric }) {
  if (m.status === "unavailable") {
    return (<tr><Td>{m.label}</Td><Td colSpan={2}><Badge tone="warn">unavailable</Badge> <span className="text-sm text-muted">{m.reason}</span>{m.last_attempt && <div className="text-xs text-muted">last tried {fmtDate(m.last_attempt)}</div>}</Td></tr>);
  }
  return (
    <tr>
      <Td>{m.label}</Td>
      <Td className="whitespace-nowrap">{m.latest ? <><span className="font-medium">{nf.format(m.latest.value)}</span> <span className="text-xs text-muted">on {m.latest.day}</span></> : <span className="text-sm text-muted">see chart</span>}
        {m.total_in_window !== undefined && <div className="text-xs text-muted">{nf.format(m.total_in_window)} over the {m.series?.length} days shown</div>}</Td>
      <Td className="text-xs text-muted">{m.source}{m.retrieved_at && <div>retrieved {fmtDate(m.retrieved_at)}</div>}</Td>
    </tr>
  );
}

function VideoPanel({ videoId }: { videoId: number }) {
  const { data, error } = useApi<VideoPerformance>(`/api/videos/${videoId}/performance`);
  if (error && !data) return <ErrorNote error={error} />;
  if (!data) return <Loading />;
  return (
    <div>
      {data.caveat && <p className="mb-2 text-xs text-muted">{data.caveat}</p>}
      <div className="overflow-x-auto"><Table><thead><tr><Th>Figure</Th><Th>Latest</Th><Th>Source</Th></tr></thead>
        <tbody>{data.metrics.map((m) => <MetricRow key={m.metric} m={m} />)}</tbody></Table></div>
      <Retention points={data.retention} />
      {data.retention_analysis.status === "available" ? (
        <div className="mt-3 text-sm"><div className="font-medium">What the curve shows</div>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">{data.retention_analysis.statements.map((s) => <li key={s}>{s}</li>)}</ul>
          <p className="mt-1 text-xs text-muted">{data.retention_analysis.caveat}</p></div>
      ) : data.uploaded && (
        <p className="mt-3 text-sm"><Badge tone="warn">retention analysis unavailable</Badge> <span className="text-muted">{data.retention_analysis.reason}</span></p>
      )}
    </div>
  );
}

function ChannelCard({ c }: { c: AnalyticsChannel }) {
  const [sel, setSel] = useState<number | null>(c.videos[0]?.id ?? null);
  const conn = c.connection;
  const total = "value" in c.total_views ? c.total_views : null;
  const waiting = Object.values(c.comments_waiting).reduce((a, b) => a + b, 0);
  return (
    <Card title={c.name} subtitle={conn.status === "CONNECTED" ? `Connected to ${conn.google_channel_title}` : "Not connected to YouTube"}>
      {conn.status !== "CONNECTED" ? (
        <p className="text-sm text-muted">Connect this channel in Settings to see its performance. Until then every figure is unavailable; none is shown as zero.</p>
      ) : (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-4">
            <Stat label="Videos on YouTube" value={c.uploaded_videos} />
            <Stat label="Total views" value={total ? nf.format(total.value) : "unavailable"} hint={total ? `${total.videos_counted} of ${total.videos_uploaded} videos counted` : ("reason" in c.total_views ? c.total_views.reason : undefined)} />
            <Stat label="Comments waiting" value={waiting} hint={c.comments_waiting.escalate ? `${c.comments_waiting.escalate} escalated` : undefined} tone={c.comments_waiting.escalate ? "warn" : "neutral"} />
            <Stat label="Last collected" value={c.last_collected ? fmtDate(c.last_collected) : "never"} hint={c.open_gaps ? `${c.open_gaps} figure(s) unavailable` : undefined} />
          </div>
          {!conn.can_read_analytics && <Banner tone="warn">Daily views, watch time and retention need the analytics permission. Allow it in Settings, YouTube. Until then only total views, likes and comments are collected.</Banner>}
          {c.videos.length === 0 ? <Empty title="Nothing uploaded yet" hint="Performance appears here once a video has been uploaded and verified." /> : (
            <>
              <label className="block text-sm"><span className="mb-1 block font-medium">Video</span>
                <select className={inputCls} value={sel ?? ""} onChange={(e) => setSel(Number(e.target.value))}>{c.videos.map((v) => <option key={v.id} value={v.id}>{v.title}</option>)}</select></label>
              {sel !== null && <VideoPanel key={sel} videoId={sel} />}
            </>
          )}
        </div>
      )}
    </Card>
  );
}

export default function PerformancePage() {
  const { can } = useAuth();
  const { data, error, loading, reload } = useApi<AnalyticsOverview>("/api/analytics/overview", { refreshMs: 60000 });
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<{ message: string } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorNote error={error} />;
  if (!data) return null;
  const collect = async (what: "analytics" | "comments") => {
    setBusy(what); setErr(null); setNote(null);
    try { await api("/api/analytics/collect", { method: "POST", body: { what } }); setNote("Queued. The background worker will collect it within a few seconds; refresh to see the result."); await reload(); }
    catch (e) { setErr(e as Error); } finally { setBusy(null); }
  };
  return (
    <>
      <PageHeader title="Performance" description="What YouTube reports for each uploaded video, with its source and the time it was retrieved. A figure YouTube would not give is shown as unavailable with the reason, never as zero."
        actions={can("video:manage") && <div className="flex gap-2"><Button busy={busy === "analytics"} onClick={() => collect("analytics")}>Collect performance now</Button><Button busy={busy === "comments"} onClick={() => collect("comments")}>Fetch comments now</Button></div>} />
      <ErrorNote error={err} />
      {note && <Banner tone="info">{note}</Banner>}
      <div className="space-y-6">
        {data.channels.map((c) => <ChannelCard key={c.channel} c={c} />)}
        <Card title="Revenue"><p className="text-sm"><Badge tone="warn">unavailable</Badge> <span className="text-muted">{data.revenue.reason}</span></p></Card>
      </div>
    </>
  );
}
