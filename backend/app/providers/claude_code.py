"""The company's brain: the Claude Code program installed on the owner's computer, signed in with the owner's own Claude account.

Nothing here talks to Anthropic directly. It runs the ``claude`` program that is already on this computer, in its non-interactive mode, and reads what it
prints. That is what lets the AI work on the owner's PC with the owner's subscription and **no API key**.

How a call is made, and why:

* **No tools.** ``--tools ""`` leaves the model with nothing to call: no shell, no file access, no web. It can only write text. A comment, a web page or
  a source that tries to give it orders therefore cannot make anything happen; it can only influence a draft, and every draft still goes through the
  same checks, reviews and approvals as a human's would.
* **An empty working folder** outside the repository, so no project files or instruction files are read, and ``--safe-mode`` (when this version of the program
  has it) switches off hooks, plug-ins and other customisations the owner may have installed for their own work.
* **The prompt goes in on standard input**, never on the command line (command lines have length limits, especially on Windows) and nothing the model or an
  outside text could say reaches the command line.
* **No stray billing.** Variables that would make the program use an API key (``ANTHROPIC_API_KEY`` and friends) or a cloud provider are removed from the
  child's environment, so it uses the owner's signed-in account and a key set for some other purpose cannot create charges.
* **One call at a time, with a timeout**; the output is capped and must be a result the program reports as successful.

The error messages the program prints when it is not signed in or the plan's usage limit is reached could not be reproduced where this was written, so they are
recognised by wording that is documented elsewhere and **anything unrecognised is reported verbatim (shortened) as an unknown error and parks the work for the
owner**: a wrong guess can never turn into a silent retry loop.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import get_settings

SANDBOX = Path(tempfile.gettempdir()) / "ytc-ai-sandbox"
MAX_OUTPUT_CHARS = 60_000
DEFAULT_TIMEOUT = 300.0
# These make the program bill an API key or a cloud account instead of the owner's signed-in plan, so they are never passed on.
SCRUBBED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
                "AWS_BEARER_TOKEN_BEDROCK", "ANTHROPIC_BEDROCK_BASE_URL", "ANTHROPIC_VERTEX_PROJECT_ID")
REQUIRED_FLAGS = ("--print", "--output-format", "--tools", "--system-prompt", "--no-session-persistence")
OPTIONAL_FLAGS = ("--safe-mode", "--model", "--effort")

_serial = threading.Semaphore(1)  # one call at a time: the plan's usage limit is shared and bursts waste it


class ClaudeCodeError(Exception):
    """A call did not produce a usable result. ``code`` is stable; ``message`` is written for the owner."""

    code = "CLAUDE_ERROR"
    retryable = False

    def __init__(self, message: str, *, detail: str = ""):
        super().__init__(message)
        self.message, self.detail = message, detail


class NotInstalled(ClaudeCodeError):
    code = "CLAUDE_NOT_INSTALLED"


class UnsupportedVersion(ClaudeCodeError):
    code = "CLAUDE_UNSUPPORTED_VERSION"


class NotSignedIn(ClaudeCodeError):
    code = "CLAUDE_NOT_SIGNED_IN"


class UsageLimit(ClaudeCodeError):
    code = "CLAUDE_USAGE_LIMIT"


class Timeout(ClaudeCodeError):
    code = "CLAUDE_TIMEOUT"
    retryable = True


class BadOutput(ClaudeCodeError):
    code = "CLAUDE_BAD_OUTPUT"
    retryable = True


class UnknownError(ClaudeCodeError):
    code = "CLAUDE_UNKNOWN_ERROR"


_NOT_SIGNED_IN = re.compile(r"not logged in|please run /?login|/login|invalid api key|invalid x-api-key|authentication[_ ]error|unauthori[sz]ed|oauth token|credit balance|sign in", re.I)
_LIMIT = re.compile(r"usage limit|limit reached|rate[ _-]?limit|too many requests|quota|overloaded|capacity|try again later|resets? (at|in)", re.I)


def classify(message: str) -> ClaudeCodeError:
    """Turn the program's own error text into a typed error. Unrecognised text is kept (shortened) rather than guessed at."""
    text = " ".join((message or "").split())[:500] or "the program reported an error without a message"
    if _NOT_SIGNED_IN.search(text):
        return NotSignedIn("Claude Code is not signed in on this computer. Open a terminal, run `claude`, sign in with your Claude account, then try again.", detail=text)
    if _LIMIT.search(text):
        return UsageLimit("Your Claude plan's usage limit has been reached for now. The work keeps its place and can continue when the limit resets.", detail=text)
    return UnknownError(f"Claude Code reported an error: {text}", detail=text)


