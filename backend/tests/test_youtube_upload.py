"""Uploading to YouTube: every precondition re-checked, no duplicate copies, success only from a read-back."""

from __future__ import annotations

import os
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import session_scope
from app.enums import TaskStatus as S
from app.models.audit import AuditEvent
from app.models.tasks import Task
from app.models.videos import Video
from app.models.youtube import QuotaEntry, YouTubeUpload
from app.services import approvals, control, gate, org, tasks, videos
from app.services import settings as settings_svc
from app.storage import write_bytes_artifact
from app.timeutil import utcnow
from app.worker.runner import HANDLERS, Runner
from app.youtube import connection as conn_svc
from app.youtube import quota, upload
from app.youtube.upload import UploadError
from tests.google_fixtures import google  # noqa: F401
from tests.test_gate_videos import make_ready_video
from tests.test_youtube_connection import connected

MB = 1024 * 1024


@pytest.fixture
def ready(db, agent, owner, google):  # noqa: F811
    """A video that clears every gate condition, a confirmed connection, APPROVAL_FIRST mode and the owner's approval."""
    v = make_ready_video(db, agent)
    v.final_video_path = write_bytes_artifact("videos", f"yt{v.id}.mp4", os.urandom(int(2.5 * MB)))
    v.thumbnail_path = write_bytes_artifact("videos", f"yt{v.id}.png", b"\x89PNG" + os.urandom(2000))
    videos.set_stage(db, agent("production-manager"), v.id, videos.VideoStage.READY)
    connected(db, owner, google)
    settings_svc.set_value(db, owner, "operating_mode", "APPROVAL_FIRST")
    settings_svc.set_value(db, owner, "youtube", {"chunk_mb": 1})
    settings_svc.set_value(db, owner, "provider_attestations", {"youtube": {"api_access_confirmed": True, "note": "terms read"}})
    approvals.decide(db, owner, videos.request_publication_approval(db, agent("publishing-manager"), v.id).id, approve=True)
    db.commit()
    return v.id


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(upload, "SLEEP", lambda s: None)


def run_one(db=None):
    if db is not None:
        db.commit()  # the test session is a write session; let go of it before the worker writes
    return Runner("t", HANDLERS).run_once()


def queue(db, owner, vid, **kw):
    up = upload.prepare(db, owner, vid, **kw)
    db.commit()
    return up.id, up.task_id


def row(uid) -> YouTubeUpload:
    with session_scope(write=False) as s:
        u = s.get(YouTubeUpload, uid)
        s.expunge(u)
        return u


def task_state(tid):
    with session_scope(write=False) as s:
        t = s.get(Task, tid)
        return t.status, t.blocked_reason, (t.error or {}).get("message"), "external_action_started" in (t.output_refs or {})


def test_a_private_upload_is_read_back_and_only_then_recorded(db, owner, ready, google):
    uid, tid = queue(db, owner, ready)
    assert run_one(db) == tid
    u = row(uid)
    assert u.status == "VERIFIED" and u.youtube_video_id == "vid001" and u.thumbnail_status == "SET" and u.bytes_sent == u.bytes_total
    sent = google.videos["vid001"]
    assert sent["snippet"]["title"] == u.request["title"] and sent["status"]["privacyStatus"] == "private" and sent["status"]["selfDeclaredMadeForKids"] is True
    assert sent["snippet"]["categoryId"] == "10" and len(sent["bytes"]) == u.bytes_total  # every byte arrived exactly once
    # the read-back happened and is what was recorded
    assert google.calls("/youtube/v3/videos?") and {c["check"] for c in u.verification["checks"]} >= {"on the expected channel", "title", "privacy as requested", "audience (made for kids) as declared"}
    assert all(c["ok"] for c in u.verification["checks"])
    with session_scope(write=False) as s:
        v = s.get(Video, ready)
        assert v.youtube_video_id == "vid001" and v.metadata_json["youtube_state"] == "UPLOADED_PRIVATE" and v.stage == "READY"  # private is not published
    assert task_state(tid)[0] == S.SUBMITTED.value


def test_the_manager_can_verify_the_task_from_the_saved_responses(db, owner, ready, google):
    uid, tid = queue(db, owner, ready)
    run_one(db)
    with session_scope() as s:
        tasks.review(s, org.actor_for_slug(s, "publishing-manager"), tid, approve=True, notes="read-back file re-hashed")
    assert task_state(tid)[0] == S.COMPLETED.value
    with session_scope(write=False) as s:
        from app.models.tasks import Evidence
        kinds = {e.kind: e.verification_status for e in s.scalars(select(Evidence).where(Evidence.task_id == tid))}
    assert kinds == {"api_response": "verified", "db_record": "verified"}


def test_a_public_upload_marks_the_video_published_only_after_verification(db, owner, ready, google):
    uid, tid = queue(db, owner, ready, privacy="public")
    run_one(db)
    assert row(uid).status == "VERIFIED"
    with session_scope(write=False) as s:
        v = s.get(Video, ready)
        assert v.stage == "PUBLISHED" and v.published_at is not None and v.metadata_json["youtube_state"] == "PUBLISHED"


def test_scheduling_uploads_private_with_a_publish_time_and_verifies_it(db, owner, ready, google):
    when = utcnow() + timedelta(days=2)
    uid, tid = queue(db, owner, ready, privacy="private", publish_at=when)
    run_one(db)
    u = row(uid)
    assert u.status == "VERIFIED" and u.request["publish_at"].endswith("Z") and google.videos["vid001"]["status"]["publishAt"] == u.request["publish_at"]
    assert any(c["check"] == "scheduled time as requested" and c["ok"] for c in u.verification["checks"])
    with session_scope(write=False) as s:
        v = s.get(Video, ready)
        assert v.stage == "SCHEDULED" and v.publish_at is not None and v.metadata_json["youtube_state"] == "SCHEDULED_ON_YOUTUBE"


@pytest.mark.parametrize("kw,code", [({"privacy": "everyone"}, "BAD_PRIVACY"), ({"privacy": "public", "publish_at": utcnow() + timedelta(days=1)}, "BAD_SCHEDULE"),
                                     ({"publish_at": utcnow() + timedelta(minutes=2)}, "BAD_SCHEDULE"), ({"publish_at": utcnow() + timedelta(days=400)}, "BAD_SCHEDULE"),
                                     ({"publish_at": (utcnow() + timedelta(days=1)).replace(tzinfo=None)}, "BAD_SCHEDULE")])
def test_bad_requests_are_refused_before_anything_is_queued(db, owner, ready, kw, code):
    with pytest.raises(UploadError) as e:
        upload.prepare(db, owner, ready, **kw)
    assert e.value.code == code
    assert db.scalar(select(YouTubeUpload)) is None and db.scalar(select(Task).where(Task.task_type == "youtube.upload")) is None


def test_the_publication_gate_applies_in_full(db, owner, agent, google):
    v = make_ready_video(db, agent)
    connected(db, owner, google)
    db.commit()
    with pytest.raises(gate.GateBlocked) as e:  # DEVELOPMENT mode and no owner approval
        upload.prepare(db, owner, v.id)
    assert e.value.result.codes() == ["MODE_DEVELOPMENT"]
    settings_svc.set_value(db, owner, "operating_mode", "APPROVAL_FIRST")
    with pytest.raises(gate.GateBlocked) as e:
        upload.prepare(db, owner, v.id)
    assert e.value.result.codes() == ["OWNER_APPROVAL_REQUIRED"]


def test_a_demonstration_video_can_never_be_uploaded(db, owner, ready):
    with session_scope() as s:
        s.get(Video, ready).metadata_json = {**s.get(Video, ready).metadata_json, "demo": True}
    with pytest.raises(gate.GateBlocked) as e:
        upload.prepare(db, owner, ready)
    assert "DEMO_CONTENT" in e.value.result.codes()


def test_the_channel_must_be_connected_confirmed_and_have_an_audience_setting(db, owner, agent, google):
    v = make_ready_video(db, agent)
    settings_svc.set_value(db, owner, "operating_mode", "APPROVAL_FIRST")
    settings_svc.set_value(db, owner, "provider_attestations", {"youtube": {"api_access_confirmed": True}})
    approvals.decide(db, owner, videos.request_publication_approval(db, agent("publishing-manager"), v.id).id, approve=True)
    db.commit()
    with pytest.raises(UploadError) as e:
        upload.prepare(db, owner, v.id)
    assert e.value.code == "NOT_CONNECTED"
    from tests.test_youtube_connection import sign_in
    sign_in(db, owner, google)
    db.expire_all()
    with pytest.raises(UploadError) as e:  # signed in, but the owner has not confirmed the channel
        upload.prepare(db, owner, v.id)
    assert e.value.code == "NOT_CONNECTED"


