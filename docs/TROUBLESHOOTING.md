# Troubleshooting

Run `python -m app.cli doctor` (Windows: `scripts\windows\doctor.bat`) first: it prints your OS, RAM, GPU, FFmpeg,
free disk, whether providers are configured, and whether the database and audit chain are healthy.

| Symptom | Cause / fix |
|---|---|
| `Cannot start: Backend dependencies are missing` | Run the setup script again (Windows: `scripts\windows\setup.ps1`; Linux/macOS: `scripts/setup.sh`). |
| `Node.js 20+ was not found` | Install Node LTS from <https://nodejs.org>, reopen the terminal. |
| PowerShell says scripts are disabled | Use `powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1`. |
| Port 3000 or 8000 already in use | Close the other program, or set `YTC_PORT` for the API; the dashboard port is 3000 (edit `launcher.py` / `frontend/package.json`). |
| Dashboard shows a blank page or "Loading…" forever | The API is not running: check `data/logs/api.log`. The dashboard proxies `/api` to `http://127.0.0.1:8000`. |
| `The database is at revision ... but the application needs ...` | Run `python -m app.cli migrate` (the launcher does this automatically). |
| "Too many failed attempts" (HTTP 429) on sign-in | Wait for the time shown (15 min). If locked out for good, reset: `scripts\windows\reset-password.bat`. |
| Forgot the owner password | `scripts\windows\reset-password.bat` (signs out all sessions). |
| `Cross-origin request refused` | You opened the dashboard from an address other than `http://localhost:3000`. Use that address. |
| `Bad Request` / host error from the API | The `Host` header is not allowed. Use `localhost` or `127.0.0.1`, or extend `YTC_ALLOWED_HOSTS`. |
| FFmpeg warning | Only matters from Phase 2. `winget install Gyan.FFmpeg`, then reopen the terminal. |
| A task is stuck in BLOCKED | Open Organization → the task: the blocker says why (failed dependency, budget limit, or "an external action had already started"). |
| Everything says PAUSED | Emergency pause is on: Settings → Resume company. |
| The first-run setup page says "only available from this computer" | Open the dashboard on the PC that runs it, at `http://localhost:3000`. |
| AI Brain says the `claude` program was not found | Install Claude Code and run `claude` once to sign in. If it still is not found, put its full path in `.env` as `YTC_CLAUDE_CODE_BIN=...` (on Windows usually the `claude.cmd` in the npm folder, found with `where claude`), then restart. Details: [AI_BRAIN.md](AI_BRAIN.md) |
| Writing steps wait with "AI_…" in the blocker | The AI brain is not usable right now (not signed in, plan limit, allowance used, or you have not confirmed your plan or set the daily allowance). Fix it on the AI Brain page; the work resumes by itself |
| Antivirus quarantines `node.exe` or `ffmpeg.exe` | Add the project folder to the exclusions; these are normal developer tools. |

**The Windows scripts have not been run on Windows yet.** If one fails, copy the message and the last lines of
`data/logs/*.log` when asking for help — they contain no secrets (keys are redacted before logging).
