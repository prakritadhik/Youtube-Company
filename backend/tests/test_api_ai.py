"""The AI brain through the API: status, the connection test, the activity record, asking for ideas, starting a video with the company."""

from __future__ import annotations

import time

from sqlalchemy import select

from app.ai import autopilot
from app.config import reset_settings_cache
from app.db import session_scope
from app.models.research import ContentIdea
from app.models.tasks import Task
from app.worker.runner import HANDLERS, Runner
from tests.ai_helpers import brief_json, say
from tests.api_fixtures import api_env, client, fast_hashing, make_client, owner_client, viewer_client  # noqa: F401
from tests.fake_claude import FakeClaude
from tests.prod_helpers import BDAY
from tests.test_api_research import IDEA


def connect(owner_client, tmp_path, monkeypatch, *, attest=True, limit=50) -> FakeClaude:
    fake = FakeClaude(tmp_path / "bin")
    monkeypatch.setenv("YTC_CLAUDE_CODE_BIN", str(fake.path))
    reset_settings_cache()
    if attest:
        assert owner_client.put("/api/settings/provider_attestations", json={"value": {"claude-code": {"api_access_confirmed": True, "commercial_use_confirmed": True}}}).status_code == 200
    assert owner_client.put("/api/settings/budget", json={"value": {"daily_model_requests": limit, "daily_provider_requests": limit}}).status_code == 200
    return fake


def wait_for_test(c, seconds=30):
    for _ in range(seconds * 10):
        t = c.get("/api/ai/status").json()["test"]
        if t["state"] == "done":
            return t
        time.sleep(0.1)
    raise AssertionError("the connection test did not finish")


def test_everything_requires_signing_in(client):
    for method, url in (("GET", "/api/ai/status"), ("POST", "/api/ai/test"), ("GET", "/api/ai/activity"), ("GET", "/api/ai/generations/1"), ("POST", "/api/ai/ideas"),
                        ("POST", "/api/ideas/1/start-with-ai"), ("GET", "/api/videos/1/progress")):
        assert client.request(method, url, json={}).status_code == 401, (method, url)


def test_viewers_can_read_the_activity_but_not_the_settings_and_cannot_test_ask_or_start(owner_client, viewer_client):
    assert viewer_client.get("/api/ai/status").status_code == 403 and viewer_client.get("/api/ai/activity").status_code == 200
    assert viewer_client.get("/api/ai/generations/1").status_code == 403  # the full prompt and answer are diagnostics
    for url in ("/api/ai/test", "/api/ai/ideas", "/api/ideas/1/start-with-ai"):
        assert viewer_client.post(url, json={}).status_code == 403, url


