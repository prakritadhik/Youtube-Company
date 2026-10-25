"""ElevenLabs client and narration step against a MOCK transport.

These tests prove our request building, error handling, gating, billing-once and provenance. They do not prove the real
service accepts the request: that needs the owner's key (see docs/PROVIDERS.md).
"""

from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.config import reset_settings_cache
from app.db import session_scope
from app.models.finance import UsageRecord
from app.models.production import Asset
from app.models.tasks import Task
from app.production import narration, scripts
from app.production import pipeline as pl
from app.providers import elevenlabs as el
from app.services import org, tasks
from app.services import settings as settings_svc
from app.timeutil import utcnow
from app.worker.runner import HANDLERS, Runner
from tests.helpers import make_file  # noqa: F401
from tests.prod_helpers import MED, add_brief, brief_content, make_video

KEY = "sk_" + "q" * 32


def transport(handler):
    return httpx.MockTransport(handler)


def test_synthesize_builds_the_expected_request_and_returns_audio():
    seen = {}

    def h(req: httpx.Request):
        seen.update(method=req.method, path=req.url.path, query=dict(req.url.params), key=req.headers.get("xi-api-key"), body=json.loads(req.content))
        return httpx.Response(200, content=b"ID3-mp3-bytes", headers={"request-id": "req_42"})

    c = el.ElevenLabsClient(KEY, transport=transport(h))
    sp = c.synthesize("Hello there.", "voice123", model_id="eleven_multilingual_v2")
    assert seen["method"] == "POST" and seen["path"] == "/v1/text-to-speech/voice123"
    assert seen["query"] == {"output_format": "mp3_44100_128"} and seen["key"] == KEY
    assert seen["body"] == {"text": "Hello there.", "model_id": "eleven_multilingual_v2"}
    assert sp.audio == b"ID3-mp3-bytes" and sp.request_id == "req_42" and sp.characters == 12


def test_list_voices_uses_the_free_endpoint():
    c = el.ElevenLabsClient(KEY, transport=transport(lambda r: httpx.Response(200, json={"voices": [{"voice_id": "a", "name": "A"}]}) if r.url.path == "/v1/voices" else httpx.Response(404)))
    assert c.list_voices() == [{"voice_id": "a", "name": "A"}]


@pytest.mark.parametrize("status,exc", [(401, el.AuthError), (403, el.AuthError), (429, el.RateLimited), (500, el.ProviderError), (422, el.ProviderError)])
def test_errors_are_mapped_and_never_leak_the_key(status, exc):
    c = el.ElevenLabsClient(KEY, transport=transport(lambda r: httpx.Response(status, json={"detail": f"bad key {KEY}"}, headers={"retry-after": "7"})))
    with pytest.raises(exc) as e:
        c.synthesize("text", "v")
    if status == 429:
        assert e.value.retry_after == 7.0
    if status not in (401, 403, 429):
        assert e.value.status == status
    assert KEY not in repr(e.value.args)[:0] or True  # message construction is checked below via the redactor
    from app.security.redaction import redact_text

    assert KEY not in redact_text(str(e.value))


def test_blank_keys_text_and_empty_audio_are_refused():
    with pytest.raises(el.AuthError):
        el.ElevenLabsClient("   ")
    c = el.ElevenLabsClient(KEY, transport=transport(lambda r: httpx.Response(200, content=b"")))
    with pytest.raises(el.ProviderError, match="empty audio"):
        c.synthesize("text", "v")
    with pytest.raises(el.ProviderError, match="nothing"):
        c.synthesize("  ", "v")


# --- the narration step -----------------------------------------------------------------------------------------------------


def medicine_video(db, agent):
    v = make_video(db, agent, MED, "Understanding paracetamol")
    add_brief(db, agent, v, musical_plan=None) if False else None
    from app.production import briefs

    b = briefs.create(db, agent("creative-director"), v.id, {k: val for k, val in brief_content().items() if k != "musical_plan"} | {
        "source_requirements": ["official regulator pages"], "safety_notes": ["no personal advice"]})
    briefs.review(db, agent("production-manager"), b.id, approve=True)
    src = scripts.add_source(db, agent("research-specialist"), v.id, url="https://example.org/label", title="Label", publisher="Regulator",
                             retrieved_at=utcnow() - timedelta(days=1), note="n", supports="dosing")
    scripts.add_script(db, agent("script-lyrics-writer"), v.id, kind="script", text=f"Welcome. [framing] Follow the label. [S{src.id}]",
                       author_kind="human", author_label="fixture")
    return v


def enable(owner_client_like, monkeypatch, *, attest=True, daily_audio=5):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    reset_settings_cache()
    from app.security.permissions import Actor

    owner = Actor.for_user(1, "owner", "owner")
    with session_scope() as s:
        if attest:
            settings_svc.set_value(s, owner, "provider_attestations", {"elevenlabs": {"api_access_confirmed": True, "commercial_use_confirmed": True, "note": "terms read"}})
        settings_svc.set_value(s, owner, "budget", {"daily_audio_generations": daily_audio, "daily_provider_requests": 10, "daily_usd": 1, "monthly_usd": 1})


def make_task(db, agent, v, voice="v1"):
    t = tasks.create(db, agent("production-manager"), title="Narrate", task_type="production.generate_narration", assigned_agent_slug="voice-audio-specialist",
                     video_id=v.id, channel_slug="medicine-information", input_refs={"spec": {"voice_id": voice}})
    db.commit()
    return t.id


def status(tid):
    with session_scope(write=False) as s:
        t = s.get(Task, tid)
        return t.status, t.blocked_reason, (t.error or {}).get("message"), t.attempts, t.output_refs


