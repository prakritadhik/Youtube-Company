"use client";

import Link from "next/link";
import { useApi } from "@/lib/api";
import { Badge, Card, Empty, ErrorNote, Loading, PageHeader, StatusBadge, Table, Td, Th } from "@/components/ui";
import { titleCase } from "@/lib/format";
import type { VideoItem } from "@/lib/types";

interface Rights {
  cleared: VideoItem[]; awaiting_review: VideoItem[]; blocked_or_open: VideoItem[]; missing_licenses: VideoItem[];
  medical_review_pending: VideoItem[]; note: string;
}

function VideoTable({ rows, empty }: { rows: VideoItem[]; empty: string }) {
  if (!rows.length) return <p className="text-sm text-muted">{empty}</p>;
  return (
    <Table>
      <thead><tr><Th>Video</Th><Th>Channel</Th><Th>Rights status</Th><Th>CEO verified</Th><Th>Unlicensed assets</Th><Th>Why blocked</Th></tr></thead>
      <tbody>
        {rows.map((v) => (
          <tr key={v.id}>
            <Td className="font-medium"><Link href={`/videos/${v.id}`} className="text-accent hover:underline">{v.title}</Link></Td>
            <Td>{v.channel ? titleCase(v.channel) : "—"}</Td>
            <Td><StatusBadge status={v.originality_status} /></Td>
            <Td>{v.ceo_rights_verified ? <Badge tone="ok">Yes</Badge> : <Badge>No</Badge>}</Td>
            <Td>{v.asset_license_issues}</Td>
            <Td className="text-xs text-muted">{v.gate.issues.filter((i) => i.code.startsWith("ORIGINALITY") || i.code.includes("LICENSE") || i.code.includes("RIGHTS") || i.code.includes("MEDICAL")).map((i) => i.message).join(" ") || "—"}</Td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

export default function RightsPage() {
  const { data, error, loading } = useApi<Rights>("/api/rights", { refreshMs: 30000 });
  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorNote error={error} />;
  if (!data) return null;
  const nothing = Object.entries(data).every(([k, v]) => k === "note" || (Array.isArray(v) && v.length === 0));

  return (
    <>
      <PageHeader title="Originality & Rights" description={data.note} />
      {nothing && <Empty title="No videos yet" hint="Each video receives a PASS / NEEDS REVISION / BLOCKED / HUMAN REVIEW REQUIRED status from the Copyright & Originality Reviewer, and the CEO inspects the evidence independently before it can be published." />}
      <div className="space-y-6">
        <Card title="Blocked or unresolved" subtitle="These cannot enter the publishing queue."><VideoTable rows={data.blocked_or_open} empty="No unresolved rights concerns." /></Card>
        <Card title="Missing licences"><VideoTable rows={data.missing_licenses} empty="No assets are missing licences." /></Card>
        <Card title="Medical review pending or rejected"><VideoTable rows={data.medical_review_pending} empty="No medicine videos are waiting for human review." /></Card>
        <Card title="Awaiting originality review"><VideoTable rows={data.awaiting_review} empty="Nothing is waiting for review." /></Card>
        <Card title="Cleared for publication" subtitle="PASS from the reviewer and independently verified by the CEO."><VideoTable rows={data.cleared} empty="No videos are cleared yet." /></Card>
      </div>
    </>
  );
}