def test_a_video_is_never_uploaded_twice(db, owner, ready, google):
    uid, tid = queue(db, owner, ready)
    with pytest.raises(UploadError) as e:
        upload.prepare(db, owner, ready)  # queued, not yet run
    assert e.value.code == "ALREADY_UPLOADED"
    run_one(db)
    with pytest.raises(UploadError) as e:
        upload.prepare(db, owner, ready)  # uploaded
    assert e.value.code == "ALREADY_UPLOADED" and len(google.videos) == 1
    with pytest.raises(UploadError, match="Only the owner"):
        upload.abandon(db, org.actor_for_slug(db, "ceo"), uid, "no")
    upload.abandon(db, owner, uid, "uploaded the wrong cut")
    db.commit()
    assert row(uid).status == "ABANDONED"
    assert queue(db, owner, ready)[0] != uid  # only an explicit, recorded owner decision allows another


def test_running_a_finished_task_again_repeats_no_call(db, owner, ready, google):
    uid, tid = queue(db, owner, ready)
    run_one(db)
    before = len(google.requests)
    out = upload.perform(uid, mark_started=lambda d: None, clear_started=lambda: None)
    assert out["status"] == "VERIFIED" and len(google.requests) == before


# --- the awkward network cases -----------------------------------------------------------------------------------------------


def test_a_dropped_connection_resumes_the_same_session_without_duplicating_bytes(db, owner, ready, google):
    google.drop_after_bytes = [300_000]  # the first chunk dies after 300 000 bytes were accepted
    uid, tid = queue(db, owner, ready)
    run_one(db)
    u = row(uid)
    assert u.status == "VERIFIED" and len(google.sessions) == 1 and len(google.videos) == 1  # one session, one video
    assert bytes(google.videos["vid001"]["bytes"]) == open(get_settings().data_dir / u.file_ref, "rb").read()  # byte-for-byte


def test_a_transient_server_error_is_retried_inside_the_upload(db, owner, ready, google):
    google.fail["upload.chunk"] = [(503, "backendError", "try later"), (500, "backendError", "again")]
    uid, tid = queue(db, owner, ready)
    run_one(db)
    assert row(uid).status == "VERIFIED" and len(google.sessions) == 1


def test_quota_exhaustion_parks_the_upload_with_its_session_and_it_resumes_later(db, owner, ready, google):
    google.fail["upload.chunk"] = [(403, "quotaExceeded", "The request cannot be completed because you have exceeded your quota.")]
    uid, tid = queue(db, owner, ready)
    run_one(db)
    st, reason, _, marker = task_state(tid)
    assert st == S.BLOCKED.value and "quota" in reason.lower() and "midnight Pacific" in reason
    assert row(uid).status == "SESSION_OPEN" and not marker  # parked cleanly: the session is kept, so the manager may release it
    with session_scope() as s:
        tasks.unblock(s, org.actor_for_slug(s, "publishing-manager"), tid, "quota reset")
    run_one(db)
    assert row(uid).status == "VERIFIED" and len(google.sessions) == 1 and len(google.videos) == 1  # resumed, not restarted


def test_a_crash_after_the_last_byte_is_reconciled_by_asking_google_not_by_uploading_again(db, owner, ready, google):
    uid, tid = queue(db, owner, ready)
    run_one(db)
    with session_scope() as s:  # simulate: the response was lost, so the app only knows the session was open
        u = s.get(YouTubeUpload, uid)
        u.status, u.youtube_video_id, u.verification = "SESSION_OPEN", None, {}
        tk = s.get(Task, tid)
        tk.status = S.QUEUED.value
        tk.output_refs = {}
        tk.locked_by = None
    before_sessions = len(google.sessions)
    run_one(db)
    assert row(uid).status == "VERIFIED" and len(google.sessions) == before_sessions == 1  # asked the session, found it complete


def test_the_upload_session_url_is_stored_encrypted(db, owner, ready, google):
    uid, _ = queue(db, owner, ready)
    run_one(db)
    u = row(uid)
    assert u.session_uri_enc and "googleapis" not in u.session_uri_enc and "upload_id" not in u.session_uri_enc


# --- verification --------------------------------------------------------------------------------------------------------------


def test_an_unverified_project_that_gets_a_public_video_locked_private_is_reported_as_not_published(db, owner, ready, google):
    google.lock_private = True
    uid, tid = queue(db, owner, ready, privacy="public")
    run_one(db)
    u = row(uid)
    assert u.status == "PRIVACY_LOCKED" and u.thumbnail_status == "SET"
    st, reason, _, _ = task_state(tid)
    assert st == S.BLOCKED.value and "NOT published" in reason and "compliance audit" in reason
    with session_scope(write=False) as s:
        v = s.get(Video, ready)
        assert v.stage != "PUBLISHED" and v.published_at is None and v.metadata_json["youtube_state"] == "PRIVACY_LOCKED" and v.youtube_video_id == "vid001"


def test_a_video_that_landed_on_the_wrong_channel_is_a_mismatch_not_a_success(db, owner, ready, google):
    google.channel_for_videos = "UC_somebody_else"
    uid, tid = queue(db, owner, ready)
    run_one(db)
    assert row(uid).status == "MISMATCH"
    st, reason, _, _ = task_state(tid)
    assert st == S.BLOCKED.value and "on the expected channel" in reason
    with session_scope(write=False) as s:
        assert s.get(Video, ready).metadata_json.get("youtube_state") is None


def test_verify_resource_covers_each_documented_failure():
    req = {"title": "T", "category_id": "10", "made_for_kids": True, "privacy": "public", "publish_at": None}
    good = {"id": "x", "snippet": {"channelId": "C", "title": "T", "categoryId": "10"}, "status": {"uploadStatus": "uploaded", "privacyStatus": "public", "selfDeclaredMadeForKids": True}}
    assert upload.verify_resource(good, req, "C")["outcome"] == "VERIFIED"
    import copy
    for mutate, outcome in (
        (lambda r: r["snippet"].update(title="Other"), "MISMATCH"), (lambda r: r["status"].update(selfDeclaredMadeForKids=False), "MISMATCH"),
        (lambda r: r["status"].update(privacyStatus="private"), "PRIVACY_LOCKED"), (lambda r: r["status"].update(privacyStatus="unlisted"), "MISMATCH"),
        (lambda r: r["status"].update(uploadStatus="rejected", rejectionReason="duplicate"), "FAILED"), (lambda r: r["status"].update(uploadStatus="failed"), "FAILED"),
        (lambda r: r.pop("id"), "MISMATCH"),
    ):
        r = copy.deepcopy(good)
        mutate(r)
        assert upload.verify_resource(r, req, "C")["outcome"] == outcome
    assert upload.verify_resource(good, req, "C_other")["outcome"] == "MISMATCH"


def test_a_missing_video_on_read_back_is_a_mismatch(db, owner, ready, google):
    google.fail["videos.list"] = []
    orig = google._handle

    def hide(req):
        r = orig(req)
        if req.url.path == "/youtube/v3/videos" and req.method == "GET":
            return httpx.Response(200, json={"items": []})
        return r
    google.transport = httpx.MockTransport(hide)
    import app.youtube.connection as c
    c.TRANSPORT = google.transport
    uid, tid = queue(db, owner, ready)
    run_one(db)
    assert row(uid).status == "MISMATCH" and task_state(tid)[0] == S.BLOCKED.value


def test_a_refused_thumbnail_does_not_undo_the_upload_and_says_why(db, owner, ready, google):
    google.thumbnail_error = (403, "forbidden", "The authenticated user doesn't have permissions to upload and set custom video thumbnails.")
    uid, tid = queue(db, owner, ready)
    run_one(db)
    u = row(uid)
    assert u.status == "VERIFIED" and u.thumbnail_status == "FAILED" and "verify the channel" in u.thumbnail_detail
    assert task_state(tid)[0] == S.SUBMITTED.value


# --- guards at run time ----------------------------------------------------------------------------------------------------------


def test_changes_after_queueing_are_caught_when_the_task_runs(db, owner, ready, google):
    uid, tid = queue(db, owner, ready)
    with session_scope() as s:
        v = s.get(Video, ready)
        v.title = "Edited after queueing"  # metadata no longer matches what was approved
    run_one(db)
    st, reason, _, _ = task_state(tid)
    assert st == S.BLOCKED.value and "metadata" in reason.lower() and not google.requests or not google.calls("upload/youtube")
    assert not google.sessions


def test_a_changed_video_file_is_refused(db, owner, ready, google):
    uid, tid = queue(db, owner, ready)
    path = get_settings().data_dir / row(uid).file_ref
    path.write_bytes(path.read_bytes() + b"x")
    run_one(db)
    assert task_state(tid)[0] == S.FAILED.value and "changed after the upload was queued" in task_state(tid)[2] and not google.sessions


