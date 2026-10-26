#!/usr/bin/env python3
"""Prove that the AI company works in the real running stack: real API process, real worker process, real FFmpeg, a stand-in ``claude`` program.

    python scripts/verify_ai_stack.py

It builds a throwaway installation in a temporary folder (your real data and your real Claude account are never touched), starts the real launcher
(API and worker only, port 18766) with ``YTC_CLAUDE_CODE_BIN`` pointing at a small stand-in for Claude Code that answers by reading the prompt, then
does what you would do in the dashboard, through the API: sign in, confirm the plan terms, set the allowance, run the connection test, propose and
approve an idea, press "Start with the AI company", and waits for the worker's own housekeeping to write, review, build, check and prepare the video.

It checks that the video reaches READY with the publication request *pending* (the company never grants it), and that every model call is recorded.
It proves the wiring in the worker process (no test harness drives anything). It does not prove anything about the quality of what a real model writes;
for that run ``YTC_LIVE_CLAUDE=1 python -m pytest tests/test_ai_live.py`` from backend/.

Standard library only. Windows support is untested.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
PORT = "18766"
BASE = f"http://127.0.0.1:{PORT}"
IS_WINDOWS = os.name == "nt"
PW = "Correct-Horse-9"

FAKE = r'''#!{python}
import json, re, sys
a = sys.argv[1:]
if a == ["--version"]:
    print("2.1.0 (stand-in)"); sys.exit(0)
if a == ["--help"]:
    print("  -p, --print\n  --output-format <f>\n  --tools <t>\n  --system-prompt <s>\n  --no-session-persistence\n  --safe-mode\n  --model <m>\n  --effort <e>"); sys.exit(0)
p = sys.stdin.read()
if "List every tool you can call" in p:
    out = "NONE"
elif "reviewing a" in p and "Decide whether it may go into production" in p:
    out = json.dumps({"decision": "approve", "notes": "Specific, original and faithful to the brief.", "checks": [{"check": "specificity", "ok": True}]})
elif "Creative Director" in p:
    out = json.dumps({"video_objective": "Give a child a birthday song that feels like a morning in a garden.", "target_audience": "Parents and children aged 3 to 8",
        "viewer_benefit": "A gentle song to sing along to at a party.", "main_concept": "A garden wakes up as the birthday child arrives, told through bells and soft drums.",
        "tone_and_style": "warm, playful, unhurried", "planned_length_seconds": 40, "visual_direction": "bright illustrated hills with paper lanterns",
        "audio_direction": "bells, pad, soft bass and brushed drums", "hook_and_opening": "Bells over a sunrise", "structure": "intro, two verses, two choruses, outro",
        "thumbnail_concept": "A big friendly title over a sunrise hill", "distinctive_elements": ["lyrics follow one morning from first light to the party"],
        "originality_requirements": ["no reused lines", "own melody and rhythm"], "success_criteria": ["viewers stay to the second chorus", "no QA failures"],
        "musical_plan": {"tempo_bpm": 104, "key": "G major", "mood": "bright", "instrumentation": ["bells", "pad", "bass", "drums"], "vocal_delivery": "instrumental",
                         "song_structure": ["intro", "verse", "chorus", "verse 2", "chorus 2", "outro"]}})
elif "Script and Lyrics Writer" in p:
    words = ["lantern", "meadow", "ribbon", "sparrow", "pebble", "orchard", "willow", "ember", "thistle", "harbour", "velvet", "copper"]
    lines, k = [], 0
    for m in re.finditer(r"section header exactly (\[[^\]]+\]), then exactly (\d+) lines, each at most (\d+) characters", p):
        lines.append(m.group(1))
        for _ in range(int(m.group(2))):
            lines.append(("%s s%d drifts by the %s" % (words[k % 12], k, words[(k + 5) % 12]))[: int(m.group(3))]); k += 1
    out = json.dumps({"text": "\n".join(lines), "note": "stand-in"})
elif "Content Strategy Specialist" in p:
    out = json.dumps({"ideas": [{"title": "Lantern River Birthday", "concept": "A song that follows paper lanterns down a quiet river on the night of a birthday.",
                                 "audience": "Parents and children aged 3 to 8", "rationale": "Gentle and easy to sing."}]})
else:
    out = "{}"
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": out, "session_id": "stand-in", "total_cost_usd": 0.0, "duration_ms": 5, "num_turns": 1,
                  "permission_denials": [], "usage": {"input_tokens": 10, "output_tokens": 10}, "modelUsage": {"stand-in-model": {}}}))
'''


def backend_python() -> str:
    venv = BACKEND / ".venv" / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
    return str(venv) if venv.exists() else sys.executable


def run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=BACKEND, env=env, capture_output=True, text=True)


class Api:
    def __init__(self) -> None:
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = ""

    def call(self, method: str, path: str, body: object | None = None) -> object:
        req = urllib.request.Request(BASE + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "X-CSRF-Token": self.csrf})
        try:
            with self.opener.open(req, timeout=30) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            raise SystemExit(f"{method} {path} -> HTTP {e.code}: {e.read().decode()[:300]}") from e


def start(env: dict[str, str], log: Path) -> subprocess.Popen:
    kw: dict = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if IS_WINDOWS else {"start_new_session": True}  # type: ignore[attr-defined]
    p = subprocess.Popen([sys.executable, str(ROOT / "launcher.py"), "--no-frontend"], cwd=ROOT, env=env, stdout=open(log, "ab"), stderr=subprocess.STDOUT, **kw)
    end = time.time() + 90
    while time.time() < end:
        if p.poll() is not None:
            raise SystemExit(f"the launcher exited early; see {log}")
        try:
            with urllib.request.urlopen(f"{BASE}/api/health", timeout=2) as r:  # noqa: S310 - local URL
                if r.status == 200:
                    return p
        except OSError:
            time.sleep(0.5)
    raise SystemExit("the API did not start in 90 seconds")


def stop(p: subprocess.Popen) -> None:
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
    else:
        os.kill(p.pid, signal.SIGTERM)
    p.wait(timeout=60)


def wait_for(cond, what: str, timeout: float):
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(2)
    raise SystemExit(f"timed out waiting for: {what}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ytc-ai-"))
    fake = tmp / ("claude.py" if IS_WINDOWS else "claude")
    fake.write_text(FAKE.replace("{python}", sys.executable))
    if not IS_WINDOWS:
        fake.chmod(0o755)
    env = {**os.environ, "YTC_DATA_DIR": str(tmp / "data"), "YTC_PORT": PORT, "YTC_FRONTEND_ORIGIN": "http://localhost:3000", "VERIFY_PW": PW,
           "YTC_CLAUDE_CODE_BIN": str(fake)}
    log = tmp / "launcher.log"
    results: list[tuple[str, bool, str]] = []
    p = None
    finished = False
    try:
        print(f"Throwaway installation: {tmp}")
        for cmd in (["-m", "app.cli", "setup"], ["-m", "app.cli", "create-owner", "--username", "owner", "--password-env", "VERIFY_PW"]):
            r = run([backend_python(), *cmd], env)
            if r.returncode != 0:
                raise SystemExit(f"{' '.join(cmd)} failed:\n{r.stdout}\n{r.stderr}")
        p = start(env, log)
        api = Api()
        login = api.call("POST", "/api/auth/login", {"username": "owner", "password": PW})
        api.csrf = login["csrf_token"]  # type: ignore[index]

        st = api.call("GET", "/api/ai/status")
        results.append(("before any setting the brain is NOT ready and says why", st["brain"]["ready"] is False and st["brain"]["code"] == "NEEDS_ATTESTATION", st["brain"]["detail"]))  # type: ignore[index]
        try:
            api.call("POST", "/api/ai/ideas", {"channel": "happy-birthday-songs", "count": 1})
            results.append(("asking for ideas is refused while the brain is not ready", False, "it was accepted"))
        except SystemExit as e:
            results.append(("asking for ideas is refused while the brain is not ready", "not ready" in str(e), str(e)[:120]))

        api.call("PUT", "/api/settings/provider_attestations", {"value": {"claude-code": {"api_access_confirmed": True, "commercial_use_confirmed": True, "note": "verify_ai_stack"}}})
        api.call("PUT", "/api/settings/budget", {"value": {"daily_model_requests": 60, "daily_provider_requests": 60}})
        api.call("POST", "/api/ai/test", {})
        t = wait_for(lambda: (lambda s: s["test"] if s["test"]["state"] == "done" else None)(api.call("GET", "/api/ai/status")), "the connection test", 120)
        results.append(("the connection test passes (starts, signed in, no tools)", bool(t["report"]["ok"]), json.dumps(t["report"]["problems"])))

        idea = api.call("POST", "/api/ideas", {"channel": "happy-birthday-songs", "title": "Garden Morning Birthday Song", "audience": "Parents and children aged 3 to 8",
                                                "concept": "A birthday song that follows one morning in a garden, from first light to the party.",
                                                "requires": ["instrumental_music", "illustrated_scenes", "video_render", "llm_drafting"]})
        api.call("POST", f"/api/ideas/{idea['id']}/decision", {"approve": True, "note": "Fits the channel."})  # type: ignore[index]
        started = api.call("POST", f"/api/ideas/{idea['id']}/start-with-ai", {})  # type: ignore[index]
        vid = started["video_id"]  # type: ignore[index]
        print(f"Started video #{vid}; the worker's own housekeeping now reviews and conducts it (this takes a few minutes)...")

        def progress():
            pr = api.call("GET", f"/api/videos/{vid}/progress")
            print("  ", pr["step"], "-", pr["detail"][:90])  # type: ignore[index]
            return pr if pr["step"] in ("publication_approval", "stopped", "task_stuck", "corrections", "rights_decision", "brain_unavailable") else None  # type: ignore[index]

        end = wait_for(progress, "the video to reach a decision of yours", 420)
        results.append(("the video reaches 'waiting for your approval to publish'", end["step"] == "publication_approval", end["detail"]))  # type: ignore[index]
        v = api.call("GET", f"/api/videos/{vid}")
        results.append(("it is READY, QA passed, rights PASS, CEO verified", (v["stage"], v["qa_passed"], v["originality_status"], v["ceo_rights_verified"]) == ("READY", True, "PASS", True), str((v["stage"], v["originality_status"]))))  # type: ignore[index]
        results.append(("the company did not grant the publication approval", v["owner_approval_status"] == "PENDING", v["owner_approval_status"]))  # type: ignore[index]
        results.append(("the lyrics are labelled as AI-written", bool(v["text"]) and v["text"]["author"].startswith("AI:"), str(v["text"] and v["text"]["author"])))  # type: ignore[index]
        act = api.call("GET", "/api/ai/activity?limit=50")
        purposes = [c["purpose"] for c in act["calls"]]  # type: ignore[index]
        work = sorted(x for x in purposes if x != "doctor")
        results.append(("four model calls were recorded (brief, its review, lyrics, their review)", work == ["brief.draft", "review.brief", "review.script", "script.draft"], str(work)))
        tasks = [x for x in v["tasks"]]  # type: ignore[index]
        results.append(("every task of the chain was completed by a manager", all(x["status"] == "COMPLETED" for x in tasks), str({x["status"] for x in tasks})))
        finished = True
    finally:
        if p is not None:
            try:
                stop(p)
            except Exception:  # noqa: BLE001
                pass
        if not finished or any(not ok for _, ok, _ in results):
            print(f"\n(launcher log kept at {log})")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    width = max(len(n) for n, _, _ in results)
    print()
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name.ljust(width)}  {textwrap.shorten(str(detail), 90)}")
    return 0 if finished and all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