def locate() -> str | None:
    """The ``claude`` program to run: the configured path, else the first one on the PATH (this also finds ``claude.cmd`` on Windows)."""
    configured = get_settings().claude_code_bin
    if configured:
        p = Path(configured).expanduser()
        return str(p) if p.is_file() else (shutil.which(configured) or None)
    return shutil.which("claude")


@dataclass
class Generation:
    text: str
    session_id: str | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    list_price_usd: float | None = None  # what the program prices the call at list rates: NOT what is billed to a subscription
    duration_ms: int | None = None
    num_turns: int = 1
    tools_denied: list[Any] = field(default_factory=list)


def _int(v: Any) -> int:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


class ClaudeCodeClient:
    def __init__(self, binary: str | None = None, *, model: str | None = None, effort: str | None = None, timeout: float = DEFAULT_TIMEOUT,
                 sandbox: Path | None = None, max_chars: int = MAX_OUTPUT_CHARS, env: dict[str, str] | None = None):
        self.binary = binary or locate()
        self.model, self.effort, self.timeout, self.max_chars = model or None, effort or None, timeout, max_chars
        self.sandbox = sandbox or SANDBOX
        self._env = env
        self._flags: set[str] | None = None

    # --- environment ---------------------------------------------------------------------------------------------------------

    def _child_env(self) -> dict[str, str]:
        base = dict(self._env if self._env is not None else os.environ)
        for k in SCRUBBED_ENV:
            base.pop(k, None)
        return base

    def _require_binary(self) -> str:
        if not self.binary:
            raise NotInstalled("The `claude` program was not found. Install Claude Code (https://claude.com/claude-code), sign in once, and restart; "
                               "or set YTC_CLAUDE_CODE_BIN in .env to its full path.")
        return self.binary

    def _run(self, args: list[str], *, stdin: str | None = None, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        self.sandbox.mkdir(parents=True, exist_ok=True)
        try:
            return subprocess.run([self._require_binary(), *args], input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=timeout or self.timeout, cwd=str(self.sandbox), env=self._child_env(), check=False)
        except FileNotFoundError as e:
            raise NotInstalled(f"The `claude` program could not be started ({e}). Check YTC_CLAUDE_CODE_BIN or reinstall Claude Code.") from e
        except subprocess.TimeoutExpired as e:
            raise Timeout(f"Claude Code did not answer within {int(timeout or self.timeout)} seconds.", detail=str(e)) from e
        except OSError as e:
            raise UnknownError(f"The `claude` program could not be run: {e}") from e

    def flags(self) -> set[str]:
        """The options this version of the program lists in its own help, so only supported ones are used."""
        if self._flags is None:
            out = self._run(["--help"], timeout=60)
            text = (out.stdout or "") + (out.stderr or "")
            known = (*REQUIRED_FLAGS, *OPTIONAL_FLAGS)
            self._flags = {f for f in known if re.search(rf"(?<![\w-]){re.escape(f)}(?![\w-])", text)}
            if "-p," in text or re.search(r"(?m)^\s*-p\b", text):
                self._flags.add("--print")
        return self._flags

    def version(self) -> str | None:
        out = self._run(["--version"], timeout=30)
        return (out.stdout or "").strip().splitlines()[0] if (out.stdout or "").strip() else None

    def _command(self, system: str) -> list[str]:
        flags = self.flags()
        missing = [f for f in REQUIRED_FLAGS if f not in flags]
        if missing:
            raise UnsupportedVersion(f"This version of Claude Code lacks options this app needs ({', '.join(missing)}). Update it (`claude update`) and try again.")
        cmd = ["-p", "--output-format", "json", "--tools", "", "--no-session-persistence", "--system-prompt", system]
        if "--safe-mode" in flags:
            cmd.append("--safe-mode")
        if self.model and "--model" in flags:
            cmd += ["--model", self.model]
        if self.effort and "--effort" in flags:
            cmd += ["--effort", self.effort]
        return cmd

    # --- the call --------------------------------------------------------------------------------------------------------------

    def generate(self, *, system: str, prompt: str, timeout: float | None = None) -> Generation:
        cmd = self._command(system)
        with _serial:
            out = self._run(cmd, stdin=prompt, timeout=timeout)
        return self._parse(out)

    def _parse(self, out: subprocess.CompletedProcess[str]) -> Generation:
        raw = (out.stdout or "").strip()
        try:
            d = json.loads(raw) if raw else None
        except ValueError:
            d = None
        if not isinstance(d, dict):
            if out.returncode != 0:
                raise classify(out.stderr or raw)
            raise BadOutput("Claude Code did not return a result in the expected format.", detail=(raw or out.stderr or "")[:300])
        if d.get("is_error") or d.get("subtype", "success") != "success" or out.returncode != 0:
            raise classify(str(d.get("result") or d.get("error") or out.stderr or d.get("subtype") or ""))
        text = d.get("result")
        if not isinstance(text, str) or not text.strip():
            raise BadOutput("Claude Code returned an empty answer.")
        if len(text) > self.max_chars:
            raise BadOutput(f"The answer was {len(text):,} characters, over the {self.max_chars:,} allowed.")
        u = d.get("usage") or {}
        models = list((d.get("modelUsage") or {}).keys())
        return Generation(text=text.strip(), session_id=d.get("session_id"), model=", ".join(models) or None, input_tokens=_int(u.get("input_tokens")),
                          output_tokens=_int(u.get("output_tokens")), cache_read_tokens=_int(u.get("cache_read_input_tokens")),
                          cache_creation_tokens=_int(u.get("cache_creation_input_tokens")),
                          list_price_usd=d.get("total_cost_usd") if isinstance(d.get("total_cost_usd"), (int, float)) else None,
                          duration_ms=_int(d.get("duration_ms")) or None, num_turns=_int(d.get("num_turns")) or 1, tools_denied=list(d.get("permission_denials") or []))

    # --- self-test ---------------------------------------------------------------------------------------------------------------

    def doctor(self) -> dict[str, Any]:
        """Everything the owner needs to know about whether the brain works, found by actually running it once."""
        report: dict[str, Any] = {"installed": False, "path": self.binary, "version": None, "supported": False, "options": {}, "signed_in": None, "tool_free": None,
                                  "reply": None, "model": None, "duration_ms": None, "problems": [],
                                  "api_key_in_environment_ignored": any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"))}
        try:
            self._require_binary()
            report["version"] = self.version()
            report["installed"] = True  # only once the program has actually started
            flags = self.flags()
            report["options"] = {f: (f in flags) for f in (*REQUIRED_FLAGS, *OPTIONAL_FLAGS)}
            report["supported"] = all(f in flags for f in REQUIRED_FLAGS)
            if not report["supported"]:
                report["problems"].append("This version of Claude Code lacks options the app needs; run `claude update`.")
                report["ok"] = False
                return report
            g = self.generate(system="You are a careful writing assistant. You have no tools.", timeout=120,
                              prompt="List every tool you can call, as a comma-separated list. If you have none, reply with exactly one word: NONE")
            report.update(signed_in=True, reply=g.text[:200], model=g.model, duration_ms=g.duration_ms)
            report["tool_free"] = g.text.strip().upper().rstrip(".") == "NONE" and not g.tools_denied and g.num_turns == 1
            if not report["tool_free"]:
                report["problems"].append("Claude Code reported having tools although none were allowed, so the company will not use it as a brain until this is resolved.")
        except NotSignedIn as e:
            report.update(signed_in=False)
            report["problems"].append(e.message)
        except NotInstalled as e:
            report["installed"] = False
            report["problems"].append(e.message)
        except ClaudeCodeError as e:
            report["problems"].append(e.message)
        report["ok"] = bool(report["installed"] and report["supported"] and report["signed_in"] and report["tool_free"])
        return report


Factory = Callable[..., ClaudeCodeClient]
