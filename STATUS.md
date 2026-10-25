# Project status

_Last updated: 2026-10-25 · Branch `claude/sweet-hamilton-hvxkym`_

## Summary

**Phases 1 (Foundation), 2 (local video production), 3 (research, planning and packaging) 4 (YouTube integration, mock-tested
only) and **7 (scheduler, daily cycle, alerts, health)** are built and tested.** Phases 5 and 6 are not started. The system can assemble, check and rights-screen a real video from local assets, plan content
per channel, keep a source register, and draft, check and approve metadata and thumbnail concepts. It does **not** yet call
any AI provider, and it has **never connected to the real YouTube** (that needs your Google Cloud OAuth client; see docs/YOUTUBE.md) or published
anything, and it is not autonomous. The soundtrack is an instrumental with
**no vocals**; a sung birthday song needs a music provider that is not connected. Search, trend, keyword and audience data
are **unavailable** (nothing is estimated), and titles and descriptions are plain template drafts, not model-written.

| Phase | State | Notes |
|---|---|---|
| 1 Foundation | ✅ built and tested | Acceptance criteria met on Linux; see evidence below. Windows scripts untested. |
| 2 Real video production | ✅ built and tested (local assets) | One validated demo video produced; sung music, AI art and LLM drafting need providers (see "Needs you") |
| 3 Research and SEO | ✅ built and tested (no data source / language model) | Planning, source register, safe fetcher, metadata and thumbnail checks, approval gate; trend/keyword data unavailable |
| 4 YouTube integration | 🟨 built, **mock-tested only** | Sign-in, identity confirmation, resumable upload, thumbnail, scheduling, read-back verification, quota ledger; needs your OAuth client and a first real run (docs/YOUTUBE.md) |
| 5 Analytics and community | ⬜ not started | Depends on 4 |
| 6 Management and improvement | 🟨 foundation only | Reporting chain, board, acknowledgements exist as deterministic code; no LLM narrative, no experiments module |
| 7 Controlled autonomy | ✅ built and tested (restart-verified on Linux) | Persistent scheduler with catch-up, daily operating cycle, alerts, worker heartbeat, health checks, gated autonomous upload queueing; no e-mail/phone alerts; Windows run untested |

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

## Phase 3 acceptance criteria

> A content brief can move through research, scripting, metadata preparation, and approval with evidence and persistent records.

| Criterion | Evidence |
|---|---|
| Research | Ideas and notes are persistent records; medicine ideas cannot be approved without two sources, one authoritative; `research.fetch_source` tasks store a hashed snapshot that the manager re-verifies; `test_an_approved_idea_becomes_a_video_with_its_sources` |
| Scripting | An approved idea becomes a video whose sources are copied into the script's citable source list; medicine chains start with a verified research task before the brief (`test_source_based_channels_must_record_sources_before_the_brief`) |
| Metadata preparation | `seo.prepare_metadata` task by the Keyword and SEO Specialist produces a checked set with evidence (file + record) that the research manager verifies; nothing is applied yet |
| Approval | A different manager approves; blocking findings cannot be waived; revise-level findings need a recorded reason; approval re-checks, then writes the title/description/tags to the video (`test_preparing_metadata_creates_a_checked_set_but_changes_nothing_until_it_is_approved`) |
| Enforced downstream | The publication gate refuses a video without approved metadata, or whose title/description was edited afterwards |
| Live demonstration | `python -m app.cli demo-production` now also approves a thumbnail concept and metadata (with three measured chapters on the 38 s demo) and still ends with only `DEMO_CONTENT` blocking |
| In a browser | Content-plan page (propose → assess → approve → start production; a sung idea is *parked*, not approved) and the video page's packaging cards, including a low-contrast thumbnail concept refused with the measured ratio; no horizontal overflow at 390 px; smoke test passes on a fresh install |

## Phase 7 acceptance criteria

> A scheduled development workflow runs without requiring the owner to keep the AI chat open, and its tasks and reports survive application restarts.

Verified with real processes by `python scripts/verify_restart.py` (a throwaway installation; the real API and worker):

| Check | Result |
|---|---|
| The daily operating cycle fires by itself from its schedule, completes and leaves a verified executive report | PASS |
| The launcher is stopped; three tasks queued and a week of missed slots simulated; after restart the tasks ran | PASS (3 of 3) |
| The schedule ran its latest missed slot **once**, not once per missed day | PASS (one catch-up run) |
| Reports and tasks recorded before the stop are intact; the audit hash chain verifies | PASS |

