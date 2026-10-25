from __future__ import annotations

import os
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

from app.db import session_scope
from app.models.auth import User
from app.models.videos import Video
from app.models.youtube import YouTubeConnection
from app.security.permissions import Actor
from app.services import approvals, org, videos
from app.services import settings as settings_svc
from app.storage import write_bytes_artifact
from app.timeutil import utcnow
from tests.api_fixtures import api_env, client, fast_hashing, make_client, owner_client, viewer_client  # noqa: F401
from tests.google_fixtures import google  # noqa: F401
from tests.prod_helpers import BDAY
from tests.test_gate_videos import make_ready_video


def oauth_dance(c, fake, slug=BDAY):
    url = c.post(f"/api/youtube/{slug}/connect").json()["url"]
    q = parse_qs(urlsplit(url).query)
    fake.challenge = q["code_challenge"][0]
    return url, q["state"][0]


def sign_in(c, fake, slug=BDAY):
    _, state = oauth_dance(c, fake, slug)
    return c.get("/api/youtube/callback", params={"state": state, "code": "good-code"}, follow_redirects=False)


def confirmed(c, fake, slug=BDAY):
    sign_in(c, fake, slug)
    r = c.post(f"/api/youtube/{slug}/confirm", json={"google_channel_id": fake.channel["id"], "made_for_kids": True})
    assert r.status_code == 200, r.text
    return r.json()


def test_everything_requires_signing_in(client):
    for method, url in (("GET", "/api/youtube/status"), ("POST", "/api/youtube/happy-birthday-songs/connect"), ("GET", "/api/youtube/callback"),
                        ("POST", "/api/youtube/happy-birthday-songs/confirm"), ("POST", "/api/youtube/happy-birthday-songs/disconnect"),
                        ("GET", "/api/videos/1/uploads"), ("POST", "/api/videos/1/upload"), ("POST", "/api/youtube/uploads/1/abandon")):
        assert client.request(method, url, json={} if method == "POST" else None).status_code == 401, (method, url)


def test_status_says_what_is_missing_and_that_nothing_is_live_verified(owner_client):
    d = owner_client.get("/api/youtube/status").json()
    assert d["oauth_client_configured"] is False and d["live_verified"] is False and "mock" in d["note"]
    assert [c["channel"] for c in d["channels"]] == ["happy-birthday-songs", "medicine-information", "ai-story-videos"]
    assert {c["connection"]["status"] for c in d["channels"]} == {"NOT_CONNECTED"}
    assert d["upload_capability"]["state"] in ("not_configured", "needs_attestation") and d["quota"]["used_today"] == 0
    r = owner_client.post(f"/api/youtube/{BDAY}/connect")
    assert r.status_code == 409 and "docs/YOUTUBE.md" in r.json()["detail"]


