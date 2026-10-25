# Project status

_Last updated: 2026-10-25 · Branch `claude/sweet-hamilton-hvxkym`_

## Summary

**Phase 1 (Foundation) is built, tested and runnable.** Phases 2–7 are not started. The system does **not** yet
produce videos, call any AI provider, connect to YouTube, or publish anything. It is not autonomous.

| Phase | State | Notes |
|---|---|---|
| 1 Foundation | ✅ built and tested | Acceptance criteria met on Linux; see evidence below. Windows scripts untested. |
| 2 Real video production | ⬜ not started | Needs provider decisions (see "Needs you") |
| 3 Research and SEO | ⬜ not started | |
| 4 YouTube integration | ⬜ not started | Needs your Google Cloud OAuth client |
| 5 Analytics and community | ⬜ not started | Depends on 4 |
| 6 Management and improvement | 🟨 foundation only | Reporting chain, board, acknowledgements exist as deterministic code; no LLM narrative, no experiments module |
| 7 Controlled autonomy | 🟨 foundation only | Persistent queue, leases, retry, recovery, pause, backup exist; no scheduler / daily cycle / alerting |

## Phase 1 acceptance criteria

> The application starts locally, the owner can sign in, tasks persist, the organization is visible, the board can be
> updated, and the CEO report can distinguish actual records from missing data.

| Criterion | Evidence |
|---|---|
| Starts locally | `python3 launcher.py` brought up API + worker + dashboard; `/api/health` OK through the dashboard proxy; SIGTERM stops all three and frees the ports |
| Owner can sign in | First-run setup and sign-in exercised in a real browser (Playwright smoke test passes) |
| Tasks persist | 60 tasks enqueued from a second process were all completed by the worker; restart/crash recovery covered by `test_crash_and_restart_*` |
| Organization visible | 50 roles in the DB and on the Organization page; "Running" only when a task is genuinely RUNNING |
| Board can be updated | Lifecycle, evidence re-verification, channel isolation, acknowledgement — 22 board tests |
| CEO report separates records from missing data | Verified facts / unverified / rejected / estimates / unavailable are separate sections; a fabricated manager claim is rejected (`test_ceo_rejects_a_fabricated_manager_claim`) |

## Test results (actually run)

| Check | Result |
|---|---|
| Backend unit + API tests (`python -m pytest`) | **178 passed** (about 24 s) |
| Lint (`ruff check app tests migrations`) | clean |
| Migration drift (`alembic check`) and up/down/up | clean |
| Frontend typecheck (`tsc --noEmit`) and production build | pass |
| Browser smoke test (`frontend/e2e/smoke.cjs`, Chromium, fresh install) | **passed**: first-run setup, all 11 pages, report generation, pause banner < 3 s, resume, audit verify, mobile no-overflow, sign-out guard |
| Live concurrency stress (API + worker + 3rd process) | 150 concurrent writes, 60 tasks, 80 reads: 0 errors, audit chain intact over 352 events |
| Clean-clone install via `scripts/setup.sh` | see "Install verification" below |

Where the tests live: `backend/tests/` — `test_org_security`, `test_audit_settings`, `test_tasks`, `test_board`,
`test_reports`, `test_gate_videos`, `test_finance_backup_runner`, `test_api`, `test_migrations_docs`.
Mocks are used only in tests (no provider is called anywhere).

## Implemented (Phase 1)

Organization & permissions · persistent task queue with manager-verified completion · evidence verification (file
hash, saved API response, test result; notes/URLs never sufficient) · intelligence board with lifecycle, versions,
per-channel/department digests and acknowledgements · approvals with owner-only decisions and effect handlers ·
reporting chain with CEO claim re-verification · publication gate and video controls (originality verdicts,
CEO rights sign-off, human medical review, scoped exceptions) · budget guard and usage ledger (actual / estimate /
subscription-included kept apart) · emergency pause with honest report · hash-chained audit log · SQLite backup/restore ·
auth, sessions, CSRF, host allowlist, rate limit, redaction · Next.js dashboard · launcher, CLI, Windows scripts.

## Not implemented / known limitations

* **No provider adapters at all** (Claude, ElevenLabs, image, music, video, Canva, YouTube). `docs/PROVIDERS.md`
  lists what must be verified first.
* **Windows scripts are untested on Windows** (`setup.ps1`, `start.bat`, etc.). The Python code is cross-platform
  but only exercised on Linux.
* No scheduler or daily operating cycle; reports are generated on demand (button / CLI / queue task).
* The reporting chain is deterministic. No language model writes narrative yet. Agent "intelligence" is not
  implemented: roles define permissions and duties; LLM-backed behaviour arrives with the provider adapters.
* The `videos` table and gate exist, but nothing creates real video files yet (Phase 2).
* Revenue, views, retention, comments: **unavailable** (shown as such).
* PostgreSQL is a configuration change in principle but has not been run.
* Artifacts are not included in database backups (copy `data/` for a full backup).

## Needs you (owner decisions / credentials)

1. **Windows run-through:** run `scripts\windows\setup.ps1` then `start.bat` and tell me what fails.
2. **Phase 2 provider choices** (I will verify each provider's current terms and report before building):
   which language-model API (needs a billed API key — a Claude Pro chat subscription is not assumed to cover it),
   ElevenLabs API access on your plan, and an affordable licensed **singing/music** tool (do not assume ElevenLabs
   text-to-speech can sing), plus an image source.
3. **Phase 4:** a Google Cloud project with the YouTube Data API enabled and an OAuth "Desktop app" client for each
   channel's Google account.
4. Spending limits (default $0) and which channels you ever want in Controlled autonomy.
5. A named human reviewer process for medicine videos (today: you).

## Install verification

Recorded below after running the documented install path from a clean clone of the pushed branch.