def test_status_says_exactly_what_is_missing_and_never_calls_the_program(owner_client, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.delenv("YTC_CLAUDE_CODE_BIN", raising=False)
    reset_settings_cache()
    s = owner_client.get("/api/ai/status").json()
    assert s["brain"]["ready"] is False and s["brain"]["code"] == "MISSING_TOOL" and s["program"]["found"] is None and "install Claude Code" in s["brain"]["detail"]
    fake = connect(owner_client, tmp_path, monkeypatch, attest=False)
    s = owner_client.get("/api/ai/status").json()
    assert s["brain"]["code"] == "NEEDS_ATTESTATION" and s["attested"] is False
    owner_client.put("/api/settings/provider_attestations", json={"value": {"claude-code": {"api_access_confirmed": True}}})
    owner_client.put("/api/settings/budget", json={"value": {"daily_model_requests": 0}})
    s = owner_client.get("/api/ai/status").json()
    assert s["brain"]["code"] == "LIMIT_ZERO" and s["allowance"]["daily_model_requests"] == 0
    owner_client.put("/api/settings/budget", json={"value": {"daily_model_requests": 5, "daily_provider_requests": 5}})
    s = owner_client.get("/api/ai/status").json()
    assert s["brain"]["ready"] is True and s["allowance"] == {"daily_model_requests": 5, "daily_provider_requests": 5, "used_today": 0}
    assert fake.calls() == []  # looking never uses the plan


def test_the_connection_test_runs_in_the_background_and_reports_what_it_found(owner_client, tmp_path, monkeypatch):
    fake = connect(owner_client, tmp_path, monkeypatch)
    fake.set(reply="NONE")
    assert owner_client.post("/api/ai/test").json() == {"state": "running"}
    t = wait_for_test(owner_client)
    r = t["report"]
    assert r["ok"] is True and r["signed_in"] is True and r["tool_free"] is True and r["installed"] is True
    st = owner_client.get("/api/ai/status").json()
    assert st["last_test"]["status"] == "OK" and st["last_test"]["report"]["ok"] is True and st["brain"]["ready"] is True


def test_a_failed_connection_test_explains_why_and_holds_the_company_back_until_it_passes(owner_client, tmp_path, monkeypatch):
    fake = connect(owner_client, tmp_path, monkeypatch)
    fake.set(error="result", message="Invalid API key · Please run /login")
    owner_client.post("/api/ai/test")
    t = wait_for_test(owner_client)
    assert t["report"]["ok"] is False and t["report"]["signed_in"] is False and any("not signed in" in p.lower() for p in t["report"]["problems"])
    st = owner_client.get("/api/ai/status").json()
    assert st["last_test"]["status"] == "FAILED" and st["last_test"]["error_code"] == "NOT_SIGNED_IN"
    assert st["brain"]["ready"] is False and st["brain"]["code"].startswith("COOLING_DOWN")
    fake.set(reply="NONE")  # signed in again; the next test passes and ends the wait
    owner_client.post("/api/ai/test")
    wait_for_test(owner_client)
    assert owner_client.get("/api/ai/status").json()["brain"]["ready"] is True


def test_the_connection_test_is_the_owners_alone(owner_client, viewer_client, tmp_path, monkeypatch):
    fake = connect(owner_client, tmp_path, monkeypatch)
    fake.set(reply="NONE")
    assert viewer_client.post("/api/ai/test").status_code == 403 and fake.calls() == []
    assert owner_client.post("/api/ai/test").status_code == 200
    wait_for_test(owner_client)  # (the test thread must finish before the temporary database is removed)


def test_asking_for_ideas_needs_a_ready_brain_and_queues_one_task(owner_client, tmp_path, monkeypatch):
    r = owner_client.post("/api/ai/ideas", json={"channel": BDAY, "count": 2})
    assert r.status_code == 409 and "not ready" in r.json()["detail"]
    fake = connect(owner_client, tmp_path, monkeypatch)
    a = owner_client.post("/api/ai/ideas", json={"channel": BDAY, "count": 2})
    assert a.status_code == 200
    assert owner_client.post("/api/ai/ideas", json={"channel": BDAY, "count": 2}).json()["task_id"] == a.json()["task_id"]  # a double click is one request
    assert owner_client.post("/api/ai/ideas", json={"channel": "no-such-channel", "count": 2}).status_code >= 400
    assert owner_client.post("/api/ai/ideas", json={"channel": BDAY, "count": 9}).status_code == 422
    say(fake, {"ideas": [{"title": "Lantern River Birthday", "concept": "A song that follows paper lanterns down a quiet river on the night of a birthday.",
                          "audience": "Parents and children aged 3 to 8", "rationale": "Gentle and easy to sing."}]})
    assert Runner("w", HANDLERS).run_once() == a.json()["task_id"]
    with session_scope(write=False) as s:
        assert [i.status for i in s.scalars(select(ContentIdea))] == ["ASSESSED"]
    plan = owner_client.get("/api/plan").json()
    assert plan["channels"][0]["items"][0]["title"] == "Lantern River Birthday"


def test_starting_a_video_with_the_company_and_following_its_progress(owner_client, tmp_path, monkeypatch):
    fake = connect(owner_client, tmp_path, monkeypatch)
    idea = owner_client.post("/api/ideas", json={**IDEA, "requires": ["instrumental_music", "illustrated_scenes", "video_render", "llm_drafting"]}).json()
    assert owner_client.post(f"/api/ideas/{idea['id']}/decision", json={"approve": True, "note": "Fits."}).status_code == 200
    r = owner_client.post(f"/api/ideas/{idea['id']}/start-with-ai")
    assert r.status_code == 200, r.text
    vid = r.json()["video_id"]
    p = owner_client.get(f"/api/videos/{vid}/progress").json()
    assert p["conducted"] is True and p["who"] == "company" and p["step"] == "working"
    assert owner_client.get(f"/api/videos/{vid}").json()["progress"]["step"] == "working"
    assert owner_client.post(f"/api/ideas/{idea['id']}/start-with-ai").status_code == 409  # already started
    say(fake, brief_json())
    assert Runner("w", HANDLERS).run_once() is not None
    summary = autopilot.step("w")  # (the review call has no queued answer: the fake repeats the brief, which is not a decision)
    assert summary["reviews"][0]["result"] in ("unusable", "failed")
    acts = owner_client.get("/api/ai/activity").json()
    assert [c["purpose"] for c in acts["calls"]][-1] == "brief.draft" and acts["today"].get("OK") == 1
    first = acts["calls"][-1]["id"]
    full = owner_client.get(f"/api/ai/generations/{first}").json()
    assert full["record"]["prompt"].startswith("You are the Creative Director") and "Original English birthday songs" in full["record"]["prompt"]
    assert owner_client.get("/api/ai/generations/9999").status_code == 404
    with session_scope(write=False) as s:
        assert s.scalar(select(Task).where(Task.task_type == "ai.draft_brief")).status == "SUBMITTED"
