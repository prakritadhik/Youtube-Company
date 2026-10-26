"""The gateway is the only door to the model: readiness, limits, permissions, checking of answers, and what is recorded."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.ai import gateway, schema
from app.db import session_scope
from app.models.ai import AiGeneration
from app.models.finance import UsageRecord
from app.providers import claude_code as cc
from app.security.permissions import PermissionDenied
from app.services import control
from tests.ai_helpers import connect_brain, say

SCHEMA = {"type": "object", "required": ["title", "n"], "properties": {"title": {"type": "string", "minLength": 3, "maxLength": 20}, "n": {"type": "integer", "minimum": 1, "maximum": 5}}}
ROLE = "creative-director"


def ask(**kw):
    return gateway.ask(role=kw.pop("role", ROLE), purpose=kw.pop("purpose", "brief.draft"), prompt=kw.pop("prompt", "Give a title and a number."), schema=SCHEMA, **kw)


@pytest.fixture
def brain(db, owner, tmp_path, monkeypatch):
    return connect_brain(db, owner, tmp_path, monkeypatch)


def rows():
    with session_scope(write=False) as s:
        return [(g.attempt, g.status, g.error_code) for g in s.scalars(select(AiGeneration).order_by(AiGeneration.id))]


# --- extracting and checking answers ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", ['{"a": 1}', '```json\n{"a": 1}\n```', 'Here you go: {"a": 1} Hope that helps.', '  \n{"a": 1}\n  '])
def test_json_is_found_in_a_fenced_or_chatty_answer(text):
    assert schema.extract_json(text) == {"a": 1}


@pytest.mark.parametrize("text", ["no json at all", "{broken: json}", "", '{"a": 1'])
def test_an_answer_without_usable_json_is_refused(text):
    with pytest.raises(schema.NoJson):
        schema.extract_json(text)


def test_the_schema_check_reports_every_kind_of_problem_with_its_place():
    s = {"type": "object", "required": ["a", "b"], "properties": {"a": {"type": "string", "minLength": 2}, "b": {"type": "array", "minItems": 2, "maxItems": 3, "items": {"type": "integer", "minimum": 0}},
                                                                    "c": {"type": "string", "enum": ["x", "y"]}}}
    assert schema.validate(s, {"a": "ok", "b": [1, 2], "c": "x"}) == []
    got = schema.validate(s, {"a": " ", "b": [1, -1, "z", 4], "c": "q"})
    assert any("$.a" in p for p in got) and any("$.b" in p and "items" in p for p in got) and any("$.b[1]" in p for p in got) and any("$.b[2]" in p for p in got) and any("$.c" in p for p in got)
    assert schema.validate(s, {"a": "ok"}) == ["$.b: is required"]
    assert schema.validate(s, [1]) == ["$: expected object, got list"]
    assert schema.validate({"type": "integer"}, True) == ["$: expected integer, got bool"]  # a boolean is not a number


# --- readiness and limits --------------------------------------------------------------------------------------------------


def test_nothing_is_run_until_the_owner_has_confirmed_the_plan_terms(db, owner, tmp_path, monkeypatch):
    fake = connect_brain(db, owner, tmp_path, monkeypatch, attest=False)
    with pytest.raises(gateway.BrainNotReady) as e:
        ask()
    assert e.value.code == "AI_NEEDS_ATTESTATION" and fake.calls() == []


def test_a_daily_allowance_of_zero_means_not_permitted(db, owner, tmp_path, monkeypatch):
    fake = connect_brain(db, owner, tmp_path, monkeypatch, limit=0)
    with pytest.raises(gateway.BrainNotReady) as e:
        ask()
    assert e.value.code == "AI_LIMIT_ZERO" and "Settings" in e.value.reason and fake.calls() == []


def test_the_daily_allowance_is_enforced_and_counts_every_call_including_corrections(db, owner, tmp_path, monkeypatch):
    fake = connect_brain(db, owner, tmp_path, monkeypatch, limit=2)
    say(fake, "not json", {"title": "Garden", "n": 2}, {"title": "Garden", "n": 2})
    assert ask().data == {"title": "Garden", "n": 2}  # used the whole allowance: one bad answer, one correction
    with pytest.raises(gateway.BrainNotReady) as e:
        ask()
    assert e.value.code == "AI_LIMIT_REACHED" and len(fake.calls()) == 2


def test_the_program_is_missing(db, owner, tmp_path, monkeypatch):
    connect_brain(db, owner, tmp_path, monkeypatch)
    monkeypatch.setenv("YTC_CLAUDE_CODE_BIN", str(tmp_path / "nowhere" / "claude"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    from app.config import reset_settings_cache

    reset_settings_cache()
    with pytest.raises(gateway.BrainNotReady) as e:
        ask()
    assert e.value.code == "AI_MISSING_TOOL"


def test_an_emergency_pause_stops_model_calls(db, owner, tmp_path, monkeypatch):
    fake = connect_brain(db, owner, tmp_path, monkeypatch)
    control.pause(db, owner, "test")
    db.commit()
    with pytest.raises(gateway.BrainNotReady) as e:
        ask()
    assert e.value.code == "AI_PAUSED" and fake.calls() == []


def test_only_roles_with_the_model_tool_may_ask(brain):
    with pytest.raises(PermissionDenied):
        ask(role="video-editing-specialist")
    assert brain.calls() == []


# --- the call, the answer, the record --------------------------------------------------------------------------------------


def test_a_good_answer_is_returned_and_recorded_with_its_prompt_and_usage(brain):
    say(brain, {"title": "Garden", "n": 3})
    a = ask(prompt="Give a title. API key sk-ant-api03-abcdefghijklmnopqrstuvwx should be hidden in the record.", video_id=None)
    assert a.data == {"title": "Garden", "n": 3} and a.attempts == 1 and a.model == "claude-test-1" and len(a.generation_ids) == 1
    with session_scope(write=False) as s:
        g = s.get(AiGeneration, a.generation_ids[0])
        assert (g.status, g.purpose, g.role_slug, g.output) == ("OK", "brief.draft", ROLE, {"title": "Garden", "n": 3})
        assert (g.input_tokens, g.output_tokens, g.list_price_micro_usd) == (12, 34, 12300)
        from app.storage import resolve_data_path

        body = json.loads(resolve_data_path(g.artifact_path).read_text())
        assert "sk-ant-api03" not in json.dumps(body) and "Give a title." in body["prompt"] and body["response"].startswith("{")
        u = s.scalar(select(UsageRecord).where(UsageRecord.provider == "claude-code"))
        assert (u.category, u.cost_kind, u.cost_micro_usd, u.meta["generation_id"]) == ("model", "included_in_subscription", 12300, g.id)


def test_an_unusable_answer_gets_one_correction_that_lists_the_problems_and_both_are_recorded(brain):
    say(brain, {"title": "x", "n": 9}, {"title": "Garden", "n": 2})
    a = ask()
    assert a.data["n"] == 2 and a.attempts == 2
    second = brain.calls()[1]["stdin"]
    assert "$.title: needs at least 3 characters" in second and "$.n: must be at most 5" in second and "Your previous answer" in second
    assert rows() == [(1, "INVALID", None), (2, "OK", None)]


def test_an_answer_that_stays_unusable_is_never_used(brain):
    say(brain, "nonsense", {"title": "x", "n": 0})
    with pytest.raises(gateway.BrainOutputError):
        ask()
    assert rows() == [(1, "INVALID", None), (2, "INVALID", None)]
    with session_scope(write=False) as s:
        assert all(g.output is None for g in s.scalars(select(AiGeneration)))


def test_the_callers_own_rules_apply_after_the_schema(brain):
    say(brain, {"title": "Forbidden", "n": 1}, {"title": "Allowed!", "n": 1})
    a = ask(validators=[lambda d: ["the title may not be 'Forbidden'"] if d["title"] == "Forbidden" else []])
    assert a.data["title"] == "Allowed!" and "the title may not be 'Forbidden'" in brain.calls()[1]["stdin"]


def test_not_signed_in_parks_the_work_and_the_next_calls_wait_instead_of_hammering(brain):
    brain.set(error="result", message="Invalid API key · Please run /login")
    with pytest.raises(gateway.BrainNotReady) as e:
        ask()
    assert e.value.code == "AI_NOT_SIGNED_IN" and rows() == [(1, "FAILED", "NOT_SIGNED_IN")]
    with session_scope(write=False) as s:
        st = gateway.brain_state(s)
    assert not st["ready"] and st["code"].startswith("COOLING_DOWN") and "connection test" in st["detail"]
    with pytest.raises(gateway.BrainNotReady):
        ask()
    assert len(brain.calls()) == 1  # the second ask never started the program


def test_a_later_successful_call_ends_the_wait(brain):
    brain.set(error="result", message="Please run /login")
    with pytest.raises(gateway.BrainNotReady):
        ask()
    with session_scope() as s:  # what the connection test in Settings records when it succeeds
        s.add(AiGeneration(purpose="doctor", role_slug="owner", attempt=1, status="OK", prompt_sha256="0" * 64, prompt_chars=1))
    with session_scope(write=False) as s:
        assert gateway.brain_state(s)["ready"]


def test_a_usage_limit_parks_the_work_with_the_programs_own_words(brain):
    brain.set(error="result", message="Claude usage limit reached. Your limit will reset at 3pm.")
    with pytest.raises(gateway.BrainNotReady) as e:
        ask()
    assert e.value.code == "AI_USAGE_LIMIT" and "reset" in e.value.reason.lower() or "limit" in e.value.reason.lower()


def test_an_unrecognised_error_is_kept_verbatim_and_parks_the_work(brain):
    brain.set(error="result", message="Something entirely new went wrong: code 77")
    with pytest.raises(gateway.BrainNotReady) as e:
        ask()
    assert e.value.code == "AI_UNKNOWN_ERROR" and "code 77" in e.value.reason


def test_a_timeout_is_retryable_not_parked(brain):
    brain.set(reply='{"title": "Slow", "n": 1}', sleep=3)

    def factory(binary, **kw):
        return cc.ClaudeCodeClient(binary, **{**kw, "timeout": 0.5})

    with pytest.raises(cc.Timeout):
        ask(client_factory=factory)
    assert rows() == [(1, "FAILED", "TIMEOUT")]
    with session_scope(write=False) as s:
        assert gateway.brain_state(s)["ready"]  # a timeout is not a reason to stop asking


def test_the_heartbeat_runs_while_the_program_thinks(brain, monkeypatch):
    monkeypatch.setattr(gateway, "HEARTBEAT_EVERY_S", 0.05)
    brain.set(reply='{"title": "Slow", "n": 1}', sleep=0.4)
    beats = []
    ask(beat=lambda: beats.append(1))
    assert len(beats) >= 3