Unit and API tests: scheduler arithmetic in the owner's time zone (including a daylight-saving change), idempotent slots, two workers,
catch-up and skip policies, restart in-process, alert de-duplication and escalation, every health check, the daily cycle's phases and
its autonomy limits (never an unlisted channel, never medicine, daily limit, any gate failure), pause stops the cycle.

## Phase 4 acceptance criteria

> Complete a real integration test with an authorized account, **or clearly document which external configuration remains necessary.**

The first alternative is **not met**: no real account was used. The second is: [docs/YOUTUBE.md](docs/YOUTUBE.md) lists the six things only you
can do (Google Cloud project, consent screen, Desktop OAuth client, attestation, connect and confirm each channel, switch to
Approval-first) and a one-minute `youtube-selftest` that answers the open questions with real data.

What *was* verified, against a mock that follows Google's documented protocol (it models the documentation, not Google):

| Behaviour | Evidence |
|---|---|
| OAuth with PKCE, single-use state, minimal scopes; the channel Google reports must be confirmed by the owner, who also declares the audience; one YouTube channel per internal channel | `test_youtube_connection.py` (19) |
| Tokens and upload-session URLs encrypted, key outside the database; a missing key is reported and never replaced; refused refresh → *needs reconnect* | same |
| Upload re-checks the whole publication gate, attestation, file hash, quota at run time; no second copy of a video; interrupted uploads resume the same session byte-for-byte; quota exhaustion parks and later resumes | `test_youtube_upload.py` (32) |
| Success only from a read-back on the expected channel with the requested title, category, audience, privacy and schedule; a privately-locked public upload is reported as **not published**; a wrong channel is a mismatch | same, plus five deliberate mutations that were confirmed to fail it |
| Self-test uploads private, reads back, sets a thumbnail, deletes, never touches the catalogue | `test_youtube_selftest.py` |
| API and dashboard: connect → callback → confirm; queue, list, abandon; viewers read-only; CSRF; the browser flow on a migrated install, 390 px width | `test_api_youtube.py` (10), Playwright run |

## Test results (actually run)

| Check | Result |
|---|---|
| Backend unit + API tests (`python -m pytest`) | **601 passed** (about 80 s with `-n 4`) |
| Lint (`ruff check app tests migrations`) | clean |
| Migration drift (`alembic check`) and up/down/up | clean |
| Frontend typecheck (`tsc --noEmit`) and production build | pass (14 routes, including `/plan`, `/videos/[id]` and the YouTube cards) |
| Browser smoke test (`frontend/e2e/smoke.cjs`, Chromium, fresh install) | **passed**: first-run setup, all 11 pages, report generation, pause banner < 3 s, resume, audit verify, mobile no-overflow, sign-out guard |
| Live concurrency stress (API + worker + 3rd process) | 150 concurrent writes, 60 tasks, 80 reads: 0 errors, audit chain intact over 352 events |
| Video page in a real browser (demo data) | player, lyrics, rights report, 19 QA rows, provenance table render; no horizontal overflow, no console errors |
| Clean-clone install via `scripts/setup.sh` | see "Install verification" below (verified at the end of Phase 1; not repeated for Phase 2) |

Where the tests live: `backend/tests/` — `test_org_security`, `test_audit_settings`, `test_tasks`, `test_board`,
`test_reports`, `test_gate_videos`, `test_finance_backup_runner`, `test_api`, `test_migrations_docs`, `test_config`,
`test_production_core`, `test_render_qa`, `test_originality`, `test_pipeline`, `test_elevenlabs`, `test_api_production`, `test_demo`,
`test_research_safety`, `test_research_ideas`, `test_research_seo`, `test_research_workflow`, `test_api_research`,
`test_youtube_connection`, `test_youtube_upload`, `test_youtube_selftest`, `test_api_youtube`,
`test_ops_scheduler`, `test_ops_health_cycle`, `test_api_ops`; `scripts/verify_restart.py` is the live check.
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

## Implemented (Phase 3)

Safe web fetcher (public addresses only, connection pinned to the checked address, robots.txt, size/time caps) · untrusted-text
layer · research register with provenance · content ideas with deterministic assessment, explainable ordering, parking and
approval · metadata sets with versioned checks and approval · measured chapters · thumbnail-concept checks · queue handlers ·
research department claims verified by the CEO · publication gate requires approved, unmodified metadata · Content Plan page and
packaging cards. Details: [docs/RESEARCH.md](docs/RESEARCH.md).

## Implemented (Phase 4)

