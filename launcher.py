#!/usr/bin/env python3
"""Start the whole platform: database setup, API, queue worker and dashboard.

    python launcher.py              # production build of the dashboard (builds it first if needed)
    python launcher.py --dev        # Next.js dev server with hot reload
    python launcher.py --no-frontend
    python launcher.py --check      # verify prerequisites and exit

Works on Windows, macOS and Linux (standard library only). Logs go to data/logs/.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
IS_WINDOWS = os.name == "nt"


def backend_python() -> str:
    venv = BACKEND / ".venv" / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
    return str(venv) if venv.exists() else sys.executable


SOURCE_DIRS = ("app", "components", "lib", "public")
SOURCE_FILES = ("package.json", "package-lock.json", "next.config.mjs", "postcss.config.mjs", "tsconfig.json")


def dashboard_is_stale() -> bool:
    """True when there is no built dashboard, or any dashboard source file is newer than the build (for example after pulling an update).

    Without this an update that adds a page would keep serving the old build, and the new page would show "not found".
    """
    build_id = FRONTEND / ".next" / "BUILD_ID"
    if not build_id.exists():
        return True
    built = build_id.stat().st_mtime
    files = [FRONTEND / f for f in SOURCE_FILES if (FRONTEND / f).exists()]
    for d in SOURCE_DIRS:
        files += [p for p in (FRONTEND / d).rglob("*") if p.is_file()] if (FRONTEND / d).is_dir() else []
    return any(p.stat().st_mtime > built for p in files)


def load_env_file() -> dict[str, str]:
    """Minimal .env reader so the launcher can learn host/port without importing the backend."""
    env: dict[str, str] = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def check_prerequisites(need_frontend: bool) -> list[str]:
    problems: list[str] = []
    py = backend_python()
    r = subprocess.run([py, "-c", "import fastapi, sqlalchemy, alembic, uvicorn"], capture_output=True)
    if r.returncode != 0:
        problems.append("Backend dependencies are missing. Run scripts\\windows\\setup.ps1 (or: pip install -e backend[dev]).")
    if sys.version_info < (3, 11):
        problems.append(f"Python 3.11+ is required (this is {sys.version.split()[0]}).")
    if need_frontend:
        if not shutil.which("node"):
            problems.append("Node.js 20+ was not found. Install it from https://nodejs.org (LTS).")
        elif not (FRONTEND / "node_modules").exists():
            problems.append("Frontend dependencies are missing. Run: cd frontend && npm install")
    if not shutil.which("ffmpeg"):
        print("note: FFmpeg was not found. The dashboard works, but video rendering (Phase 2) needs it. "
              "Windows: winget install Gyan.FFmpeg")
    return problems


def run_step(args: list[str], cwd: Path, env: dict[str, str], label: str) -> None:
    print(f"==> {label}")
    r = subprocess.run(args, cwd=cwd, env=env)
    if r.returncode != 0:
        raise SystemExit(f"Step failed: {label}")


def spawn(args: list[str], cwd: Path, env: dict[str, str], log: Path) -> subprocess.Popen:
    log.parent.mkdir(parents=True, exist_ok=True)
    fh = open(log, "ab", buffering=0)  # noqa: SIM115 - lives as long as the process
    kwargs: dict = {"cwd": cwd, "env": env, "stdout": fh, "stderr": subprocess.STDOUT}
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(args, **kwargs)


def terminate(proc: subprocess.Popen, name: str) -> None:
    if proc.poll() is not None:
        return
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=15)
    except (subprocess.TimeoutExpired, ProcessLookupError, PermissionError):
        proc.kill()
    print(f"stopped {name}")


def wait_http(url: str, timeout: float, proc: subprocess.Popen) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=2) as r:  # noqa: S310 - local URL
                if r.status < 500:
                    return True
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            time.sleep(0.5)
    return False


def tail(path: Path, n: int = 25) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
    except OSError:
        return "(no log)"


def _on_sigterm(_signum, _frame) -> None:
    raise KeyboardInterrupt  # route SIGTERM through the same clean-shutdown path as Ctrl+C


def main() -> int:
    signal.signal(signal.SIGTERM, _on_sigterm)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dev", action="store_true", help="run the dashboard in development mode")
    ap.add_argument("--no-frontend", action="store_true")
    ap.add_argument("--no-worker", action="store_true")
    ap.add_argument("--check", action="store_true", help="verify prerequisites and exit")
    args = ap.parse_args()

    problems = check_prerequisites(need_frontend=not args.no_frontend)
    if problems:
        print("Cannot start:\n - " + "\n - ".join(problems))
        return 2
    if args.check:
        print("All prerequisites are present.")
        return 0

    dotenv = load_env_file()
    env = {**os.environ, **dotenv}
    api_port = env.get("YTC_PORT", "8000")
    api_host = env.get("YTC_HOST", "127.0.0.1")
    logs = ROOT / "data" / "logs"
    py = backend_python()

    run_step([py, "-m", "app.cli", "setup"], BACKEND, env, "Applying database migrations and seeding the organization")

    procs: list[tuple[str, subprocess.Popen]] = []
    try:
        api = spawn([py, "-m", "app.cli", "api"], BACKEND, env, logs / "api.log")
        procs.append(("api", api))
        print(f"==> Starting API on http://{api_host}:{api_port} ...")
        if not wait_http(f"http://{api_host}:{api_port}/api/health", 60, api):
            print("API failed to start. Log tail:\n" + tail(logs / "api.log"))
            return 1

        if not args.no_worker:
            procs.append(("worker", spawn([py, "-m", "app.cli", "worker"], BACKEND, env, logs / "worker.log")))
            print("==> Worker started")

        if not args.no_frontend:
            node = shutil.which("node") or "node"
            next_bin = str(FRONTEND / "node_modules" / "next" / "dist" / "bin" / "next")
            fenv = {**env, "BACKEND_URL": f"http://{api_host}:{api_port}"}
            if not args.dev and dashboard_is_stale():
                run_step([node, next_bin, "build"], FRONTEND, fenv, "Building the dashboard (first run, or the dashboard code changed; ~1 minute)")
            mode = "dev" if args.dev else "start"
            procs.append(("dashboard", spawn([node, next_bin, mode, "-H", "127.0.0.1", "-p", "3000"], FRONTEND, fenv,
                                             logs / "frontend.log")))
            print("==> Starting dashboard ...")
            if not wait_http("http://127.0.0.1:3000/login", 90, procs[-1][1]):
                print("Dashboard failed to start. Log tail:\n" + tail(logs / "frontend.log"))
                return 1
            print("\nOpen the dashboard:  http://localhost:3000\n")
        print("Running. Press Ctrl+C to stop everything.  Logs: " + str(logs))

        while True:
            for name, p in procs:
                if p.poll() is not None:
                    log_name = "frontend.log" if name == "dashboard" else f"{name}.log"
                    print(f"\n'{name}' stopped unexpectedly (exit {p.returncode}). Log tail:\n" + tail(logs / log_name))
                    return 1
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping ...")
        return 0
    finally:
        for name, p in reversed(procs):
            terminate(p, name)


if __name__ == "__main__":
    sys.exit(main())
