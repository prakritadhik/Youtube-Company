"use client";

import { use } from "react";
import { useApi } from "@/lib/api";
import { Badge, Card, Empty, ErrorNote, List, Loading, PageHeader, StatusBadge, Table, Td, Th, Unavailable } from "@/components/ui";
import { fmtDate, titleCase } from "@/lib/format";
import type { BoardEntry, TaskSummary, VideoItem } from "@/lib/types";

interface ChannelDash {
  channel: { slug: string; name: string; kind: string; objective: string; policy: Record<string, boolean>; youtube_connected: boolean };
  performance: { available: boolean; reason?: string };
  audience_feedback: { available: boolean; reason?: string };
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
        <Card title="Performance"><Unavailable title="Views, watch time, retention, CTR, subscribers" reason={data.performance.reason} /></Card>
        <Card title="Audience feedback"><Unavailable title="Comments and sentiment" reason={data.audience_feedback.reason} /></Card>
      </div>

      <Card className="mt-6" title="Production" subtitle="Videos in the pipeline and what, if anything, still blocks publication.">
        {data.production.length === 0 ? <Empty title="No videos in production" hint="Videos appear here once the production workflow creates them." /> : (
          <Table>
            <thead><tr><Th>Video</Th><Th>Stage</Th><Th>Rights</Th><Th>Publication gate</Th></tr></thead>
            <tbody>
              {data.production.map((v) => (
                <tr key={v.id}>
                  <Td className="font-medium">{v.title}</Td>
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

      {data.recommendations.length === 0 && (
        <p className="mt-4 text-sm text-muted">No channel-specific recommendations: recommendations require measured performance data, which is not available yet.</p>
      )}
    </>
  );
}
