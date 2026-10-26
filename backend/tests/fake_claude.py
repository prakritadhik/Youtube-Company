"""A stand-in for the ``claude`` program, as a real executable, so the adapter's subprocess handling is tested for real.

It follows what the real program was observed to do (``-p`` with the prompt on standard input, ``--output-format json`` printing one result object with
``result``, ``usage``, ``modelUsage``, ``session_id`` ...). What it prints for a sign-in problem or a usage limit is a *guess* at the wording, because the real
messages could not be reproduced; the adapter's classification is tested against these guesses and against unknown text.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

SCRIPT = r'''#!{python}
import json, os, sys, time
d = os.environ["FAKE_CLAUDE_DIR"]
sc = json.load(open(os.path.join(d, "scenario.json")))
args = sys.argv[1:]
if args == ["--version"]:
    print(sc.get("version", "2.1.0 (Claude Code)")); sys.exit(0)
if args == ["--help"]:
    flags = sc.get("flags", ["--print", "--output-format", "--tools", "--system-prompt", "--no-session-persistence", "--safe-mode", "--model", "--effort"])
    print("Usage: claude [options]\n  -p, --print  Print response and exit\n" + "\n".join("  " + f + " <x>  an option" for f in flags if f != "--print")); sys.exit(0)
stdin = sys.stdin.read()
with open(os.path.join(d, "calls.jsonl"), "a") as f:
    f.write(json.dumps({"args": args, "stdin": stdin, "cwd": os.getcwd(), "env": {k: os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "PATH")}}) + "\n")
n = len(open(os.path.join(d, "calls.jsonl")).read().splitlines())
if sc.get("sleep"):
    time.sleep(sc["sleep"])
replies = sc.get("replies")
text = replies[min(n - 1, len(replies) - 1)] if replies else sc.get("reply", "ok")
kind = sc.get("error")
if kind == "garbage":
    print("this is not json"); sys.exit(0)
if kind == "stderr":
    sys.stderr.write(sc.get("message", "boom")); sys.exit(1)
if kind:
    print(json.dumps({"type": "result", "subtype": "success", "is_error": True, "result": sc.get("message", ""), "session_id": "s-err"})); sys.exit(1)
if text == "__empty__":
    text = ""
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text, "session_id": "sess-%d" % n,
    "total_cost_usd": sc.get("cost", 0.0123), "duration_ms": 1234, "num_turns": sc.get("turns", 1), "permission_denials": sc.get("denials", []),
    "usage": {"input_tokens": 12, "output_tokens": sc.get("out_tokens", 34), "cache_read_input_tokens": 100, "cache_creation_input_tokens": 200},
    "modelUsage": {sc.get("model", "claude-test-1"): {"inputTokens": 12, "outputTokens": 34}}}))
'''


class FakeClaude:
    def __init__(self, folder: Path):
        self.dir = folder
        self.dir.mkdir(parents=True, exist_ok=True)
        body = SCRIPT.replace("{python}", sys.executable)
        if os.name == "nt":  # the real program is a .cmd file on Windows, so the stand-in is too (same way of starting it, same quoting)
            impl = self.dir / "claude_standin.py"
            impl.write_text(body)
            self.path = self.dir / "claude.cmd"
            self.path.write_text(f'@echo off\r\n"{sys.executable}" "{impl}" %*\r\n')
        else:
            self.path = self.dir / "claude"
            self.path.write_text(body)
            self.path.chmod(self.path.stat().st_mode | stat.S_IEXEC)
        os.environ["FAKE_CLAUDE_DIR"] = str(self.dir)
        self.set()

    def set(self, **scenario) -> None:
        (self.dir / "scenario.json").write_text(json.dumps(scenario))

    def calls(self) -> list[dict]:
        f = self.dir / "calls.jsonl"
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []
