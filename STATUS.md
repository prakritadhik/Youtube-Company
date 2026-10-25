# Project status

_Last updated: 2026-10-25 · Branch `claude/sweet-hamilton-hvxkym`_

## Summary

**Phases 1 (Foundation) and 2 (local video production) are built, tested and runnable.** Phases 3–7 are not started.
The system can now assemble, check and rights-screen a real video from local assets. It does **not** yet call any AI
provider, connect to YouTube, or publish anything, and it is not autonomous. The soundtrack is an instrumental with
**no vocals**; a sung birthday song needs a music provider that is not connected.

| Phase | State | Notes |
|---|---|---|
| 1 Foundation | ✅ built and tested | Acceptance criteria met on Linux; see evidence below. Windows scripts untested. |
| 2 Real video production | ✅ built and tested (local assets) | One validated demo video produced; sung music, AI art and LLM drafting need providers (see "Needs you") |
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

## Phase 2 acceptance criteria

> The platform can produce at least one validated local video file from approved inputs, and a video that fails
> validation is blocked and returned for correction.

| Criterion | Evidence |
|---|---|
| A real video is produced | `python -m app.cli demo-production` rendered a 38.12 s, 1280×720 H.264/AAC file (2 MB) in about 20 s through the full task chain (9 tasks, each manager-verified) |
| It is validated by measurement | 19 QA checks all passed on it; frames (title card, bar-aligned captions, outro) were inspected by eye; a real browser played the authenticated stream and seeked to 20 s |
| Failing videos are blocked | Tests build deliberately broken videos (corrupt, wrong size, silent, clipped, black, desynchronised, unreadable captions); each is caught by the matching check, and a failure opens correction tasks once |
| Rights are screened | Originality engine, asset licence register and CEO re-computation (`ceo_inspect`); copied text, reused scenes/thumbnails, unlicensed or modified assets and the traditional "Happy Birthday" refrain are each covered by a test |
| It cannot reach YouTube by accident | The demo video carries `DEMO_CONTENT`, which no waiver or approval can clear; the gate stays fail-closed |

## Test results (actually run)

| Check | Result |
|---|---|
| Backend unit + API tests (`python -m pytest`) | **288 passed** (about 2 min serial; about 45 s with `-n 4`) |
| Lint (`ruff check app tests migrations`) | clean |
| Migration drift (`alembic check`) and up/down/up | clean |
| Frontend typecheck (`tsc --noEmit`) and production build | pass (13 routes, including `/videos/[id]`) |
| Browser smoke test (`frontend/e2e/smoke.cjs`, Chromium, fresh install) | **passed**: first-run setup, all 11 pages, report generation, pause banner < 3 s, resume, audit verify, mobile no-overflow, sign-out guard |
| Live concurrency stress (API + worker + 3rd process) | 150 concurrent writes, 60 tasks, 80 reads: 0 errors, audit chain intact over 352 events |
| Video page in a real browser (demo data) | player, lyrics, rights report, 19 QA rows, provenance table render; no horizontal overflow, no console errors |
| Clean-clone install via `scripts/setup.sh` | see "Install verification" below (verified at the end of Phase 1; not repeated for Phase 2) |

Where the tests live: `backend/tests/` — `test_org_security`, `test_audit_settings`, `test_tasks`, `test_board`,
`test_reports`, `test_gate_videos`, `test_finance_backup_runner`, `test_api`, `test_migrations_docs`, `test_config`,
`test_production_core`, `test_render_qa`, `test_originality`, `test_pipeline`, `test_elevenlabs`, `test_api_production`.
Mocks are used only in tests (no provider is called anywhere); the ElevenLabs client has only ever run against a mock transport.

## Implemented (Phase 1)

