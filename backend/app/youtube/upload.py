"""Putting a video on YouTube, and proving that it arrived.

The rules this module enforces (each has a test):

* **Everything is re-checked at the moment of upload**, not trusted from when it was queued: the publication gate (rights,
  QA, approved and unmodified metadata, medical review, owner approval, operating mode, pause), the connection, the audience
  setting, the files (their hash must still match), and the quota.
* **No second copy.** The upload row is written before anything irreversible happens and updated at every step. A crash or
  a retry resumes the *same* upload session (or finds the finished video); it never opens a new one. After the byte transfer
  has begun, an interrupted task needs the owner to reconcile it, because only a person can look in YouTube Studio.
* **Success means a read-back.** The upload is "verified" only when ``videos.list`` returns a video on the *expected channel*
  with the *requested title, privacy, schedule and audience*. A video that came back private although public was requested is
  reported as ``PRIVACY_LOCKED`` (what YouTube is reported to do for API projects that have not passed its compliance audit; not confirmed here), not as
  published. A request that merely returned without error proves nothing.
* **The raw API responses are saved** as files and attached to the task as evidence the manager re-hashes.
* **Nothing is applied to the video record until the read-back verified it.**
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import session_scope
from app.models.org import Channel
from app.models.tasks import Task
from app.models.videos import Video
from app.models.youtube import YouTubeConnection, YouTubeUpload
from app.providers import base as providers
from app.research import seo
from app.security.permissions import Actor, P
from app.services import audit, control, gate, tasks, videos
from app.services import settings as settings_svc
from app.storage import resolve_data_path, write_json_artifact
from app.timeutil import iso, utcnow
from app.youtube import client as yt
from app.youtube import connection as conn_svc
from app.youtube import quota
from app.youtube.crypto import KeyMissing, TokenBox

ACTIVE = ("PREPARED", "SESSION_OPEN", "UPLOADED", "VERIFIED", "PRIVACY_LOCKED", "MISMATCH")  # an upload in any of these blocks another one
SLEEP: Callable[[float], None] = time.sleep  # replaced in tests so backoff does not wait
MIN_SCHEDULE_AHEAD = timedelta(minutes=15)
MAX_SCHEDULE_AHEAD = timedelta(days=365)


class UploadError(Exception):
    """An upload cannot be prepared or continued. ``code`` is stable; the message is written for the owner."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class Blocked(Exception):
    """The step cannot continue now but nothing is wrong with the upload (quota, reconnect, pause): park it for a person."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.reason = code, message


class Failed(Exception):
    """The upload cannot succeed as asked; retrying will not help."""


# --- preparing --------------------------------------------------------------------------------------------------------------


def _file(rel: str | None, what: str) -> Path:
    if not rel:
        raise UploadError("FILE_MISSING", f"The {what} file is missing.")
    p = resolve_data_path(rel) if not rel.startswith("resource:") else None
    if p is None or not p.is_file() or p.stat().st_size == 0:
        raise UploadError("FILE_MISSING", f"The {what} file is missing or empty.")
    return p


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        while block := f.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def _body(req: dict[str, Any]) -> dict[str, Any]:
    snippet: dict[str, Any] = {"title": req["title"], "description": req["description"], "categoryId": req["category_id"]}
    if req.get("tags"):
        snippet["tags"] = req["tags"]
    status: dict[str, Any] = {"privacyStatus": req["privacy"], "selfDeclaredMadeForKids": req["made_for_kids"]}
    if req.get("publish_at"):
        status["publishAt"] = req["publish_at"]
    return {"snippet": snippet, "status": status}


def prepare(db: Session, actor: Actor, video_id: int, *, privacy: str = "private", publish_at: datetime | None = None) -> YouTubeUpload:
    """Check everything, freeze exactly what will be sent, and queue the upload task. Nothing leaves the computer yet."""
    if not (actor.is_owner or actor.can(P.VIDEO_MANAGE)):
        raise UploadError("NOT_ALLOWED", "Only the owner or a manager may queue an upload.")
    if privacy not in yt.PRIVACY:
        raise UploadError("BAD_PRIVACY", f"privacy must be one of {', '.join(yt.PRIVACY)}")
    if publish_at is not None:
        if privacy != "private":
            raise UploadError("BAD_SCHEDULE", "YouTube schedules a video only when it is uploaded as private; it then becomes public at that time.")
        now = utcnow()
        if publish_at.tzinfo is None or publish_at < now + MIN_SCHEDULE_AHEAD or publish_at > now + MAX_SCHEDULE_AHEAD:
            raise UploadError("BAD_SCHEDULE", "The publish time must be a timezone-aware time between 15 minutes and one year from now.")
    video = db.get(Video, video_id)
    if video is None:
        raise UploadError("UNKNOWN_VIDEO", "video not found")
    res = gate.evaluate(db, video, purpose="publish")
    if not res.allowed:
        raise gate.GateBlocked(res)
    state, detail = providers.capability_state(db, "youtube", "youtube.upload")
    if state != "ready":
        raise UploadError(f"PROVIDER_{state.upper()}", f"YouTube cannot be used yet: {detail}.")
    conn = conn_svc.connection_for(db, video.channel_id)
    _require_usable(conn)
    dup = db.scalar(select(YouTubeUpload).where(YouTubeUpload.video_id == video_id, YouTubeUpload.status.in_(ACTIVE)))
    if dup is not None:
        raise UploadError("ALREADY_UPLOADED", f"This video already has upload #{dup.id} ({dup.status.replace('_', ' ').lower()}). A second copy is never created; "
                                              "the owner can abandon the first one if it is wrong.")
    ms = seo.approved(db, video_id)
    if ms is None or not seo.matches_video(ms, video):  # the gate says so too; this keeps the message specific
        raise UploadError("METADATA", "The approved metadata is missing or no longer matches the video.")
    vpath = _file(video.final_video_path, "video")
    _file(video.thumbnail_path, "thumbnail")
    quota.require(db, "videos.insert", "thumbnails.set", "videos.list")
    md = video.metadata_json or {}
    req = {"title": video.title, "description": md.get("description", ""), "tags": md.get("tags", []), "category_id": conn.category_id,  # type: ignore[union-attr]
           "made_for_kids": conn.made_for_kids, "privacy": privacy, "publish_at": publish_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if publish_at else None}
    up = YouTubeUpload(video_id=video_id, connection_id=conn.id, status="PREPARED", privacy_requested=privacy, publish_at=publish_at, request=req,  # type: ignore[union-attr]
                       file_ref=video.final_video_path, thumbnail_ref=video.thumbnail_path, metadata_set_id=ms.id, file_sha256=_sha(vpath),
                       bytes_total=vpath.stat().st_size, created_by=actor.label)
    db.add(up)
    db.flush()
    ch = db.get(Channel, video.channel_id)
    t = tasks.create(db, actor, title=f"Upload to YouTube ({privacy}) — {video.title}", task_type="youtube.upload", assigned_agent_slug="youtube-upload-specialist",
                     video_id=video_id, channel_slug=ch.slug if ch else None, input_refs={"upload_id": up.id},
                     idempotency_key=f"yt-upload:{up.id}")
    up.task_id = t.id
    audit.record(db, actor, "youtube.upload_prepared", "youtube_upload", up.id, {"video": video_id, "privacy": privacy, "publish_at": req["publish_at"], "task": t.id})
    return up


def _require_usable(conn: YouTubeConnection | None) -> None:
    if conn is None or conn.status != "CONNECTED":
        raise UploadError("NOT_CONNECTED", "This channel is not connected to YouTube (or the connection is not confirmed). Connect and confirm it in Settings.")
    if conn.made_for_kids is None:
        raise UploadError("AUDIENCE_REQUIRED", "Say in Settings whether this channel's videos are made for kids; YouTube requires it.")


def abandon(db: Session, actor: Actor, upload_id: int, note: str) -> YouTubeUpload:
    """Owner only: forget a stuck or wrong upload so a new one may be queued. Delete the video in YouTube Studio yourself if it exists."""
    if not actor.is_owner:
        raise UploadError("OWNER_ONLY", "Only the owner can abandon an upload.")
    up = db.get(YouTubeUpload, upload_id)
    if up is None:
        raise UploadError("UNKNOWN_UPLOAD", "upload not found")
    if not note.strip():
        raise UploadError("NOTE_REQUIRED", "Say why; it is kept on record.")
    up.status, up.error = "ABANDONED", f"abandoned by the owner: {note.strip()}"
    if up.task_id:
        t = db.get(Task, up.task_id)
        if t is not None and t.status not in ("COMPLETED", "FAILED", "CANCELLED"):
            tasks.cancel(db, actor, t.id, f"upload abandoned: {note.strip()}")
    audit.record(db, actor, "youtube.upload_abandoned", "youtube_upload", up.id, {"note": note.strip(), "youtube_video_id": up.youtube_video_id})
    return up


# --- performing -------------------------------------------------------------------------------------------------------------


def _save(upload_id: int, **fields: Any) -> None:
    with session_scope() as db:
        up = db.get(YouTubeUpload, upload_id)
        for k, v in fields.items():
            setattr(up, k, v)


def _fail_api(upload_id: int, e: yt.YouTubeApiError, step: str) -> Exception:
    """Translate an API error into 'park it' or 'it cannot work', recording what happened. Quota was charged either way."""
    if e.is_quota:
        return Blocked("YOUTUBE_QUOTA", f"YouTube says the daily quota is used up ({e.message}). The upload keeps its place and can continue after the quota resets "
                                        "(midnight Pacific time).")
    if e.is_auth:
        return Blocked("YOUTUBE_AUTH", "YouTube no longer accepts the stored permission. Reconnect the channel in Settings, then release the task.")
    if e.reason == "uploadLimitExceeded":
        _save(upload_id, status="FAILED", error="YouTube's daily upload limit for this channel was reached.")
        return Failed("YouTube's upload limit for this channel was reached today; try again tomorrow.")
    if e.retryable:
        return RuntimeError(f"{step}: {e.reason}: {e.message}")  # transient: the queue retries with backoff, and the upload resumes
    _save(upload_id, status="FAILED", error=f"{step}: {e.reason}: {e.message}")
    return Failed(f"{step} was refused by YouTube: {e.reason}: {e.message}")


def verify_resource(res: dict[str, Any], request: dict[str, Any], expected_channel: str) -> dict[str, Any]:
    """Compare what YouTube reports with what was asked for. Pure; every check is listed so a person can read the evidence."""
    snippet, status = res.get("snippet", {}) or {}, res.get("status", {}) or {}
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, expected: Any, actual: Any) -> None:
        checks.append({"check": name, "ok": ok, "expected": expected, "actual": actual})

    check("a video id was returned", bool(res.get("id")), "an id", res.get("id"))
    check("on the expected channel", snippet.get("channelId") == expected_channel, expected_channel, snippet.get("channelId"))
    check("title", snippet.get("title") == request["title"], request["title"], snippet.get("title"))
    check("category", str(snippet.get("categoryId")) == str(request["category_id"]), request["category_id"], snippet.get("categoryId"))
    declared = status.get("selfDeclaredMadeForKids")
    check("audience (made for kids) as declared", declared == request["made_for_kids"], request["made_for_kids"], declared)
    check("upload status", status.get("uploadStatus") in ("uploaded", "processed"), "uploaded or processed", status.get("uploadStatus"))
    privacy_ok = status.get("privacyStatus") == request["privacy"]
    check("privacy as requested", privacy_ok, request["privacy"], status.get("privacyStatus"))
    sched_ok = True
    if request.get("publish_at"):
        got = status.get("publishAt")
        try:
            sched_ok = got is not None and abs(datetime.fromisoformat(got.replace("Z", "+00:00")) - datetime.fromisoformat(request["publish_at"].replace("Z", "+00:00"))) < timedelta(seconds=60)
        except ValueError:
            sched_ok = False
        check("scheduled time as requested", sched_ok, request["publish_at"], got)
    rejected = status.get("uploadStatus") in ("failed", "rejected", "deleted")
    locked = (not privacy_ok) and status.get("privacyStatus") == "private" and request["privacy"] in ("public", "unlisted")
    others_ok = all(c["ok"] for c in checks if c["check"] != "privacy as requested")
    if rejected:
        outcome = "FAILED"
    elif not others_ok:
        outcome = "MISMATCH"
    elif locked:
        outcome = "PRIVACY_LOCKED"
    elif not privacy_ok or not sched_ok:
        outcome = "MISMATCH"
    else:
        outcome = "VERIFIED"
    return {"outcome": outcome, "checks": checks, "processing": (res.get("processingDetails") or {}).get("processingStatus"),
            "failure_reason": status.get("failureReason") or status.get("rejectionReason"), "verified_at": iso(utcnow())}


def perform(upload_id: int, *, mark_started: Callable[[dict[str, Any]], None], clear_started: Callable[[], None], heartbeat: Callable[[], None] = lambda: None,
            task_id: int | None = None, check_paused: Callable[[], None] = lambda: None) -> dict[str, Any]:
    """Run (or resume) one upload to its verified end. Raises ``Blocked``, ``Failed`` or a plain error (retry)."""
    box = TokenBox()
    # --- phase A: everything is re-checked, in a read-only transaction ---
    with session_scope(write=False) as db:
        up = db.get(YouTubeUpload, upload_id)
        if up is None:
            raise Failed("The upload record does not exist.")
        if up.status in ("VERIFIED", "PRIVACY_LOCKED", "MISMATCH", "FAILED", "ABANDONED"):
            return _result(up)  # already finished: re-running a task must not repeat any call
        conn = db.get(YouTubeConnection, up.connection_id)
        try:
            if not up.is_test:
                video = db.get(Video, up.video_id)
                res = gate.evaluate(db, video, purpose="publish")  # type: ignore[arg-type]
                if not res.allowed:
                    raise Blocked("GATE_BLOCKED", "The publication gate no longer allows this: " + "; ".join(i.message for i in res.issues))
                ms = seo.approved(db, up.video_id)  # type: ignore[arg-type]
                if ms is None or ms.id != up.metadata_set_id or not seo.matches_video(ms, video):  # type: ignore[arg-type]
                    raise Blocked("METADATA_CHANGED", "The approved metadata changed after this upload was queued. Queue a new upload.")
            control.assert_external_action_allowed(db, "upload")
            state, detail = providers.capability_state(db, "youtube", "youtube.upload")
            if state != "ready":
                raise Blocked(f"PROVIDER_{state.upper()}", f"YouTube cannot be used right now: {detail}.")
            _require_usable(conn)
            vpath = _file(up.file_ref, "video")
            if _sha(vpath) != up.file_sha256:
                raise Failed("The video file changed after the upload was queued. Queue a new upload for the current file.")
            tpath = _file(up.thumbnail_ref, "thumbnail") if up.thumbnail_ref else None
            needs = {"PREPARED": ("videos.insert", "thumbnails.set", "videos.list"), "SESSION_OPEN": ("thumbnails.set", "videos.list"),
                     "UPLOADED": ("thumbnails.set", "videos.list")}[up.status]
            quota.require(db, *needs)
        except control.ExternalActionBlocked as e:
            raise Blocked(e.code, str(e)) from e
        except quota.QuotaExceeded as e:
            raise Blocked(e.code, e.reason) from e
        except UploadError as e:
            if e.code in ("NOT_CONNECTED", "AUDIENCE_REQUIRED"):
                raise Blocked(e.code, str(e)) from e  # a person can fix these and release the task
            raise Failed(str(e)) from e
        req, status0, session_enc, google_channel = dict(up.request), up.status, up.session_uri_enc, conn.google_channel_id  # type: ignore[union-attr]
        conn_id, chunk = conn.id, settings_svc.get(db, "youtube")["chunk_mb"] * 1024 * 1024  # type: ignore[union-attr]
        vid = up.video_id
    check_paused()
    try:
        token = conn_svc.access_token(conn_id)
    except conn_svc.YouTubeError as e:
        raise Blocked(e.code, str(e)) from e
    api = yt.YouTubeClient(token, transport=conn_svc.TRANSPORT, sleep=SLEEP)

    def charge(method: str, ok: bool = True, note: str = "") -> None:
        with session_scope() as db:
            quota.record(db, method, ok=ok, video_id=vid, task_id=task_id, note=note)

    # --- phase B: open the session (re-used if it already exists) ---
    try:
        if status0 == "PREPARED":
            try:
                uri = api.start_upload(_body(req), vpath.stat().st_size, "video/mp4")
            except yt.YouTubeApiError as e:
                charge("videos.insert", ok=False, note=f"session: {e.reason}")
                raise _fail_api(upload_id, e, "opening the upload session") from e
            charge("videos.insert", note="session opened")
            _save(upload_id, session_uri_enc=box.encrypt(uri), status="SESSION_OPEN")
            offset = 0
        else:
            uri = box.decrypt(session_enc) if session_enc else ""  # type: ignore[arg-type]
            offset = 0
        # --- phase C: the bytes. From here the owner must reconcile an interrupted task ---
        if status0 in ("PREPARED", "SESSION_OPEN"):
            mark_started({"step": "uploading", "upload": upload_id, "session": "resumed" if status0 == "SESSION_OPEN" else "new"})
            if status0 == "SESSION_OPEN":
                try:
                    state, val = api.query_upload(uri, vpath.stat().st_size)  # where is the existing session? never assume
                except yt.YouTubeApiError as e:
                    raise _fail_api(upload_id, e, "asking where the upload stood") from e
                resource = val if state == "complete" else None
                offset = 0 if state == "complete" else int(val)  # type: ignore[arg-type]
            else:
                resource = None
            if resource is None:
                def progress(n: int) -> None:
                    _save(upload_id, bytes_sent=n)
                    heartbeat()
                try:
                    resource = api.upload(uri, vpath, start=offset, chunk_bytes=chunk, on_progress=progress)
                except yt.YouTubeApiError as e:
                    raise _fail_api(upload_id, e, "sending the video") from e
            ref = write_json_artifact(f"youtube/uploads/{upload_id}", "insert-response.json",
                                      {"provider": "youtube", "endpoint": "videos.insert (resumable upload, final response)", "status_code": 200, "simulated": False,
                                       "retrieved_at": iso(utcnow()), "response": resource})
            _save(upload_id, status="UPLOADED", youtube_video_id=resource.get("id"), response_ref=ref, bytes_sent=vpath.stat().st_size)
            if not resource.get("id"):
                _save(upload_id, status="FAILED", error="YouTube accepted the bytes but returned no video id.")
                raise Failed("YouTube accepted the bytes but returned no video id.")
        # --- phase D: read it back ---
        with session_scope(write=False) as db:
            yid = db.get(YouTubeUpload, upload_id).youtube_video_id  # type: ignore[union-attr]
        try:
            listing = api.videos_list(yid)  # type: ignore[arg-type]
        except yt.YouTubeApiError as e:
            charge("videos.list", ok=False, note=f"read-back: {e.reason}")
            raise _fail_api(upload_id, e, "reading the video back") from e
        charge("videos.list", note="read-back")
        items = listing.get("items") or []
        if not items:
            verification = {"outcome": "MISMATCH", "checks": [{"check": "the video exists when read back", "ok": False, "expected": yid, "actual": None}],
                            "verified_at": iso(utcnow()), "processing": None, "failure_reason": None}
        else:
            verification = verify_resource(items[0], req, google_channel or "")
        vref = write_json_artifact(f"youtube/uploads/{upload_id}", "verification.json",
                                   {"provider": "youtube", "endpoint": "videos.list (read-back)", "status_code": 200, "simulated": False, "retrieved_at": iso(utcnow()),
                                    "asked_for": req, "response": listing, "verification": verification})
        _save(upload_id, status=verification["outcome"], verification={**verification, "ref": vref},
              error=None if verification["outcome"] in ("VERIFIED", "PRIVACY_LOCKED") else f"read-back: {verification['outcome']}")
        # --- phase E: thumbnail (the upload stands whether or not this works) ---
        if tpath is not None and verification["outcome"] in ("VERIFIED", "PRIVACY_LOCKED"):
            try:
                api.thumbnails_set(yid, tpath)  # type: ignore[arg-type]
                charge("thumbnails.set", note="thumbnail")
                _save(upload_id, thumbnail_status="SET", thumbnail_detail=None)
            except yt.YouTubeApiError as e:
                charge("thumbnails.set", ok=False, note=f"thumbnail: {e.reason}")
                hint = (" YouTube only lets verified channels set custom thumbnails; verify the channel (phone) in YouTube Studio." if e.status == 403 else "")
                _save(upload_id, thumbnail_status="FAILED", thumbnail_detail=f"{e.reason}: {e.message}.{hint}")
        elif tpath is None:
            _save(upload_id, thumbnail_status="SKIPPED", thumbnail_detail="no thumbnail file")
    except KeyMissing as e:
        clear_started()
        raise Blocked("KEY_MISSING", str(e)) from e
    except Blocked:
        clear_started()  # parked on purpose with the upload session intact; resuming asks the session, so nothing can be duplicated
        raise
    if vid is not None:  # a real video: record the verified outcome on it
        with session_scope() as db:
            up = db.get(YouTubeUpload, upload_id)
            if up.status == "VERIFIED":  # type: ignore[union-attr]
                videos.record_upload(db, Actor.system("youtube"), vid, upload_id)
            elif up.status == "PRIVACY_LOCKED":  # type: ignore[union-attr]
                videos.record_upload_problem(db, Actor.system("youtube"), vid, upload_id)
    with session_scope(write=False) as db:
        return _result(db.get(YouTubeUpload, upload_id))  # type: ignore[arg-type]


def _result(up: YouTubeUpload) -> dict[str, Any]:
    return {"upload_id": up.id, "status": up.status, "youtube_video_id": up.youtube_video_id, "privacy_requested": up.privacy_requested,
            "publish_at": iso(up.publish_at) if up.publish_at else None, "thumbnail": up.thumbnail_status, "error": up.error, "insert_ref": up.response_ref,
            "verification": {k: v for k, v in (up.verification or {}).items() if k in ("outcome", "checks", "processing", "failure_reason", "ref")}}


def serialize(up: YouTubeUpload) -> dict[str, Any]:
    d = _result(up)
    d.update({"video_id": up.video_id, "is_test": up.is_test, "bytes_total": up.bytes_total, "bytes_sent": up.bytes_sent, "task_id": up.task_id,
              "thumbnail_detail": up.thumbnail_detail, "created_by": up.created_by, "created_at": iso(up.created_at), "request": up.request,
              "watch_url": f"https://www.youtube.com/watch?v={up.youtube_video_id}" if up.youtube_video_id else None})
    return d
