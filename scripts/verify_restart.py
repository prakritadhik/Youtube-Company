#!/usr/bin/env python3
"""Prove that scheduled work and its records survive a restart, using the real API and worker processes.

    python scripts/verify_restart.py

It builds a throwaway installation in a temporary folder (your real data is never touched), starts the real launcher (API and
worker only, on port 18765), and checks, with real processes and the real database:

  1. the daily operating cycle fires by itself because its slot is due, runs to completion and leaves a verified executive report;
  2. the launcher is stopped; three queued tasks are added while it is down and a *week* of missed slots is simulated;
  3. on restart the queued tasks run, the schedule runs its latest missed slot exactly once (not once per missed day), and
     everything recorded before the stop is still there;
  4. the audit log's hash chain still verifies.

Standard library only. Windows support is untested.
"""

from __future__ import annotations

import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
PORT = "18765"
IS_WINDOWS = os.name == "nt"


def backend_python() -> str:
    venv = BACKEND / ".venv" / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
    return str(venv) if venv.exists() else sys.executable


def run(args: list[str], env: dict[str, str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=BACKEND, env=env, capture_output=True, text=True, **kw)


def orm(env: dict[str, str], code: str) -> str:
    r = run([backend_python(), "-c", textwrap.dedent(code)], env)
    if r.returncode != 0:
        raise SystemExit(f"helper failed:\n{r.stdout}\n{r.stderr}")
    return r.stdout.strip()


def start(env: dict[str, str], log: Path) -> subprocess.Popen:
    kw: dict = {}
    if IS_WINDOWS:
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        kw["start_new_session"] = True
    p = subprocess.Popen([sys.executable, str(ROOT / "launcher.py"), "--no-frontend"], cwd=ROOT, env=env, stdout=open(log, "ab"), stderr=subprocess.STDOUT, **kw)
    end = time.time() + 90
    while time.time() < end:
        if p.poll() is not None:
            raise SystemExit(f"the launcher exited early; see {log}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=2) as r:  # noqa: S310 - local URL
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


def q(db: Path, sql: str, *args):
    con = sqlite3.connect(db, timeout=30)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def wait_for(cond, what: str, timeout: float = 150.0):
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(1)
    raise SystemExit(f"timed out waiting for: {what}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ytc-restart-"))
    env = {**os.environ, "YTC_DATA_DIR": str(tmp), "YTC_PORT": PORT, "YTC_FRONTEND_ORIGIN": "http://localhost:3000", "VERIFY_PW": "Correct-Horse-9"}
    db, log = tmp / "db" / "company.sqlite3", tmp / "launcher.log"
    results: list[tuple[str, bool, str]] = []
    finished = False
    p = None
    try:
        print(f"Throwaway installation: {tmp}")
        for cmd in (["-m", "app.cli", "setup"], ["-m", "app.cli", "create-owner", "--username", "owner", "--password-env", "VERIFY_PW"]):
            r = run([backend_python(), *cmd], env)
            if r.returncode != 0:
                raise SystemExit(r.stdout + r.stderr)
        # make the daily cycle's latest 07:30 slot already due, as if the schedule had been created three days ago
        orm(env, """
            from datetime import timedelta
            from sqlalchemy import select
            from app.db import session_scope
            from app.models.ops import Schedule
            from app.timeutil import utcnow
            with session_scope() as db:
                for s in db.scalars(select(Schedule)):
                    s.created_at = utcnow() - timedelta(days=3)
                    if s.slug != 'daily-cycle':
                        s.enabled = False   # only the cycle matters here
        """)
        print("Starting the real API and worker ...")
        p = start(env, log)

        def cycle_done():
            return q(db, "select id, status from tasks where task_type='ops.daily_cycle' and status='COMPLETED' order by id")
        first = wait_for(cycle_done, "the daily cycle to run by itself")
        n_before_tasks = q(db, "select count(*) from tasks")[0][0]
        reports_before = q(db, "select id from reports where kind='executive' order by id")
        results.append(("the daily cycle fired on its own and completed", len(first) == 1, f"task {first[0][0]}"))
        results.append(("it left a verified executive report", len(reports_before) >= 1, f"report(s) {[r[0] for r in reports_before]}"))
        beats = q(db, "select count(*) from worker_heartbeats")[0][0]
        results.append(("the worker is beating", beats >= 1, f"{beats} worker row(s)"))

        print("Stopping the launcher (a restart) ...")
        stop(p)
        p = None
        orm(env, """
            from datetime import timedelta
            from zoneinfo import ZoneInfo
            from sqlalchemy import select
            from app.db import session_scope
            from app.models.ops import Schedule
            from app.services import settings as settings_svc
            from app.services import tasks
            from app.security.permissions import Actor
            from app.timeutil import utcnow
            with session_scope() as db:
                s = db.scalar(select(Schedule).where(Schedule.slug == 'daily-cycle'))
                tz = ZoneInfo(settings_svc.get(db, 'timezone'))
                # a different clock time, half an hour ago: its latest slot is new (the first run used another slot)
                s.cadence = {'kind': 'daily', 'at': (utcnow().astimezone(tz) - timedelta(minutes=30)).strftime('%H:%M')}
                s.last_slot = utcnow() - timedelta(days=8)       # and a week of slots came due while the app was off
                for i in range(3):
                    tasks.create(db, Actor.system('verify'), title=f'queued while down {i}', task_type='system.noop', review_required=False)
        """)
        print("Starting again ...")
        p = start(env, log)
        wait_for(lambda: q(db, "select count(*) from tasks where title like 'queued while down%' and status='COMPLETED'")[0][0] == 3, "the queued tasks to run")
        results.append(("tasks queued while it was down ran after the restart", True, "3 of 3"))
        second = wait_for(lambda: len(cycle_done()) >= 2 and cycle_done(), "the catch-up cycle")
        time.sleep(20)  # give a (wrong) second catch-up run time to appear
        cycles = len(cycle_done())
        results.append(("a week of missed slots produced exactly one catch-up run", cycles == 2, f"{cycles} cycle runs in total (1 before, 1 after)"))
        still = q(db, "select id from reports where kind='executive' order by id")
        results.append(("the earlier report is still there", all(r in still for r in reports_before), f"{len(still)} executive report(s)"))
        results.append(("earlier tasks are still recorded", q(db, "select count(*) from tasks")[0][0] >= n_before_tasks, "task count did not shrink"))
        audit = run([backend_python(), "-m", "app.cli", "verify-audit"], env)
        results.append(("the audit log hash chain still verifies", audit.returncode == 0, audit.stdout.strip().splitlines()[-1] if audit.stdout.strip() else audit.stderr[-120:]))
        _ = second
        finished = True
    finally:
        if p is not None:
            stop(p)
        ok = finished and all(r[1] for r in results)
        print()
        for name, good, detail in results:
            print(f"  [{'PASS' if good else 'FAIL'}] {name}  ({detail})")
        print("\nRESTART CHECK PASSED" if ok else f"\nRESTART CHECK FAILED or did not finish  (log: {log})")
        if ok:
            shutil.rmtree(tmp, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