Google OAuth (PKCE, loopback via the backend), identity confirmation with an owner-declared audience, encrypted token storage with the key
outside the database, resumable upload with resume and no-duplicate guarantees, thumbnails, scheduling, read-back verification, a local
quota ledger, an idempotent `youtube.upload` queue task evidenced by saved API responses, `youtube-status` and `youtube-selftest`
commands, Settings and video-page cards. Details: [docs/YOUTUBE.md](docs/YOUTUBE.md).

## Implemented (Phase 7)

Persistent schedules with catch-up and skip policies, idempotent slots, owner time zone; the worker's heartbeat; alerts (de-duplicated,
escalating, self-resolving); a hierarchy of health checks; the daily operating cycle; gated autonomous upload queueing; the Operations page
and a badge on every page; `ops-status`; `scripts/verify_restart.py`. Details: [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Not implemented / known limitations

* **YouTube has never been contacted.** Everything in Phase 4 is tested against a mock of Google's documented behaviour. Unconfirmed
  until your first real run: that the resumable-upload and thumbnail calls match the live API, the real quota costs, whether public
  uploads stay public (an unverified API project is *reported* to have them locked private), whether tokens last, and the loopback
  redirect. No analytics, comments, post-upload metadata edits, playlists or deletes (other than the self-test's own video).
* **No other provider has been run live either.** The ElevenLabs text-to-speech client exists but was written from public API
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
* **No search, trend, keyword, competitor or audience data source is connected.** Those facts exist only if a person records
  them with a source and a date; otherwise they read *unavailable*. No title or thumbnail is shown to perform better than another.
* **Template wording.** Titles, descriptions and tags are plain template drafts from the brief. Ideas are proposed by a person.
* **The fetcher has not touched the live internet** (the build environment's network policy blocks most hosts); it is tested
  against a mock web server and a deliberate set of attacks, and the first real fetch is its live test.
* **Word-based screening.** Distinctness and title accuracy compare words, not meaning. The thumbnail gate rule is not enforced yet
  (an approved concept is advisory until an image provider makes thumbnails).
* The platform limits used by the metadata checks are *reported* values; Phase 4 confirms them against the live API.
* **Windows scripts are untested on Windows** (`setup.ps1`, `start.bat`, etc.). The Python code is cross-platform
  but only exercised on Linux.
* **Alerts are in the dashboard and CLI only.** No e-mail, SMS or phone notification is configured. A stopped worker is noticed by the dashboard (banner within two minutes) and not by anything outside it.
* The scheduler runs inside the worker, so it only works while the launcher is running; there is no Windows service or Task Scheduler entry yet.
* The reporting chain is deterministic. No language model writes narrative yet. Agent "intelligence" is not
  implemented: roles define permissions and duties; LLM-backed behaviour arrives with the provider adapters.
* Revenue, views, retention, comments: **unavailable** (shown as such).
* PostgreSQL is a configuration change in principle but has not been run.
* Artifacts are not included in database backups (copy `data/` for a full backup).

## Needs you (owner decisions / credentials)

1. **Windows run-through:** run `scripts\windows\setup.ps1` then `start.bat` and tell me what fails.
2. **(Optional now, needed for Phase 3 to be more than templates)** a source of search/trend data you are allowed to use
   (for example an export from a tool you pay for) or the willingness to record observations by hand on ideas.
3. **Provider decisions** (each needs a key, your confirmation of the plan's terms in Settings → Providers, and your
   approval of one small test generation before the app will spend anything):
   * a language-model API for drafting briefs, lyrics and scripts (a Claude Pro chat subscription is not assumed to
     cover API use);
   * ElevenLabs: an API key and a check that your plan's terms allow commercial use via the API. Music is the route to a
     *sung* song; text-to-speech is for narration only;
   * an image source (provider, licensed stock, or your own artwork) to replace the procedural placeholders.
4. **Phase 4 (blocks every real upload):** the Google Cloud setup in [docs/YOUTUBE.md](docs/YOUTUBE.md): project, YouTube Data API, consent
   screen, a Desktop OAuth client in `.env`, your confirmation of the API terms, signing in to each channel and answering whether it is made
   for kids, Approval-first mode, then `youtube-selftest`.
5. Spending limits (default $0) and which channels you ever want in Controlled autonomy.
6. A named human reviewer process for medicine videos (today: you).

## Install verification

The documented path was run on Linux from a **clean clone of the pushed branch** (empty directory, nothing local):
`scripts/setup.sh` (about 40 s) → fresh `.env` copied from `.env.example` → `python3 launcher.py` → browser smoke test:
**passed**. This found and fixed two real bugs that the unit tests had missed: comments after values in
`.env.example` crashed the first start, and blank placeholder API keys were reported as "configured". Both now have
regression tests. The same path on **Windows has not been run** (that is the first item under "Needs you").