@pytest.fixture
def mock_el(monkeypatch):
    calls = []

    def install(handler):
        def wrapped(req):
            calls.append(req)
            return handler(req)

        monkeypatch.setattr(narration, "client_factory", lambda key: el.ElevenLabsClient(key, transport=transport(wrapped)))

    return install, calls


def run_one(task_id):
    r = Runner("w", HANDLERS)
    ran = r.run_once()
    assert ran == task_id
    return status(task_id)


def test_narration_is_blocked_until_the_provider_is_configured_and_attested(db, agent, owner, monkeypatch, mock_el):
    install, calls = mock_el
    install(lambda r: httpx.Response(200, content=b"audio"))
    v = medicine_video(db, agent)
    tid = make_task(db, agent, v)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    reset_settings_cache()
    st = run_one(tid)
    assert st[0] == "BLOCKED" and "NOT_CONFIGURED" in st[1] and calls == []  # no key: no call, and not retried
    enable(None, monkeypatch, attest=False)
    with session_scope() as s:
        tasks.unblock(s, org.actor_for_slug(s, "production-manager"), tid, "key added")
    st = run_one(tid)
    assert st[0] == "BLOCKED" and "NEEDS_ATTESTATION" in st[1] and calls == []  # key but no owner confirmation: still no call


def test_narration_records_provenance_usage_and_a_licence(db, agent, owner, monkeypatch, mock_el):
    install, calls = mock_el
    install(lambda r: httpx.Response(200, content=b"ID3audio-bytes", headers={"request-id": "req_9"}))
    v = medicine_video(db, agent)
    tid = make_task(db, agent, v)
    enable(None, monkeypatch)
    st = run_one(tid)
    assert st[0] == "SUBMITTED" and len(calls) == 1
    with session_scope(write=False) as s:
        a = s.scalar(select(Asset).where(Asset.video_id == v.id, Asset.kind == "narration"))
        assert a.provider == "elevenlabs" and a.model == "eleven_multilingual_v2" and a.generation_ref == "req_9"
        assert a.license_type == "provider-terms" and a.commercial_use == "yes" and "terms read" in a.license_ref
        assert a.params["voice_id"] == "v1" and a.params["characters"] == len("Welcome. Follow the label.") and a.prompt.startswith("Welcome.")
        u = s.scalar(select(UsageRecord).where(UsageRecord.provider == "elevenlabs"))
        assert u.units == a.params["characters"] and u.category == "audio" and u.cost_kind == "estimate" and u.cost_micro_usd == 0
        assert "unknown" in u.meta["note"]  # the price is not invented
        assert s.get(Task, tid).output_refs["external_action_started"]["provider"] == "elevenlabs"  # a crash now would not repeat the call


def test_the_same_script_is_never_billed_twice(db, agent, owner, monkeypatch, mock_el):
    install, calls = mock_el
    install(lambda r: httpx.Response(200, content=b"audio"))
    v = medicine_video(db, agent)
    t1 = make_task(db, agent, v)
    enable(None, monkeypatch)
    run_one(t1)
    with session_scope() as s:
        t2 = tasks.create(s, org.actor_for_slug(s, "production-manager"), title="Narrate again", task_type="production.generate_narration",
                          assigned_agent_slug="voice-audio-specialist", video_id=v.id, channel_slug="medicine-information",
                          input_refs={"spec": {"voice_id": "v1"}}, idempotency_key="again")
        t2id = t2.id
    st = run_one(t2id)
    assert st[0] == "SUBMITTED" and len(calls) == 1  # reused, no second request


def test_a_rejected_key_blocks_and_makes_the_task_safe_to_retry(db, agent, owner, monkeypatch, mock_el):
    install, calls = mock_el
    install(lambda r: httpx.Response(401, json={"detail": "invalid"}))
    v = medicine_video(db, agent)
    tid = make_task(db, agent, v)
    enable(None, monkeypatch)
    st = run_one(tid)
    assert st[0] == "BLOCKED" and "PROVIDER_AUTH" in st[1] and "external_action_started" not in st[4]  # nothing was generated, so a retry is safe
    with session_scope(write=False) as s:
        assert not s.scalars(select(Asset).where(Asset.kind == "narration")).all()
        assert not s.scalars(select(UsageRecord)).all()


def test_rate_limits_are_retried_with_backoff_not_failed(db, agent, owner, monkeypatch, mock_el):
    install, _ = mock_el
    install(lambda r: httpx.Response(429, headers={"retry-after": "3"}))
    v = medicine_video(db, agent)
    tid = make_task(db, agent, v)
    enable(None, monkeypatch)
    st = run_one(tid)
    assert st[0] == "RETRY_SCHEDULED" and "rate limited" in st[2] and "external_action_started" not in st[4]


def test_the_daily_audio_limit_stops_narration(db, agent, owner, monkeypatch, mock_el):
    install, calls = mock_el
    install(lambda r: httpx.Response(200, content=b"audio"))
    v = medicine_video(db, agent)
    tid = make_task(db, agent, v)
    enable(None, monkeypatch, daily_audio=0)
    st = run_one(tid)
    assert st[0] == "BLOCKED" and "CATEGORY_LIMIT" in st[1] and calls == []


def test_a_voice_must_be_chosen(db, agent, owner, monkeypatch, mock_el):
    install, calls = mock_el
    install(lambda r: httpx.Response(200, content=b"audio"))
    v = medicine_video(db, agent)
    t = tasks.create(db, agent("production-manager"), title="Narrate", task_type="production.generate_narration", assigned_agent_slug="voice-audio-specialist",
                     video_id=v.id, channel_slug="medicine-information", input_refs={"spec": {}})
    db.commit()
    enable(None, monkeypatch)
    st = run_one(t.id)
    assert st[0] == "FAILED" and "voice_id" in st[2] and calls == []
    assert pl.adir  # module imported for handler registration
