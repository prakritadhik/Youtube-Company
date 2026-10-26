"use client";

import Link from "next/link";
import { use } from "react";
import { useApi } from "@/lib/api";
import { Badge, Card, Empty, ErrorNote, List, Loading, PageHeader, StatusBadge, Table, Td, Th, Unavailable } from "@/components/ui";
import { fmtDate, titleCase } from "@/lib/format";
import type { BoardEntry, TaskSummary, VideoItem } from "@/lib/types";

interface ChannelDash {
  channel: { slug: string; name: string; kind: string; objective: string; policy: Record<string, boolean>; youtube_connected: boolean };
  performance: { available: boolean; reason?: string; uploaded_videos?: number; videos_with_figures?: number; total_views?: number; open_gaps?: number; last_collected?: string;
    videos?: { id: number; title: string; views_total: number | null; as_of: string | null }[]; note?: string };
  audience_feedback: { available: boolean; reason?: string; comments?: number; escalated_waiting?: number; drafts_waiting?: number; method?: string;
    sentiment?: { positive: number; negative: number; questions: number; needs_a_person: number; unclassified: number } };
  production: VideoItem[]; publishing_history: VideoItem[]; tasks: TaskSummary[];
  board: { release_version: number; active_instructions: BoardEntry[]; experiments: BoardEntry[]; lessons: BoardEntry[] };
  recommendations: string[];
}

