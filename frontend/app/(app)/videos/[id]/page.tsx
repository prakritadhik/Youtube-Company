"use client";

import Link from "next/link";
import { use } from "react";
import { useApi } from "@/lib/api";
import { Badge, Card, Empty, ErrorNote, KeyValue, List, Loading, PageHeader, Severity, StatusBadge, Table, Td, Th } from "@/components/ui";
import { fmtDate, titleCase } from "@/lib/format";
import type { Tone, VideoDetail } from "@/lib/types";

const checkTone: Record<string, Tone> = { pass: "ok", warn: "warn", fail: "danger", skipped: "neutral" };

export default function VideoPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: v, error, loading } = useApi<VideoDetail>(`/api/videos/${id}`, { refreshMs: 20000 });
  if (loading && !v) return <Loading />;
  if (error) return <ErrorNote error={error} />;
  if (!v) return null;
  const demo = v.title.toUpperCase().startsWith("DEMO");
  const blockers = v.gate_for_publishing.issues;

  return (
    <>
      <PageHeader
        title={v.title}
        description={<><Link href={`/channels/${v.channel}`} className="text-accent hover:underline">{v.channel_name}</Link> · video #{v.id}</>}
        actions={<>
          {demo && <Badge tone="warn">Demonstration — never publishable</Badge>}
          <Badge tone="info">{titleCase(v.stage.toLowerCase())}</Badge>
          <Badge tone={v.gate_for_publishing.allowed ? "ok" : "neutral"}>{v.gate_for_publishing.allowed ? "Cleared to publish" : `${blockers.length} thing${blockers.length === 1 ? "" : "s"} stop publication`}</Badge>
        </>}
      />

      <div className="grid gap-6 xl:grid-cols-3">
        <div className="space-y-6 xl:col-span-2">
          <Card title="Video">
            {v.has_video_file ? (
              <video controls preload="metadata" poster={v.has_thumbnail ? `/api/videos/${v.id}/media/thumbnail` : undefined}
                src={`/api/videos/${v.id}/media/final`} className="aspect-video w-full rounded-xl bg-black" aria-label={`Video: ${v.title}`} />
            ) : <Empty title="No video file yet" hint="The render step has not produced a file." />}
            {v.honesty && <p className="mt-3 text-sm text-muted">{v.honesty}</p>}
            {v.description && <p className="mt-3 text-sm">{v.description}</p>}
          </Card>

          {v.text && (
            <Card title={v.text.kind === "lyrics" ? "Lyrics" : "Script"} subtitle={`Version ${v.text.version} · written by ${v.text.author} (${v.text.author_kind})`}>
              <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed">{v.text.text}</pre>
            </Card>
          )}

          <Card title="Originality & rights report" subtitle={v.rights ? `Review v${v.rights.version}, ${fmtDate(v.rights.created_at)}` : undefined}
            action={v.rights && <StatusBadge status={v.rights.status} />}>
            {!v.rights ? <Empty title="Not reviewed yet" hint="The Copyright & Originality Reviewer has not produced a report for this video." /> : (
              <div className="space-y-4 text-sm">
                <p className="text-muted">{v.rights.status_basis}</p>
                <div>
                  <h3 className="mb-1.5 font-medium">Findings</h3>
                  {v.rights.findings.length === 0 ? <p className="text-muted">No concerns were identified by these checks.</p> :
                    <ul className="space-y-1.5">{v.rights.findings.map((f, i) => <li key={i} className="flex gap-2"><Badge tone={f.severity === "block" ? "danger" : f.severity === "info" ? "neutral" : "warn"}>{f.severity}</Badge><span>{f.message}</span></li>)}</ul>}
                </div>
                {v.rights.corrective_actions.length > 0 && <div><h3 className="mb-1.5 font-medium">Required corrective actions</h3><List items={v.rights.corrective_actions.map((a) => a.action)} /></div>}
                <div><h3 className="mb-1.5 font-medium">What is distinctive about this video</h3><List items={v.rights.distinctive_contributions} empty="Nothing stated." /></div>
                <details><summary className="cursor-pointer font-medium">Limits of this review</summary><div className="mt-2"><List items={v.rights.limitations} /></div></details>
                <details><summary className="cursor-pointer font-medium">Checks that were run</summary><div className="mt-2"><List items={v.rights.tools_used} /></div></details>
              </div>
            )}
          </Card>

          <Card title="Quality assurance" subtitle={v.qa ? `Report v${v.qa.version} · ${v.qa.checks.length} checks` : undefined} action={v.qa && <Badge tone={v.qa.passed ? "ok" : "danger"}>{v.qa.passed ? "All checks passed" : "Failed"}</Badge>}>
            {!v.qa ? <Empty title="QA has not run" /> : (
              <Table>
                <thead><tr><Th>Check</Th><Th>Result</Th><Th>Measured</Th></tr></thead>
                <tbody>{v.qa.checks.map((c) => (
                  <tr key={c.id}><Td className="font-medium">{c.name}</Td><Td><Badge tone={checkTone[c.status] ?? "neutral"}>{c.status}</Badge></Td>
                    <Td className="text-muted">{c.detail}{c.status === "fail" && c.correction && <div className="text-danger">→ {c.correction}</div>}</Td></tr>
                ))}</tbody>
              </Table>
            )}
          </Card>

          <Card title="Assets and provenance" subtitle="Every file, where it came from and what it may be used for.">
            <Table>
              <thead><tr><Th>Asset</Th><Th>Made by</Th><Th>Licence</Th><Th>Status</Th></tr></thead>
              <tbody>{v.assets.map((a) => (
                <tr key={a.id}>
                  <Td className="font-medium">{titleCase(a.kind)}<div className="text-xs font-normal text-muted">{a.role}</div></Td>
                  <Td className="break-words">{a.provider}{a.model && <span className="text-muted"> · {a.model}</span>}{a.seed != null && <div className="text-xs text-muted">seed {a.seed}</div>}</Td>
                  <Td className="break-words">{a.license_type}<div className="text-xs text-muted">{a.license_ref}</div></Td>
                  <Td className="whitespace-nowrap">
                    <Badge tone={a.commercial_use === "yes" ? "ok" : a.commercial_use === "no" ? "danger" : "warn"}>commercial: {a.commercial_use}</Badge>
                    {a.problems.length > 0 && <div className="mt-1 max-w-[16rem] whitespace-normal text-xs text-danger">{a.problems.map((x) => x.message).join(" ")}</div>}
                  </Td>
                </tr>
              ))}
              {v.assets.length === 0 && <tr><Td colSpan={4} className="text-muted">No assets registered.</Td></tr>}</tbody>
            </Table>
          </Card>
        </div>

        <div className="space-y-6">
          <Card title="Publication gate" subtitle="Everything that currently stops this video from going out.">
            {blockers.length === 0 ? <p className="text-sm text-ok">Nothing is stopping publication.</p> :
              <ul className="space-y-2 text-sm">{blockers.map((i) => <li key={i.code} className="flex gap-2"><Severity level={i.code === "DEMO_CONTENT" || i.code.startsWith("MODE") ? "info" : "medium"} /><span>{i.message}</span></li>)}</ul>}
          </Card>

          <Card title="Status">
            <KeyValue items={[
              ["Rights", <StatusBadge key="r" status={v.originality_status} />],
              ["CEO verified rights", v.ceo_rights_verified ? "Yes" : "No"],
              ["QA", v.qa_passed ? "Passed" : "Not passed"],
              ["Medical review", v.medical_review_status.replace(/_/g, " ").toLowerCase()],
              ["Owner approval", v.owner_approval_status.replace(/_/g, " ").toLowerCase()],
              ["Unlicensed assets", String(v.asset_license_issues)],
            ]} />
          </Card>

          {v.brief && (
            <Card title="Creative brief" subtitle={`v${v.brief.version} · ${v.brief.status.toLowerCase()}${v.brief.board_version_used != null ? ` · board v${v.brief.board_version_used}` : ""}`}>
              <KeyValue items={[
                ["Objective", String(v.brief.content.video_objective ?? "")], ["Audience", String(v.brief.content.target_audience ?? "")],
                ["Concept", String(v.brief.content.main_concept ?? "")], ["Tone", String(v.brief.content.tone_and_style ?? "")],
                ["Distinctive", (v.brief.content.distinctive_elements as string[] | undefined)?.join("; ") ?? ""],
              ]} />
            </Card>
          )}

          {v.sources.length > 0 && (
            <Card title="Sources"><ul className="space-y-2 text-sm">{v.sources.map((s) => <li key={s.id}><a className="text-accent hover:underline" href={s.url} target="_blank" rel="noopener noreferrer">{s.title || s.url}</a><div className="text-xs text-muted">{s.publisher} · retrieved {fmtDate(s.retrieved_at)} · supports: {s.supports}</div></li>)}</ul></Card>
          )}

          <Card title="Production tasks">
            <ul className="space-y-2 text-sm">{v.tasks.map((t) => <li key={t.id} className="flex items-start justify-between gap-2"><span>{t.title.split(" — ")[0]}<div className="text-xs text-muted">{t.owner}</div></span><StatusBadge status={t.status} /></li>)}
              {v.tasks.length === 0 && <li className="text-muted">No tasks.</li>}</ul>
          </Card>
        </div>
      </div>
    </>
  );
}
