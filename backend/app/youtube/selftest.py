"""``python -m app.cli youtube-selftest``: the owner's first real contact with YouTube, small and reversible.

It uploads a few seconds of generated video to the owner's channel as **private**, reads it back, sets a thumbnail, and then
deletes the video again (unless ``--keep``). Along the way it answers, with real data, the questions the build environment
could not settle (docs/PROVIDERS.md): does the project's upload stay private, may this channel set custom thumbnails, and
what did each call cost in quota. It never touches a catalogue video, the content plan or the gate; it needs the same
connection, attestation, operating mode and quota as any upload, and an explicit flag from the owner.
"""

from __future__ import annotations

import subprocess
from typing import Any

from app.db import session_scope
from app.models.youtube import YouTubeUpload
from app.production import imaging
from app.providers import base as providers
from app.security.permissions import Actor
from app.services import audit, control
from app.storage import data_root, relative_to_data
from app.timeutil import iso, utcnow
from app.youtube import client as yt
from app.youtube import connection as conn_svc
from app.youtube import quota, upload


class SelfTestError(Exception):
    pass


def _make_files(stamp: str):
    d = data_root() / "artifacts" / "youtube" / "selftest"
    d.mkdir(parents=True, exist_ok=True)
    video, thumb = d / f"selftest-{stamp}.mp4", d / f"selftest-{stamp}.png"
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=4", "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)], capture_output=True, text=True, timeout=120, check=False)
    if r.returncode != 0 or not video.is_file():
        raise SelfTestError("FFmpeg could not make the test video: " + r.stderr.strip()[-300:])
    imaging.make_thumbnail(thumb, title="TEST", seed=7, size=(1280, 720))
    return video, thumb


def run(channel_slug: str, *, keep: bool = False, progress=print) -> dict[str, Any]:
    owner = Actor.system("youtube-selftest")
    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    with session_scope(write=False) as db:
        from sqlalchemy import select

        from app.models.org import Channel
        ch = db.scalar(select(Channel).where(Channel.slug == channel_slug))
        if ch is None:
            raise SelfTestError(f"unknown channel '{channel_slug}'")
        conn = conn_svc.connection_for(db, ch.id)
        if conn is None or conn.status != "CONNECTED":
            raise SelfTestError("This channel is not connected and confirmed yet (Settings, YouTube).")
        control.assert_external_action_allowed(db, "upload")  # raises with the reason: DEVELOPMENT mode, PAUSED, emergency pause
        state, detail = providers.capability_state(db, "youtube", "youtube.upload")
        if state != "ready":
            raise SelfTestError(f"YouTube is not ready: {detail}.")
        quota.require(db, "videos.insert", "thumbnails.set", "videos.list", *(() if keep else ("videos.delete", "videos.list")))
        conn_id, channel_name, category, kids, google_channel = conn.id, ch.name, conn.category_id, conn.made_for_kids, conn.google_channel_id
        usable = conn.made_for_kids is not None
    if not usable:
        raise SelfTestError("Declare the channel's audience (made for kids or not) in Settings first.")
    progress(f"Making a 4-second test video for {channel_name} ...")
    video, thumb = _make_files(stamp)
    req = {"title": f"ZZ connection test {stamp} (private, safe to delete)", "description": "A few seconds of generated test pattern, uploaded by the AI Media Company "
           "to check its YouTube connection. Private. Safe to delete.", "tags": [], "category_id": category, "made_for_kids": kids, "privacy": "private", "publish_at": None}
    import hashlib
    with session_scope() as db:
        up = YouTubeUpload(video_id=None, connection_id=conn_id, status="PREPARED", privacy_requested="private", publish_at=None, is_test=True, request=req,
                           file_ref=relative_to_data(video), thumbnail_ref=relative_to_data(thumb), file_sha256=hashlib.sha256(video.read_bytes()).hexdigest(),
                           bytes_total=video.stat().st_size, created_by="cli:youtube-selftest")
        db.add(up)
        db.flush()
        uid = up.id
        audit.record(db, owner, "youtube.selftest_started", "youtube_upload", uid, {"channel": channel_slug, "keep": keep})
    progress("Uploading as PRIVATE and reading it back ...")
    try:
        out = upload.perform(uid, mark_started=lambda d: None, clear_started=lambda: None)
    except (upload.Blocked, upload.Failed) as e:
        raise SelfTestError(str(e)) from e
    report: dict[str, Any] = {"channel": channel_slug, "google_channel": google_channel, "upload_id": uid, "result": out["status"], "youtube_video_id": out["youtube_video_id"],
                              "thumbnail": out["thumbnail"], "read_back": out["verification"].get("checks", []), "deleted": None, "at": iso(utcnow())}
    with session_scope(write=False) as db:
        up = db.get(YouTubeUpload, uid)
        report["thumbnail_detail"] = up.thumbnail_detail
        report["quota_units_used_today"] = quota.used_today(db)
    if not keep and out["youtube_video_id"]:
        progress("Deleting the test video again ...")
        try:
            api = yt.YouTubeClient(conn_svc.access_token(conn_id), transport=conn_svc.TRANSPORT)
            api.videos_delete(out["youtube_video_id"])
            with session_scope() as db:
                quota.record(db, "videos.delete", note="selftest cleanup")
            gone = not (api.videos_list(out["youtube_video_id"]).get("items") or [])
            with session_scope() as db:
                quota.record(db, "videos.list", note="selftest: confirm deletion")
            report["deleted"] = bool(gone)
        except (yt.YouTubeApiError, conn_svc.YouTubeError) as e:
            report["deleted"] = False
            report["delete_error"] = str(e)
    with session_scope() as db:
        up = db.get(YouTubeUpload, uid)
        up.status = "ABANDONED" if report["deleted"] else up.status  # a deleted test video is closed; a kept one stays on record
        audit.record(db, owner, "youtube.selftest_finished", "youtube_upload", uid, {k: v for k, v in report.items() if k != "read_back"})
    return report