export default function ChannelPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const { data, error, loading } = useApi<ChannelDash>(`/api/channels/${slug}`, { refreshMs: 30000 });
  if (loading && !data) return <Loading />;
  if (error) return <ErrorNote error={error} />;
  if (!data) return null;
  const c = data.channel;

  return (
    <>
      <PageHeader title={c.name} description={c.objective} actions={
        <>
          <Badge tone={c.youtube_connected ? "ok" : "neutral"}>{c.youtube_connected ? "YouTube connected" : "YouTube not connected"}</Badge>
          {c.policy.human_medical_review_required && <Badge tone="warn">Human medical review required</Badge>}
          {c.policy.autopublish_allowed === false && <Badge tone="neutral">Never auto-publishes</Badge>}
        </>
      } />

      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Performance" subtitle={data.performance.available ? `Last collected ${fmtDate(data.performance.last_collected)}` : undefined}
          action={<Link className="text-sm text-accent hover:underline" href="/performance">Details</Link>}>
          {data.performance.available ? (
            <div className="space-y-3 text-sm">
              <div><span className="text-2xl font-semibold tabular-nums">{new Intl.NumberFormat("en-US").format(data.performance.total_views ?? 0)}</span> <span className="text-muted">total views across {data.performance.videos_with_figures} of {data.performance.uploaded_videos} uploaded video(s)</span></div>
              <ul className="space-y-1">{(data.performance.videos ?? []).map((v) => (
                <li key={v.id} className="flex justify-between gap-3"><span className="min-w-0 truncate">{v.title}</span>
                  {v.views_total !== null ? <span className="tabular-nums">{new Intl.NumberFormat("en-US").format(v.views_total)} <span className="text-xs text-muted">as of {v.as_of}</span></span> : <Badge tone="warn">unavailable</Badge>}</li>))}</ul>
              {data.performance.open_gaps ? <p className="text-xs text-muted">{data.performance.open_gaps} figure(s) are unavailable, each with YouTube&rsquo;s reason on the Performance page.</p> : null}
              {data.performance.note && <p className="text-xs text-muted">{data.performance.note}</p>}
            </div>
          ) : <Unavailable title="Views, watch time, retention, CTR, subscribers" reason={data.performance.reason} />}
        </Card>
        <Card title="Audience feedback" action={<Link className="text-sm text-accent hover:underline" href="/comments">Comments</Link>}>
          {data.audience_feedback.available && data.audience_feedback.sentiment ? (
            <div className="space-y-2 text-sm">
              <p><span className="text-2xl font-semibold tabular-nums">{data.audience_feedback.comments}</span> <span className="text-muted">comment(s), spam excluded</span></p>
              <p>{data.audience_feedback.sentiment.positive} positive · {data.audience_feedback.sentiment.negative} negative · {data.audience_feedback.sentiment.questions} question(s) · {data.audience_feedback.sentiment.needs_a_person} needing a person · {data.audience_feedback.sentiment.unclassified} unclassified</p>
              {(data.audience_feedback.escalated_waiting ?? 0) > 0 && <p><Badge tone="danger">{data.audience_feedback.escalated_waiting} need a person</Badge></p>}
              <p className="text-xs text-muted">{data.audience_feedback.method}</p>
            </div>
          ) : <Unavailable title="Comments and sentiment" reason={data.audience_feedback.reason} />}
        </Card>
      </div>

      <Card className="mt-6" title="Production" subtitle="Videos in the pipeline and what, if anything, still blocks publication.">
        {data.production.length === 0 ? <Empty title="No videos in production" hint="Videos appear here once the production workflow creates them." /> : (
          <Table>
            <thead><tr><Th>Video</Th><Th>Stage</Th><Th>Rights</Th><Th>Publication gate</Th></tr></thead>
            <tbody>
              {data.production.map((v) => (
                <tr key={v.id}>
                  <Td className="font-medium"><Link href={`/videos/${v.id}`} className="text-accent hover:underline">{v.title}</Link></Td>
                  <Td><Badge>{titleCase(v.stage.toLowerCase())}</Badge></Td>
                  <Td><StatusBadge status={v.originality_status} /></Td>
                  <Td>{v.gate.allowed ? <Badge tone="ok">Clear</Badge> :
                    <ul className="space-y-0.5 text-xs text-muted">{v.gate.issues.slice(0, 4).map((i) => <li key={i.code}>• {i.message}</li>)}{v.gate.issues.length > 4 && <li>+{v.gate.issues.length - 4} more</li>}</ul>}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <Card title="Publishing history">
          {data.publishing_history.length === 0 ? <p className="text-sm text-muted">Nothing has been published from this system.</p> : (
            <List items={data.publishing_history.map((v) => <span key={v.id}>{v.title} <code className="text-xs text-muted">{v.youtube_video_id}</code></span>)} />
          )}
        </Card>
        <Card title={`Intelligence for this channel (board v${data.board.release_version})`} subtitle="Only this channel's approved instructions and company-wide procedures.">
          <h3 className="mb-1 text-sm font-medium">Active instructions</h3>
          {data.board.active_instructions.length === 0 ? <p className="mb-3 text-sm text-muted">None yet.</p> :
            <div className="mb-3"><List items={data.board.active_instructions.map((e) => <span key={e.public_id}><b>{e.title}</b> <Badge tone="neutral">{e.confidence} confidence</Badge></span>)} /></div>}
          <h3 className="mb-1 text-sm font-medium">Experiments running</h3>
          {data.board.experiments.length === 0 ? <p className="text-sm text-muted">None.</p> :
            <List items={data.board.experiments.map((e) => <span key={e.public_id}>{e.title}</span>)} />}
        </Card>
      </div>

      <Card className="mt-6" title="Recent tasks for this channel">
        {data.tasks.length === 0 ? <p className="text-sm text-muted">No tasks yet.</p> : (
          <Table>
            <thead><tr><Th>Task</Th><Th>Owner</Th><Th>Status</Th><Th>Updated</Th></tr></thead>
            <tbody>{data.tasks.map((t) => (
              <tr key={t.id}><Td className="font-medium">{t.title}{t.blocker && <div className="text-xs font-normal text-danger">{t.blocker}</div>}</Td><Td>{t.owner}</Td><Td><StatusBadge status={t.status} /></Td><Td className="text-muted">{fmtDate(t.updated_at)}</Td></tr>
            ))}</tbody>
          </Table>
        )}
      </Card>

      <Card className="mt-6" title="Recommendations" subtitle="Derived from this channel's own records. Each says what it rests on; none claims a cause.">
        {data.recommendations.length === 0
          ? <p className="text-sm text-muted">None right now. Recommendations appear when there are measured figures or comments that need attention.</p>
          : <List items={data.recommendations} />}
      </Card>
    </>
  );
}