Organization & permissions · persistent task queue with manager-verified completion · evidence verification (file
hash, saved API response, test result; notes/URLs never sufficient) · intelligence board with lifecycle, versions,
per-channel/department digests and acknowledgements · approvals with owner-only decisions and effect handlers ·
reporting chain with CEO claim re-verification · publication gate and video controls (originality verdicts,
CEO rights sign-off, human medical review, scoped exceptions) · budget guard and usage ledger (actual / estimate /
subscription-included kept apart) · emergency pause with honest report · hash-chained audit log · SQLite backup/restore ·
auth, sessions, CSRF, host allowlist, rate limit, redaction · Next.js dashboard · launcher, CLI, Windows scripts.

## Implemented (Phase 2)

Creative briefs and scripts (versioned, validated, medicine citations enforced) · asset register with provenance, licence
and hash checks · seeded instrumental synthesizer with a bar-accurate timeline · lyric/caption alignment · scene and
thumbnail generation · FFmpeg render · 19-check QA with correction tasks · originality and rights screening with a
findings-only verdict and a CEO recomputation · provider registry with owner attestation and budget gating ·
ElevenLabs text-to-speech client (mock-tested only) · video page in the dashboard · `demo-production` command.
Details: [docs/PRODUCTION.md](docs/PRODUCTION.md).

## Not implemented / known limitations

* **No provider has been run live.** The ElevenLabs text-to-speech client exists but was written from public API
  knowledge and tested against a mock transport only (`verified_live` stays false). There is **no** adapter for ElevenLabs
  music (request format not verified), a language model, image/video generation, Canva or YouTube. `docs/PROVIDERS.md`
  grades every provider fact as Observed / Reported / Unknown.
* **No vocals.** The generated soundtrack is an algorithmic instrumental. A plan that says "sung" is flagged
  `VOCALS_MISSING` and cannot pass review.
* **Creative steps are manual.** Briefs and lyrics/scripts are written by a person (or recorded as such) until a
  language model is connected. The demo lyrics are sample text and are labelled DEMO.
* **Audio fingerprinting is unavailable**, so external-provider audio always goes to human review. Text similarity does
  not detect synonym-level paraphrase, and nothing searches external lyric, music or footage databases.
* Scenes and thumbnails are procedural placeholders, not illustration.
* **Windows scripts are untested on Windows** (`setup.ps1`, `start.bat`, etc.). The Python code is cross-platform
  but only exercised on Linux.
* No scheduler or daily operating cycle; reports are generated on demand (button / CLI / queue task).
* The reporting chain is deterministic. No language model writes narrative yet. Agent "intelligence" is not
  implemented: roles define permissions and duties; LLM-backed behaviour arrives with the provider adapters.
* Revenue, views, retention, comments: **unavailable** (shown as such).
* PostgreSQL is a configuration change in principle but has not been run.
* Artifacts are not included in database backups (copy `data/` for a full backup).

## Needs you (owner decisions / credentials)

1. **Windows run-through:** run `scripts\windows\setup.ps1` then `start.bat` and tell me what fails.
2. **Provider decisions** (each needs a key, your confirmation of the plan's terms in Settings → Providers, and your
   approval of one small test generation before the app will spend anything):
   * a language-model API for drafting briefs, lyrics and scripts (a Claude Pro chat subscription is not assumed to
     cover API use);
   * ElevenLabs: an API key and a check that your plan's terms allow commercial use via the API. Music is the route to a
     *sung* song; text-to-speech is for narration only;
   * an image source (provider, licensed stock, or your own artwork) to replace the procedural placeholders.
3. **Phase 4:** a Google Cloud project with the YouTube Data API enabled and an OAuth "Desktop app" client for each
   channel's Google account.
4. Spending limits (default $0) and which channels you ever want in Controlled autonomy.
5. A named human reviewer process for medicine videos (today: you).

## Install verification

The documented path was run on Linux from a **clean clone of the pushed branch** (empty directory, nothing local):
`scripts/setup.sh` (about 40 s) → fresh `.env` copied from `.env.example` → `python3 launcher.py` → browser smoke test:
**passed**. This found and fixed two real bugs that the unit tests had missed: comments after values in
`.env.example` crashed the first start, and blank placeholder API keys were reported as "configured". Both now have
regression tests. The same path on **Windows has not been run** (that is the first item under "Needs you").
