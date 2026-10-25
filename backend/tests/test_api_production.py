from __future__ import annotations

import json

import pytest

from app.config import get_settings
from app.db import session_scope
from app.models.videos import Video
from app.production import assets, originality, pipeline  # noqa: F401
from app.services import org
from app.storage import write_bytes_artifact
from tests.api_fixtures import api_env, client, fast_hashing, make_client, owner_client, viewer_client  # noqa: F401
from tests.prod_helpers import (
    HAVE_FFMPEG,
    LYRICS_A,
    add_brief,
    add_lyrics,
    add_music,
    add_scenes,
    add_thumbnail,
    make_video,
    tiny_mp4,
)

pytestmark = pytest.mark.skipif(not HAVE_FFMPEG, reason="FFmpeg not installed")


def produced_video(title="Garden Morning Song") -> int:
    """A video with a brief, lyrics, licensed assets, a real MP4, a thumbnail and a rights review."""
    with session_scope() as s:
        agent = lambda slug: org.actor_for_slug(s, slug)  # noqa: E731
        v = make_video(s, agent, title=title)
        add_brief(s, agent, v)
        add_lyrics(s, agent, v, LYRICS_A)
        add_music(s, agent, v)
        add_scenes(s, agent, v)
        th = add_thumbnail(s, agent, v)
        mp4 = tiny_mp4(get_settings().artifacts_dir / "src.mp4", seconds=4)
        v.title = title
        v.metadata_json = {"description": "An original instrumental demo.", "tags": ["birthday"]}
        v.final_video_path = write_bytes_artifact(f"videos/{v.id}", "final.mp4", mp4.read_bytes())
        v.thumbnail_path = th.path
        s.flush()
        originality.run_review(s, agent("copyright-originality-reviewer"), v.id)
        return v.id


def test_video_detail_shows_the_production_record_without_internals(owner_client):
    vid = produced_video()
    d = owner_client.get(f"/api/videos/{vid}").json()
    assert d["title"] == "Garden Morning Song" and d["has_video_file"] and d["has_thumbnail"]
    assert d["brief"]["status"] == "APPROVED" and d["brief"]["content"]["main_concept"] and "_artifact" not in d["brief"]["content"]
    assert d["text"]["kind"] == "lyrics" and "Morning light" in d["text"]["text"] and d["text"]["author"] == "test fixture"
    kinds = {a["kind"] for a in d["assets"]}
    assert {"music", "image", "thumbnail"} <= kinds
    music = next(a for a in d["assets"] if a["kind"] == "music")
    assert music["provider"] == "local-procedural" and music["license_type"] == "owner-generated" and music["commercial_use"] == "yes"
    assert d["rights"]["status"] == "PASS" and d["rights"]["limitations"] and d["rights"]["distinctive_contributions"]
    assert "instrumental with no vocals" in d["honesty"] and "screening aids" in d["honesty"]
    assert d["gate_for_publishing"]["allowed"] is False
    leaked = set(json.dumps(d).replace('"', " ").split()) & {"sha256", "traceback", "password_hash", "text_comparisons"}
    assert not leaked
    assert "text_comparisons" not in json.dumps(d)  # raw similarity detail is diagnostics-only


def test_raw_originality_report_is_owner_diagnostics_only_and_audited(owner_client, viewer_client):
    vid = produced_video()
    assert viewer_client.get(f"/api/diagnostics/videos/{vid}/originality").status_code == 403
    r = owner_client.get(f"/api/diagnostics/videos/{vid}/originality").json()
    assert r["status"] == "PASS" and "repetition_vs_previous_uploads" in r["report"]
    assert owner_client.get(f"/api/diagnostics/videos/{vid}/qa").status_code == 404  # no QA run yet
    log = owner_client.get("/api/diagnostics/audit?action=diagnostics.viewed").json()
    assert any(e["entity"] and e["entity"].startswith("originality_review") for e in log)


def test_media_streams_to_signed_in_users_with_range_support(owner_client, viewer_client, client):
    vid = produced_video()
    for c in (owner_client, viewer_client):
        r = c.get(f"/api/videos/{vid}/media/final")
        assert r.status_code == 200 and r.headers["content-type"] == "video/mp4" and r.content[4:8] == b"ftyp"
        part = c.get(f"/api/videos/{vid}/media/final", headers={"range": "bytes=0-99"})
        assert part.status_code == 206 and len(part.content) == 100 and part.headers["content-range"].startswith("bytes 0-99/")
        t = c.get(f"/api/videos/{vid}/media/thumbnail")
        assert t.status_code == 200 and t.headers["content-type"] == "image/png" and t.content[:4] == b"\x89PNG"
    anonymous = make_client()
    assert anonymous.get(f"/api/videos/{vid}/media/final").status_code == 401


