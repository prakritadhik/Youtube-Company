"""The Claude Code adapter: how the program is run (no tools, empty folder, stdin, no API key) and how every kind of failure is reported."""

from __future__ import annotations

import threading
import time

import pytest

from app.config import reset_settings_cache
from app.providers import claude_code as cc
from app.providers.claude_code import ClaudeCodeClient
from tests.fake_claude import FakeClaude


@pytest.fixture
def fake(tmp_path):
    return FakeClaude(tmp_path / "bin")


def client(fake, **kw):
    return ClaudeCodeClient(str(fake.path), sandbox=fake.dir / "sandbox", **kw)


def test_a_call_returns_the_text_with_usage_and_provenance_fields(fake):
    fake.set(reply="A garden song for Maya.", model="claude-test-1")
    g = client(fake).generate(system="You write birthday songs.", prompt="Write one line.")
    assert g.text == "A garden song for Maya." and g.model == "claude-test-1" and g.session_id == "sess-1"
    assert (g.input_tokens, g.output_tokens, g.cache_read_tokens, g.cache_creation_tokens) == (12, 34, 100, 200)
    assert g.list_price_usd == 0.0123 and g.duration_ms == 1234 and g.num_turns == 1 and g.tools_denied == []


def test_the_program_is_run_without_tools_in_an_empty_folder_with_the_prompt_on_stdin(fake, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-never-reach-the-child")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token-should-never-reach-the-child")
    c = client(fake)
    c.generate(system="SYSTEM TEXT", prompt="THE USER PROMPT with a lyric: don't, \"quote\", & | < > ^ %")
    call = fake.calls()[0]
    a = call["args"]
    assert a[:3] == ["-p", "--output-format", "json"] and a[a.index("--tools") + 1] == "" and "--no-session-persistence" in a and "--safe-mode" in a
    assert a[a.index("--system-prompt") + 1] == "SYSTEM TEXT"
    assert not any("USER PROMPT" in x for x in a) and "USER PROMPT" in call["stdin"]  # nothing about the task is ever on the command line
    assert "--dangerously-skip-permissions" not in a and "--allow-dangerously-skip-permissions" not in a and "--bare" not in a
    assert call["cwd"].endswith("sandbox") and "--add-dir" not in a  # an empty folder of its own, never the project
    assert call["env"]["ANTHROPIC_API_KEY"] is None and call["env"]["ANTHROPIC_AUTH_TOKEN"] is None  # the owner's plan is used, never a stray key


def test_options_the_installed_version_lacks_are_not_passed_and_required_ones_are_checked(fake):
    fake.set(flags=["--print", "--output-format", "--tools", "--system-prompt", "--no-session-persistence"])  # an older version: no safe mode, model or effort
    client(fake, model="sonnet", effort="high").generate(system="s", prompt="p")
    a = fake.calls()[0]["args"]
    assert "--safe-mode" not in a and "--model" not in a and "--effort" not in a
    fake.set(flags=["--print", "--output-format", "--system-prompt"])
    with pytest.raises(cc.UnsupportedVersion) as e:
        client(fake).generate(system="s", prompt="p")
    assert "--tools" in str(e.value) and "claude update" in str(e.value)
    fake.set()
    client(fake, model="sonnet", effort="high").generate(system="s", prompt="p")
    a = fake.calls()[-1]["args"]
    assert a[a.index("--model") + 1] == "sonnet" and a[a.index("--effort") + 1] == "high"


def test_a_missing_program_is_reported_with_what_to_do(tmp_path, monkeypatch):
    with pytest.raises(cc.NotInstalled) as e:
        ClaudeCodeClient(str(tmp_path / "no-such-claude"), sandbox=tmp_path / "sb").generate(system="s", prompt="p")
    assert "Install Claude Code" in str(e.value) or "could not be started" in str(e.value)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.delenv("YTC_CLAUDE_CODE_BIN", raising=False)
    reset_settings_cache()
    nothing = ClaudeCodeClient(None, sandbox=tmp_path / "sb2")
    assert nothing.binary is None
    with pytest.raises(cc.NotInstalled, match="not found"):
        nothing.generate(system="s", prompt="p")


