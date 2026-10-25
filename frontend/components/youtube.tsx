"use client";

import { useEffect, useState } from "react";
import { api, useApi } from "@/lib/api";
import { Badge, Banner, Button, Card, ErrorNote, inputCls, KeyValue, Loading, Table, Td, Th, Unavailable } from "@/components/ui";
import { fmtDate } from "@/lib/format";
import type { Tone, YtConnection, YtStatus, YtUpload } from "@/lib/types";

const connTone: Record<string, Tone> = { CONNECTED: "ok", PENDING_CONFIRMATION: "warn", NEEDS_RECONNECT: "danger", REVOKED: "neutral", NOT_CONNECTED: "neutral" };
const label = (s: string) => s.replace(/_/g, " ").toLowerCase().replace(/^./, (c) => c.toUpperCase());

function ChannelRow({ slug, name, c, status, onChanged }: { slug: string; name: string; c: YtConnection; status: YtStatus; onChanged: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<{ message: string } | null>(null);
  const [kids, setKids] = useState<"" | "yes" | "no">(c.made_for_kids === true ? "yes" : c.made_for_kids === false ? "no" : "");
  const [cat, setCat] = useState(c.category_id ?? "");
  const [checked, setChecked] = useState(false);
  const act = async (key: string, fn: () => Promise<void>) => {
    setBusy(key); setErr(null);
    try { await fn(); onChanged(); } catch (e) { setErr(e as Error); } finally { setBusy(null); }
  };
  const connect = (key = "connect", groups: string[] = []) => act(key, async () => { const r = await api<{ url: string }>(`/api/youtube/${slug}/connect`, { method: "POST", body: { groups } }); window.location.href = r.url; });
  const confirm = () => act("confirm", async () => {
    await api(`/api/youtube/${slug}/confirm`, { method: "POST", body: { google_channel_id: c.google_channel_id, made_for_kids: kids === "yes", category_id: cat || null } });
  });
  const disconnect = () => act("disconnect", async () => { await api(`/api/youtube/${slug}/disconnect`, { method: "POST", body: {} }); });

  return (
    <li className="rounded-xl border border-line p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="font-medium">{name}</div>
        <Badge tone={connTone[c.status] ?? "neutral"}>{label(c.status)}</Badge>
      </div>
      <ErrorNote error={err} />
      {c.status === "NOT_CONNECTED" || c.status === "REVOKED" || c.status === "NEEDS_RECONNECT" ? (
        <div className="mt-3 space-y-2 text-sm">
          {c.last_error && <p className="text-danger">{c.last_error}</p>}
          <Button variant="primary" busy={busy === "connect"} disabled={!status.oauth_client_configured} onClick={() => connect()}>{c.status === "NOT_CONNECTED" ? "Connect to YouTube" : "Reconnect"}</Button>
          {!status.oauth_client_configured && <p className="text-muted">Create a Google OAuth client first (see docs/YOUTUBE.md) and put its ID and secret in <code>.env</code>.</p>}
        </div>
      ) : (
        <div className="mt-3 space-y-3 text-sm">
          <KeyValue items={[
            ["YouTube channel", <span key="t"><span className="font-medium">{c.google_channel_title}</span> {c.google_channel_handle && <span className="text-muted">{c.google_channel_handle}</span>}<div className="text-xs text-muted">id {c.google_channel_id}</div></span>],
            ["Permissions", (c.scopes ?? []).join(", ")],
            ...(c.status === "CONNECTED" ? [["Audience", c.made_for_kids ? "Made for kids" : "Not made for kids"] as [string, string], ["Category", c.category_name ?? c.category_id ?? ""] as [string, string],
              ["Confirmed", `${c.confirmed_by} · ${fmtDate(c.confirmed_at)}`] as [string, string]] : []),
          ]} />
          {c.status === "PENDING_CONFIRMATION" && (
            <div className="space-y-3 rounded-xl bg-warn-soft p-3">
              <p className="font-medium">Is this the right channel for “{name}”?</p>
              <p>Google says the account you signed in with manages <strong>{c.google_channel_title}</strong>. Nothing can be uploaded until you confirm this and answer the audience question.</p>
              <fieldset><legend className="mb-1 font-medium">Are this channel&rsquo;s videos made for kids?</legend>
                <p className="mb-1 text-xs text-muted">YouTube requires an answer and the legal responsibility is yours; the app will not guess. Children&rsquo;s songs are usually made for kids.</p>
                <div className="flex gap-4"><label className="flex items-center gap-1.5"><input type="radio" name={`kids-${slug}`} checked={kids === "yes"} onChange={() => setKids("yes")} />Yes, made for kids</label>
                  <label className="flex items-center gap-1.5"><input type="radio" name={`kids-${slug}`} checked={kids === "no"} onChange={() => setKids("no")} />No</label></div></fieldset>
              <label className="block"><span className="mb-1 block font-medium">Category</span>
                <select className={inputCls} value={cat} onChange={(e) => setCat(e.target.value)}>{Object.entries(status.categories).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
              <label className="flex items-center gap-2"><input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} />Yes, this is the channel I mean.</label>
              <div className="flex gap-2"><Button variant="primary" busy={busy === "confirm"} disabled={!checked || kids === ""} onClick={confirm}>Confirm this channel</Button>
                <Button busy={busy === "disconnect"} onClick={disconnect}>It&rsquo;s the wrong one</Button></div>
            </div>
          )}
          {c.status === "CONNECTED" && (
            <div className="space-y-2">
              <div className="rounded-xl bg-surface2 p-3">
                <div className="font-medium">Extra permissions</div>
                <p className="text-xs text-muted">Each is asked from Google only when you switch it on. You can remove them any time at myaccount.google.com/permissions.</p>
                <ul className="mt-2 space-y-2">
                  <li className="flex flex-wrap items-center justify-between gap-2"><span>Performance data (views, watch time, retention) <Badge tone={c.can_read_analytics ? "ok" : "neutral"}>{c.can_read_analytics ? "allowed" : "not allowed"}</Badge></span>
                    {!c.can_read_analytics && <Button busy={busy === "analytics"} onClick={() => connect("analytics", ["analytics"])}>Allow</Button>}</li>
                  <li className="flex flex-wrap items-center justify-between gap-2"><span>Comments (read, and post replies you approve) <Badge tone={c.can_manage_comments ? "ok" : "neutral"}>{c.can_manage_comments ? "allowed" : "not allowed"}</Badge></span>
                    {!c.can_manage_comments && <Button busy={busy === "comments"} onClick={() => connect("comments", ["comments"])}>Allow</Button>}</li>
                </ul>
              </div>
              <Button busy={busy === "disconnect"} onClick={disconnect}>Disconnect</Button>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

function QuotaLimit({ limit, onSaved }: { limit: number; onSaved: () => void }) {
  const [v, setV] = useState(String(limit));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string } | null>(null);
  const save = async () => {
    setBusy(true); setErr(null);
    try { await api("/api/settings/youtube", { method: "PUT", body: { value: { daily_quota_units: Number(v) } } }); onSaved(); } catch (e) { setErr(e as Error); } finally { setBusy(false); }
  };
  return (
    <div className="mt-2 flex flex-wrap items-end gap-2 text-sm"><ErrorNote error={err} />
      <label className="block"><span className="mb-1 block text-xs font-medium">Daily quota to allow this app</span><input className={inputCls} type="number" min="0" value={v} onChange={(e) => setV(e.target.value)} /></label>
      <Button busy={busy} onClick={save} disabled={Number(v) === limit}>Save</Button></div>
  );
}

export function YouTubeSettingsCard() {
  const { data, error, reload } = useApi<YtStatus>("/api/youtube/status", { refreshMs: 30000 });
  const [banner, setBanner] = useState<{ tone: Tone; text: string } | null>(null);
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const r = q.get("youtube");
    if (r === "signed_in") setBanner({ tone: "ok", text: "Signed in. If the channel is waiting for confirmation, check that it is the right one, answer the audience question, and confirm." });
    else if (r === "error") setBanner({ tone: "danger", text: q.get("message") ?? "The sign-in did not complete." });
    if (r) window.history.replaceState({}, "", window.location.pathname);
  }, []);
  if (error && !data) return <Card title="YouTube"><ErrorNote error={error} /></Card>;
  if (!data) return <Card title="YouTube"><Loading /></Card>;
  const q = data.quota;
  return (
    <Card title="YouTube" subtitle="Connect each channel, confirm it is the right one, then uploads can be queued from a video's page.">
      <div className="space-y-4">
        {banner && <Banner tone={banner.tone}>{banner.text}</Banner>}
        <Banner tone="warn">{data.note}</Banner>
        {!data.oauth_client_configured && <Unavailable title="No Google OAuth client is configured" reason="Follow docs/YOUTUBE.md (Google Cloud project, YouTube Data API, a Desktop-app OAuth client), then set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET in .env and restart." />}
        <ul className="space-y-3">{data.channels.map((ch) => <ChannelRow key={ch.channel} slug={ch.channel} name={ch.channel_name} c={ch.connection} status={data} onChanged={reload} />)}</ul>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="rounded-xl bg-surface2 p-3 text-sm"><div className="font-medium">Upload readiness</div>
            <div className="mt-1"><Badge tone={data.upload_capability.state === "ready" ? "ok" : "warn"}>{label(data.upload_capability.state)}</Badge></div>
            <p className="mt-1 text-muted">{data.upload_capability.detail}. Operating mode: {label(data.operating_mode)}.</p></div>
          <div className="rounded-xl bg-surface2 p-3 text-sm"><div className="font-medium">Quota used today (this app&rsquo;s own count)</div>
            <div className="mt-1 text-lg font-semibold">{q.used_today.toLocaleString()} <span className="text-sm font-normal text-muted">of {q.daily_limit.toLocaleString()}</span></div>
            <div className="mt-1 h-2 overflow-hidden rounded-full bg-line" aria-hidden><div className="h-full bg-accent" style={{ width: `${Math.min(100, (100 * q.used_today) / Math.max(1, q.daily_limit))}%` }} /></div>
            <p className="mt-1 text-xs text-muted">Resets {fmtDate(q.resets_at)} (midnight Pacific). {q.basis}</p>
            <QuotaLimit limit={q.daily_limit} onSaved={reload} /></div>
        </div>
      </div>
    </Card>
  );
}

const uploadTone: Record<string, Tone> = { VERIFIED: "ok", PRIVACY_LOCKED: "warn", MISMATCH: "danger", FAILED: "danger", ABANDONED: "neutral", PREPARED: "info", SESSION_OPEN: "info", UPLOADED: "info" };

export function YouTubeVideoCard({ videoId, canWrite, demo }: { videoId: number; canWrite: boolean; demo: boolean }) {
  const { data, error, reload } = useApi<{ uploads: YtUpload[] }>(`/api/videos/${videoId}/uploads`, { refreshMs: 10000 });
  const [privacy, setPrivacy] = useState("private");
  const [when, setWhen] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string } | null>(null);
  const [abandonNote, setAbandonNote] = useState("");
  if (error && !data) return null;
  if (!data) return <Card title="YouTube"><Loading /></Card>;

  const queue = async () => {
    setBusy(true); setErr(null);
    try { await api(`/api/videos/${videoId}/upload`, { method: "POST", body: { privacy, publish_at: when ? new Date(when).toISOString() : null } }); await reload(); }
    catch (e) { setErr(e as Error); } finally { setBusy(false); }
  };
  const abandon = async (id: number) => {
    setBusy(true); setErr(null);
    try { await api(`/api/youtube/uploads/${id}/abandon`, { method: "POST", body: { note: abandonNote } }); setAbandonNote(""); await reload(); }
    catch (e) { setErr(e as Error); } finally { setBusy(false); }
  };
  const active = data.uploads.some((u) => !["ABANDONED", "FAILED"].includes(u.status));

  return (
    <Card title="YouTube" subtitle="Nothing is published without the gate, your approval and a read-back from YouTube. A request that merely went through is never reported as success.">
      <ErrorNote error={err} />
      {demo && <p className="mb-3 text-sm text-muted">Demonstration videos can never be uploaded.</p>}
      {data.uploads.length === 0 ? <p className="text-sm text-muted">Not uploaded.</p> : (
        <ul className="space-y-4">{data.uploads.map((u) => (
          <li key={u.upload_id} className="rounded-xl border border-line p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2"><Badge tone={uploadTone[u.status] ?? "neutral"}>{label(u.status)}</Badge><Badge>{u.privacy_requested}</Badge>
              {u.publish_at && <Badge tone="info">scheduled {fmtDate(u.publish_at)}</Badge>}<span className="text-muted">upload #{u.upload_id} · {fmtDate(u.created_at)}</span></div>
            {u.status === "PRIVACY_LOCKED" && <p className="mt-2 rounded-lg bg-warn-soft p-2 text-warn">YouTube made this video private although {u.privacy_requested} was requested. It is <strong>not published</strong>. YouTube is reported to do this for API projects that have not passed its compliance audit; this has not been confirmed yet (docs/YOUTUBE.md).</p>}
            {u.status === "MISMATCH" && <p className="mt-2 rounded-lg bg-danger-soft p-2 text-danger">What YouTube holds does not match what was asked for. Look at it in YouTube Studio before doing anything else.</p>}
            {u.error && u.status !== "VERIFIED" && <p className="mt-2 text-danger">{u.error}</p>}
            {u.youtube_video_id && <p className="mt-2">Video id <code>{u.youtube_video_id}</code> {u.watch_url && <a className="text-accent hover:underline" href={u.watch_url} target="_blank" rel="noopener noreferrer">open on YouTube</a>}</p>}
            {u.thumbnail !== "NONE" && <p className="mt-1">Thumbnail: <Badge tone={u.thumbnail === "SET" ? "ok" : "warn"}>{label(u.thumbnail)}</Badge> {u.thumbnail_detail && <span className="text-muted">{u.thumbnail_detail}</span>}</p>}
            {u.verification.checks && (
              <details className="mt-2"><summary className="cursor-pointer font-medium">What YouTube reported back ({u.verification.checks.filter((c) => c.ok).length} of {u.verification.checks.length} checks passed)</summary>
                <Table><thead><tr><Th>Check</Th><Th>Asked for</Th><Th>YouTube says</Th></tr></thead><tbody>{u.verification.checks.map((c) => (
                  <tr key={c.check}><Td className="font-medium">{c.check} {c.ok ? <Badge tone="ok">ok</Badge> : <Badge tone="danger">differs</Badge>}</Td><Td className="break-words text-muted">{String(c.expected)}</Td><Td className="break-words text-muted">{String(c.actual)}</Td></tr>))}</tbody></Table></details>
            )}
            {canWrite && !["ABANDONED", "VERIFIED"].includes(u.status) && (
              <div className="mt-3 flex flex-wrap items-end gap-2"><label className="block"><span className="mb-1 block text-xs font-medium">Abandon this upload (owner; say why)</span>
                <input className={inputCls} value={abandonNote} onChange={(e) => setAbandonNote(e.target.value)} /></label>
                <Button disabled={!abandonNote.trim()} busy={busy} onClick={() => abandon(u.upload_id)}>Abandon</Button></div>
            )}
          </li>))}</ul>
      )}
      {canWrite && !demo && !active && (
        <div className="mt-4 space-y-3 rounded-xl bg-surface2 p-3 text-sm">
          <div className="font-medium">Queue an upload</div>
          <div className="flex flex-wrap gap-4">
            <label className="block"><span className="mb-1 block font-medium">Visibility</span>
              <select className={inputCls} value={privacy} onChange={(e) => { setPrivacy(e.target.value); if (e.target.value !== "private") setWhen(""); }}>
                <option value="private">Private (recommended for a first test)</option><option value="unlisted">Unlisted</option><option value="public">Public</option></select></label>
            <label className="block"><span className="mb-1 block font-medium">Go public at (optional)</span>
              <input type="datetime-local" className={inputCls} value={when} disabled={privacy !== "private"} onChange={(e) => setWhen(e.target.value)} /></label>
          </div>
          <p className="text-xs text-muted">A scheduled video is uploaded private and YouTube makes it public at that time (your computer&rsquo;s time zone). Every condition of the publication gate is checked now and again when the upload runs.</p>
          <Button variant="primary" busy={busy} onClick={queue}>Queue upload</Button>
        </div>
      )}
      {active && canWrite && <p className="mt-3 text-xs text-muted">One upload per video. Abandon the current one to queue another.</p>}
    </Card>
  );
}