def test_pause_and_mode_stop_a_queued_upload_before_any_call(db, owner, ready, google):
    uid, tid = queue(db, owner, ready)
    seen = len(google.requests)  # the sign-in made a few calls already
    control.pause(db, owner, "stop everything")
    db.commit()
    run_one(db)
    assert len(google.requests) == seen and task_state(tid)[0] != S.SUBMITTED.value
    control.resume(db, owner, "ok")
    settings_svc.set_value(db, owner, "operating_mode", "DEVELOPMENT")
    db.commit()
    run_one(db)
    st, reason, *_ = task_state(tid)
    assert not google.sessions and st in (S.BLOCKED.value, S.QUEUED.value, S.RETRY_SCHEDULED.value, S.WAITING_FOR_APPROVAL.value)


def test_a_revoked_permission_parks_the_task_and_asks_for_a_reconnect(db, owner, ready, google):
    with session_scope() as s:
        c = conn_svc.connection_for(s, s.get(Video, ready).channel_id)
        c.access_expires_at = utcnow() - timedelta(hours=1)
    google.revoked = True
    uid, tid = queue(db, owner, ready)
    run_one(db)
    st, reason, *_ = task_state(tid)
    assert st == S.BLOCKED.value and "Reconnect" in reason and not google.sessions


def test_the_quota_budget_is_checked_before_the_first_call_and_recorded_after(db, owner, ready, google):
    settings_svc.set_value(db, owner, "youtube", {"daily_quota_units": 1000})  # videos.insert alone costs 1600
    db.commit()
    with pytest.raises(quota.QuotaExceeded):
        upload.prepare(db, owner, ready)
    settings_svc.set_value(db, owner, "youtube", {"daily_quota_units": 10000})
    db.commit()
    uid, tid = queue(db, owner, ready)
    run_one(db)
    methods = [q.method for q in db.scalars(select(QuotaEntry).order_by(QuotaEntry.id))]
    assert methods.count("videos.insert") == 1 and methods.count("videos.list") == 1 and methods.count("thumbnails.set") == 1
    assert quota.summary(db)["used_today"] >= 1600 + 50 + 1


def test_every_step_leaves_an_audit_trail_without_secrets(db, owner, ready, google):
    uid, tid = queue(db, owner, ready)
    run_one(db)
    actions = [e.action for e in db.scalars(select(AuditEvent))]
    assert "youtube.upload_prepared" in actions and "video.upload_recorded" in actions
    dump = " ".join(str(e.details) for e in db.scalars(select(AuditEvent)))
    assert "refresh-secret-1" not in dump and "upload_id=" not in dump


def test_only_a_verified_upload_can_be_recorded_on_a_video(db, ready, google):
    with session_scope() as s:
        up = YouTubeUpload(video_id=ready, connection_id=conn_svc.connection_for(s, s.get(Video, ready).channel_id).id, status="UPLOADED", privacy_requested="public",
                           youtube_video_id="vidX", created_by="test")
        s.add(up)
        s.flush()
        with pytest.raises(videos.VideoError, match="verified upload"):
            videos.record_upload(s, org.actor_for_slug(s, "publishing-manager"), ready, up.id)
        with pytest.raises(videos.VideoError):  # and the generic stage setter still refuses PUBLISHED outright
            videos.set_stage(s, org.actor_for_slug(s, "production-manager"), ready, videos.VideoStage.PUBLISHED)
        assert s.get(Video, ready).stage == "READY"


def test_youtube_must_be_attested_by_the_owner_before_anything_is_queued(db, owner, ready, google):
    settings_svc.set_value(db, owner, "provider_attestations", {})
    db.commit()
    with pytest.raises(UploadError) as e:
        upload.prepare(db, owner, ready)
    assert e.value.code == "PROVIDER_NEEDS_ATTESTATION" and "confirmed" in str(e.value)
    settings_svc.set_value(db, owner, "provider_attestations", {"youtube": {"api_access_confirmed": True}})
    db.commit()
    uid, tid = queue(db, owner, ready)
    settings_svc.set_value(db, owner, "provider_attestations", {})  # withdrawn after queueing
    run_one(db)
    st, reason, *_ = task_state(tid)
    assert st == S.BLOCKED.value and "NEEDS_ATTESTATION" in reason and not google.sessions