@pytest.mark.parametrize(("message", "kind"), [
    ("Not logged in · Please run /login", cc.NotSignedIn),
    ("Invalid API key · Please run /login", cc.NotSignedIn),
    ("OAuth token has expired", cc.NotSignedIn),
    ("Claude AI usage limit reached|1761500000", cc.UsageLimit),
    ("Rate limit exceeded, try again later", cc.UsageLimit),
    ("API Error: 529 overloaded", cc.UsageLimit),
    ("something nobody has ever seen before", cc.UnknownError),
    ("", cc.UnknownError),
])
def test_known_failures_are_recognised_and_unknown_ones_are_kept_not_guessed(fake, message, kind):
    fake.set(error="result", message=message)
    with pytest.raises(kind) as e:
        client(fake).generate(system="s", prompt="p")
    assert e.value.code == kind.code
    if kind is cc.UnknownError and message:
        assert message in e.value.detail and message in str(e.value)  # shown verbatim so the owner can act on it
    assert cc.NotSignedIn("x").retryable is False and cc.UsageLimit("x").retryable is False and cc.UnknownError("x").retryable is False  # never an automatic retry loop


def test_failures_without_usable_json_are_classified_from_stderr_or_reported_as_bad_output(fake):
    fake.set(error="stderr", message="Error: not logged in")
    with pytest.raises(cc.NotSignedIn):
        client(fake).generate(system="s", prompt="p")
    fake.set(error="garbage")
    with pytest.raises(cc.BadOutput):
        client(fake).generate(system="s", prompt="p")
    fake.set(reply="__empty__")
    with pytest.raises(cc.BadOutput, match="empty"):
        client(fake).generate(system="s", prompt="p")
    fake.set(reply="x" * 500)
    with pytest.raises(cc.BadOutput, match="over the"):
        client(fake, max_chars=100).generate(system="s", prompt="p")


def test_a_call_that_takes_too_long_is_stopped(fake):
    fake.set(sleep=5)
    t0 = time.time()
    with pytest.raises(cc.Timeout) as e:
        client(fake, timeout=1).generate(system="s", prompt="p")
    assert time.time() - t0 < 4 and e.value.retryable is True


def test_calls_run_one_at_a_time_so_the_plans_limit_is_not_burst(fake):
    fake.set(sleep=0.6)
    stamps = []

    def go():
        client(fake).generate(system="s", prompt="p")
        stamps.append(time.time())
    t0 = time.time()
    threads = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert max(stamps) - t0 >= 1.1  # two 0.6 s calls could not have overlapped


# --- the self-test ------------------------------------------------------------------------------------------------------------


def test_the_doctor_runs_the_program_once_and_confirms_it_has_no_tools(fake):
    fake.set(reply="NONE", version="2.9.9 (Claude Code)")
    d = client(fake).doctor()
    assert d["ok"] is True and d["installed"] and d["supported"] and d["signed_in"] and d["tool_free"] and d["version"] == "2.9.9 (Claude Code)" and d["model"] == "claude-test-1"
    assert d["options"]["--tools"] is True and d["problems"] == [] and len(fake.calls()) == 1


def test_the_doctor_refuses_a_brain_that_claims_to_have_tools(fake):
    fake.set(reply="Bash, Read, Write")
    d = client(fake).doctor()
    assert d["ok"] is False and d["tool_free"] is False and "will not use it" in d["problems"][0]
    fake.set(reply="NONE", denials=[{"tool_name": "Bash"}])
    assert client(fake).doctor()["tool_free"] is False  # a tool attempt that was denied is still a reason not to trust the setup


def test_the_doctor_reports_each_way_it_can_fail(fake, tmp_path):
    fake.set(error="result", message="Not logged in · Please run /login")
    d = client(fake).doctor()
    assert d["ok"] is False and d["signed_in"] is False and "run `claude`" in d["problems"][0]
    fake.set(flags=["--print"])
    d = client(fake).doctor()
    assert d["ok"] is False and d["supported"] is False and "claude update" in d["problems"][0] and len(fake.calls()) == 1  # no model call was made
    d = ClaudeCodeClient(str(tmp_path / "missing"), sandbox=tmp_path / "s").doctor()
    assert d["ok"] is False and d["installed"] is False and d["version"] is None and "could not be started" in d["problems"][0]


def test_locating_the_program_uses_the_configured_path_then_the_path_variable(fake, monkeypatch, tmp_path):
    monkeypatch.setenv("YTC_CLAUDE_CODE_BIN", str(fake.path))
    reset_settings_cache()
    assert cc.locate() == str(fake.path)
    monkeypatch.setenv("YTC_CLAUDE_CODE_BIN", "  ")
    reset_settings_cache()
    monkeypatch.setenv("PATH", str(fake.dir))
    assert cc.locate() == str(fake.path)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert cc.locate() is None
    reset_settings_cache()