def test_media_only_serves_the_recorded_files(owner_client):
    vid = produced_video()
    assert owner_client.get(f"/api/videos/{vid}/media/secrets").status_code == 404
    assert owner_client.get(f"/api/videos/{vid}/media/..%2f..%2fetc%2fpasswd").status_code == 404
    assert owner_client.get("/api/videos/999/media/final").status_code == 404
    with session_scope() as s:
        s.get(Video, vid).final_video_path = "../../../../etc/passwd"
    assert owner_client.get(f"/api/videos/{vid}/media/final").status_code == 404  # a tampered path never escapes data/
    with session_scope() as s:
        s.get(Video, vid).final_video_path = None
    assert owner_client.get(f"/api/videos/{vid}/media/final").status_code == 404


def test_unknown_video_is_a_404(owner_client):
    assert owner_client.get("/api/videos/4242").status_code == 404


def test_provider_status_is_honest_and_never_shows_keys(owner_client, viewer_client, monkeypatch):
    from app.config import reset_settings_cache

    assert viewer_client.get("/api/providers").status_code == 403
    st = {p["id"]: p for p in owner_client.get("/api/providers").json()["providers"]}
    assert {"local-procedural", "local-render", "elevenlabs", "anthropic", "youtube", "canva"} <= set(st)
    caps = {c["id"]: c for c in st["elevenlabs"]["capabilities"]}
    assert caps["speech.tts"]["state"] == "not_configured" and caps["speech.tts"]["verified_live"] is False
    assert caps["music.sung"]["state"] == "not_implemented" and "not been verified" in caps["music.sung"]["note"] or "not written" in caps["music.sung"]["note"]
    assert {c["id"]: c["state"] for c in st["local-procedural"]["capabilities"]}["music.instrumental"] == "ready"
    assert st["anthropic"]["capabilities"][0]["state"] == "not_implemented"
    assert st["canva"]["needs_attestation"] is False and st["canva"]["capabilities"][0]["state"] == "ready"  # manual use needs no API terms

    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_" + "z" * 30)
    reset_settings_cache()
    resp = owner_client.get("/api/providers")
    assert "zzzzzzzz" not in resp.text
    caps = {c["id"]: c for c in {p["id"]: p for p in resp.json()["providers"]}["elevenlabs"]["capabilities"]}
    assert caps["speech.tts"]["state"] == "needs_attestation"

    att = {"elevenlabs": {"api_access_confirmed": True, "commercial_use_confirmed": True, "note": "Pro plan, terms read 2026-10"}}
    assert owner_client.put("/api/settings/provider_attestations", json={"value": att}).status_code == 200
    caps = {c["id"]: c for c in {p["id"]: p for p in owner_client.get("/api/providers").json()["providers"]}["elevenlabs"]["capabilities"]}
    assert caps["speech.tts"]["state"] == "ready" and caps["music.sung"]["state"] == "not_implemented"

    owner_client.post("/api/control/pause", json={"reason": "check provider state"})
    caps = {c["id"]: c for c in {p["id"]: p for p in owner_client.get("/api/providers").json()["providers"]}["elevenlabs"]["capabilities"]}
    assert caps["speech.tts"]["state"] == "paused"


def test_attestations_are_validated_and_owner_only(owner_client, viewer_client):
    assert owner_client.put("/api/settings/provider_attestations", json={"value": {"x": {"api_access_confirmed": "yes"}}}).status_code == 422
    assert owner_client.put("/api/settings/provider_attestations", json={"value": {"x": {"surprise": True}}}).status_code == 422
    assert owner_client.put("/api/settings/provider_attestations", json={"value": ["not", "an", "object"]}).status_code == 422
    assert viewer_client.put("/api/settings/provider_attestations", json={"value": {}}).status_code == 403
    ok = owner_client.put("/api/settings/provider_attestations", json={"value": {"elevenlabs": {"api_access_confirmed": True}}})
    assert ok.status_code == 200 and ok.json()["value"]["elevenlabs"]["confirmed_at"]
    cleared = owner_client.put("/api/settings/provider_attestations", json={"value": {}}).json()["value"]
    assert cleared == {}  # revoking an attestation really removes it


def test_a_task_waiting_on_an_unavailable_provider_is_blocked_not_failed(db, agent, owner):
    """The queue parks work that needs a provider the owner has not enabled."""
    from app.providers.base import ProviderUnavailable
    from app.services import tasks
    from app.worker.runner import HandlerResult, Runner

    db.commit()

    def needs_voice(ctx):
        with session_scope() as s:
            from app.providers import base

            base.require_ready(s, "elevenlabs", "speech.tts", category="audio")
        return HandlerResult(result={})

    with session_scope() as s:
        t = tasks.create(s, org.actor_for_slug(s, "ceo"), title="Narrate", task_type="narrate", review_required=False)
        tid = t.id
    Runner("w", {"narrate": needs_voice}).run_once()
    with session_scope(write=False) as s:
        from app.models.tasks import Task

        row = s.get(Task, tid)
        assert row.status == "BLOCKED" and "NOT_CONFIGURED" in row.blocked_reason and row.attempts == 1
    assert ProviderUnavailable("a", "b", "not_configured", "x").code == "NOT_CONFIGURED"