def test_the_owner_connects_and_confirms_a_channel_through_the_dashboard_flow(owner_client, google):
    url, state = oauth_dance(owner_client, google)
    q = parse_qs(urlsplit(url).query)
    assert q["redirect_uri"] == ["http://localhost:8000/api/youtube/callback"] and q["code_challenge_method"] == ["S256"]
    r = owner_client.get("/api/youtube/callback", params={"state": state, "code": "good-code"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "http://localhost:3000/settings?youtube=signed_in&channel=happy-birthday-songs"
    st = {c["channel"]: c["connection"] for c in owner_client.get("/api/youtube/status").json()["channels"]}[BDAY]
    assert st["status"] == "PENDING_CONFIRMATION" and st["google_channel_title"].startswith("Happy Birthday Songs") and st["made_for_kids"] is None
    dumped = str(owner_client.get("/api/youtube/status").json())
    assert "refresh-secret-1" not in dumped and "access-1" not in dumped and "token" not in dumped.lower().replace("tokens", "")
    bad = owner_client.post(f"/api/youtube/{BDAY}/confirm", json={"google_channel_id": "UC_other", "made_for_kids": True})
    assert bad.status_code == 409 and "not the one you were shown" in bad.json()["detail"]
    assert owner_client.post(f"/api/youtube/{BDAY}/confirm", json={"google_channel_id": google.channel["id"]}).status_code == 422  # the audience answer is required
    ok = owner_client.post(f"/api/youtube/{BDAY}/confirm", json={"google_channel_id": google.channel["id"], "made_for_kids": True})
    assert ok.status_code == 200 and ok.json()["status"] == "CONNECTED" and ok.json()["category_name"] == "Music"


def test_failures_during_sign_in_come_back_to_the_dashboard_with_a_message(owner_client, google):
    _, state = oauth_dance(owner_client, google)
    r = owner_client.get("/api/youtube/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert r.status_code == 303 and "youtube=error" in r.headers["location"] and "access_denied" in r.headers["location"]
    r = owner_client.get("/api/youtube/callback", params={"state": "forged", "code": "good-code"}, follow_redirects=False)
    assert r.status_code == 303 and "youtube=error" in r.headers["location"] and "single-use" in r.headers["location"].replace("%20", " ")
    google.send_refresh_token = False
    r = owner_client.get("/api/youtube/callback", params={"state": state, "code": "good-code"}, follow_redirects=False)
    assert "refresh" in r.headers["location"].lower()


def test_a_sign_in_link_only_works_for_the_signed_in_owner_and_only_once(owner_client, viewer_client, google):
    _, state = oauth_dance(owner_client, google)
    assert viewer_client.get("/api/youtube/callback", params={"state": state, "code": "good-code"}, follow_redirects=False).status_code == 403
    assert owner_client.get("/api/youtube/callback", params={"state": state, "code": "good-code"}, follow_redirects=False).headers["location"].endswith("channel=happy-birthday-songs")
    again = owner_client.get("/api/youtube/callback", params={"state": state, "code": "good-code"}, follow_redirects=False)
    assert "youtube=error" in again.headers["location"]


def test_viewers_can_read_the_status_but_change_nothing(owner_client, viewer_client, google):
    assert viewer_client.get("/api/youtube/status").status_code == 200
    for url in (f"/api/youtube/{BDAY}/connect", f"/api/youtube/{BDAY}/confirm", f"/api/youtube/{BDAY}/disconnect", "/api/videos/1/upload", "/api/youtube/uploads/1/abandon"):
        assert viewer_client.post(url, json={}).status_code == 403, url


def test_writes_need_the_csrf_token(owner_client, google):
    token = owner_client.headers.pop("x-csrf-token")
    assert owner_client.post(f"/api/youtube/{BDAY}/connect").status_code == 403
    owner_client.headers["x-csrf-token"] = token
    assert owner_client.post(f"/api/youtube/{BDAY}/connect").status_code == 200


def test_disconnecting_forgets_the_tokens_and_revokes_at_google(owner_client, google):
    confirmed(owner_client, google)
    assert owner_client.post(f"/api/youtube/{BDAY}/disconnect").status_code == 200
    assert google.revoked
    st = {c["channel"]: c["connection"] for c in owner_client.get("/api/youtube/status").json()["channels"]}[BDAY]
    assert st["status"] == "REVOKED"
    with session_scope(write=False) as s:
        c = s.query(YouTubeConnection).one()
        assert c.refresh_token_enc is None and c.access_token_enc is None


def ready_video_for_api(fake, mode="APPROVAL_FIRST"):
    with session_scope() as s:
        agent = lambda slug: org.actor_for_slug(s, slug)  # noqa: E731
        v = make_ready_video(s, agent)
        v.final_video_path = write_bytes_artifact("videos", f"api{v.id}.mp4", os.urandom(300_000))
        v.thumbnail_path = write_bytes_artifact("videos", f"api{v.id}.png", b"\x89PNG" + os.urandom(500))
        videos.set_stage(s, agent("production-manager"), v.id, videos.VideoStage.READY)
        owner = s.query(User).one()
        o = Actor.for_user(owner.id, owner.username, owner.role)
        settings_svc.set_value(s, o, "operating_mode", mode)
        settings_svc.set_value(s, o, "provider_attestations", {"youtube": {"api_access_confirmed": True}})
        approvals.decide(s, o, videos.request_publication_approval(s, agent("publishing-manager"), v.id).id, approve=True)
        return v.id


def test_an_upload_is_queued_through_the_api_with_every_check_and_listed_afterwards(owner_client, google):
    confirmed(owner_client, google)
    vid = ready_video_for_api(google)
    r = owner_client.post(f"/api/videos/{vid}/upload", json={"privacy": "private"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "PREPARED" and d["request"]["made_for_kids"] is True and d["request"]["category_id"] == "10" and d["task_id"]
    again = owner_client.post(f"/api/videos/{vid}/upload", json={"privacy": "private"})
    assert again.status_code == 409 and "second copy" in again.json()["detail"]
    listed = owner_client.get(f"/api/videos/{vid}/uploads").json()["uploads"]
    assert [u["upload_id"] for u in listed] == [d["upload_id"]] and listed[0]["watch_url"] is None
    gone = owner_client.post(f"/api/youtube/uploads/{d['upload_id']}/abandon", json={"note": "wrong cut"})
    assert gone.status_code == 200 and gone.json()["status"] == "ABANDONED"
    assert owner_client.get("/api/videos/999/uploads").status_code == 404


def test_the_publication_gate_is_reported_in_full_when_an_upload_is_refused(owner_client, google):
    confirmed(owner_client, google)
    vid = ready_video_for_api(google, mode="DEVELOPMENT")
    r = owner_client.post(f"/api/videos/{vid}/upload", json={"privacy": "private"})
    assert r.status_code == 409 and "MODE_DEVELOPMENT" in r.json()["gate"]["issues"][0]["code"]
    assert owner_client.post(f"/api/videos/{vid}/upload", json={"privacy": "everyone"}).status_code in (409, 422)
    when = (utcnow() + timedelta(minutes=1)).isoformat()
    assert owner_client.post(f"/api/videos/{vid}/upload", json={"privacy": "private", "publish_at": when}).status_code == 409
    with session_scope(write=False) as s:
        assert s.get(Video, vid).youtube_video_id is None
