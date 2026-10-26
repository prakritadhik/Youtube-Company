# Project status

_Last updated: 2026-10-26 · Branch `claude/sweet-hamilton-hvxkym`_

## Summary

**Phases 1 (Foundation), 2 (local video production), 3 (research, planning and packaging), 4 (YouTube integration, mock-tested
only), 5 (performance data and comments, mock-tested only), 6 (lessons, experiments and comparisons), 7 (scheduler, daily cycle, alerts, health), 8 (the AI brain) and 9 (Connections and continuous integration)** are built and tested.
None of the YouTube-dependent phases has touched the real YouTube, and no real experiment has run. The system can assemble, check and rights-screen a real video from local assets, plan content
per channel, keep a source register, and draft, check and approve metadata and thumbnail concepts.

**New in Phase 8, and the answer to "can the AI use my computer?":** yes. The roles that write and the managers who review them now do their thinking by running
**Claude Code on your computer under your own Claude account** (no API key; no tools; its output is only text that the company then checks). *Content Plan → Start with the AI company*
takes an approved idea to a finished video **awaiting your approval**: the AI writes the brief and the lyrics/script, managers review (bounded), the production chain runs, the CEO re-checks the rights
evidence, clean metadata is approved and the video is moved to READY with a request for your approval. It stops at every decision that is yours. Ran end to end against the **real** Claude Code on the
build machine (6 model calls, one correction round, one video READY); not run on Windows; you must confirm your plan's terms and set a daily allowance first ([docs/AI_BRAIN.md](docs/AI_BRAIN.md)).

**A gap this phase found and closed (disclosed because earlier phases were verified through the demo and unit tests, not the live app):** in the running dashboard "Start production" created only a
video, nothing reviewed submitted tasks, and the steps that record QA, rights and CEO verdicts, approve metadata and ask for approval existed only inside the demonstration command. A video could not actually be
produced from the dashboard. The conductor now performs those steps for videos started with the AI company; *Start by hand* still creates only the video.

**New in Phase 9, and the answer to "make it run properly anywhere":**
* **Continuous integration on GitHub** (`.github/workflows/ci.yml`) installs the project from scratch on a clean **Linux** and a clean **Windows** machine on every push, then lints, runs the whole test suite, builds the
  dashboard, starts the real API and worker processes (restart survival, and the AI company end to end with a stand-in `claude`), and runs the Windows installer (`setup.ps1`) exactly as you would. Getting it green on
  Windows found and fixed four real faults that had passed on Linux (listed under "Faults found by running on a clean Windows machine" below).
* **Connections page** (*Connections* in the dashboard): paste the ElevenLabs key and the Google OAuth client there instead of editing `.env`. Stored encrypted (the same key file as YouTube sign-in tokens), used at once by the API
  and the worker without a restart, shown once as "saved" and never again, owner-only, audited without the value. A *Test* button makes one harmless read-only call (ElevenLabs account check). A saved key is **not** called working
  until its test passes. Canva has no connection to make: its designs stay a manual step. YouTube still needs the Google Cloud setup (docs/YOUTUBE.md); only the final paste moved into the dashboard.

It has **never connected to the real YouTube** (that needs your Google Cloud OAuth client; see docs/YOUTUBE.md) or published
anything. The soundtrack is an instrumental with
**no vocals** and there is no narration voice unless a provider is connected; a sung birthday song needs a music provider that is not connected. Search, trend, keyword and audience data
are **unavailable** (nothing is estimated), and titles and descriptions are plain template drafts.

| Phase | State | Notes |
|---|---|---|
| 1 Foundation | ✅ built and tested | Acceptance criteria met on Linux; the Windows installer and tests run green on a clean GitHub Windows machine (see evidence below), not yet on your PC. |
| 2 Real video production | ✅ built and tested (local assets) | One validated demo video produced; sung music and AI art need providers; drafting is done by the AI brain (Phase 8) |
| 3 Research and SEO | ✅ built and tested (no data source) | Planning, source register, safe fetcher, metadata and thumbnail checks, approval gate; trend/keyword data unavailable |
| 4 YouTube integration | 🟨 built, **mock-tested only** | Sign-in, identity confirmation, resumable upload, thumbnail, scheduling, read-back verification, quota ledger; needs your OAuth client and a first real run (docs/YOUTUBE.md) |
| 5 Analytics and community | 🟨 built, **mock-tested only** | Daily collection of totals, daily figures and retention with source and time; unavailable figures carry YouTube's reason, never zero; comment retrieval with fixed English-only rules (`rules-v1`); template thank-yous for plain praise only; owner-approved replies, read back; Performance and Comments pages. Needs the Phase 4 Google setup plus the analytics and comments permissions (docs/ANALYTICS.md) |
| 6 Management and improvement | ✅ built and tested (no real data yet) | Lessons mined from finished workflows and driven through the board by you; lessons recorded on later tasks; experiments (seeded random arms, no test before the planned sample, permutation test, your decision); video comparisons; a real learning phase in the daily cycle. No real experiment has run; no language-model narrative (docs/IMPROVEMENT.md) |
| 7 Controlled autonomy | ✅ built and tested (restart-verified on Linux and on a clean GitHub Windows machine) | Persistent scheduler with catch-up, daily operating cycle, alerts, worker heartbeat, health checks, gated autonomous upload queueing; no e-mail/phone alerts |
| 8 The AI brain | ✅ built and tested (stand-in program on Linux and on a clean GitHub Windows machine; one real end-to-end run on Linux; the real Claude Code has not run on Windows) | Claude Code on your computer drafts briefs, lyrics/scripts and ideas and assists manager review; a conductor carries a video to your approval and stops at your decisions; every call recorded with its prompt; daily allowance, plan-terms confirmation, connection test; AI Brain page (docs/AI_BRAIN.md) |
| 9 Connections and CI | ✅ built and tested | Connections page (encrypted in-dashboard keys, read-only tests, honest states); GitHub Actions on Linux and Windows (install, lint, tests, dashboard build, real processes, the Windows installer); only ElevenLabs has a test, YouTube still needs its Google setup |

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

## Phase 8 acceptance criteria (the AI brain)

> The company's writing and its managers' reviews are done by Claude Code on the owner's computer, with no API key; a video started with the AI company reaches the owner's approval without anyone
> running a step by hand; and nothing the model says can approve, publish, pay or change a setting.

| Criterion | Evidence |
|---|---|
| Uses the owner's computer and account, not a key | The program is run with `-p`, no tools, an empty folder, the prompt on standard input, and `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` / cloud switches removed from its environment (`test_claude_code.py`, 19). Against the real program: connection test and a real call pass (`test_claude_code_live.py`) |
| Cannot act, only write | Tool-free by construction and by a self-test that fails if it reports a tool; output is parsed, schema-checked, then subjected to the same rules as a person's work; no code path lets it change a permission, gate, budget or setting |
| Refuses to run until you allow it | Readiness: program present, **your plan-terms confirmation**, no emergency pause, a **daily allowance above 0** (default 0), allowance not used up; a role without the model tool is refused (`test_ai_gateway.py`, 25) |
| A video reaches your approval by itself | `test_ai_flow.py` / `test_ai_conductor.py`: idea → AI brief → manager review → AI lyrics → manager review → music → scenes → captions → render → thumbnail → QA → rights → CEO re-check → metadata → READY → publication request, through the real queue and FFmpeg; every task of the chain COMPLETED by a manager |
| Stops where a person must decide | Tested stops: failed QA (corrections opened), rights verdict not PASS, CEO re-check disagrees, medicine → **human medical review first** (never READY before it), publication approval asked once and never granted or re-asked after a "no"; a draft returned twice, an unusable review, an unavailable brain (parked with the reason, resumed by itself, no attempt used up) |
| Everything recorded | `ai_generations` row + file with the exact prompt and answer (secrets redacted) per call; a usage record per call (category model, "included in subscription"); the lessons in force are put into the prompt and recorded on the task |
| Against the real model | `tests/test_ai_live.py` (opt-in): the real Claude Code wrote a brief (approved by its review), wrote lyrics (one correction round for line length, reviewed OK) and the company built, checked and rights-cleared the video to READY with the publication request pending: **6 model calls**. An earlier run found two real defects, both fixed and tested: the reviewer demanded a sung vocal that cannot be produced and nit-picked new points each round (it escalated to the owner, as designed), and the review notes limit was too small |
| API and dashboard | `test_api_ai.py` (8); the AI Brain page (readiness checklist, connection test, model/effort, activity with the exact prompt), "Start with the AI company" on approved ideas, "What happens next" on the video page |

**Not verified:** Windows; the real not-signed-in and usage-limit messages; how long your plan lasts under daily use; the quality of what a real model writes over many videos (one run, judged by eye).
The managers' review of a draft is a second reading by the same kind of model, not independent assurance.

**Mutation checks** (each safety rule broken on purpose, a test must fail): 33 mutants over the gateway, review, conductor, drafting and prompts. First pass: 26 killed, 7 survived. Five were weak tests (assertions that were true whatever the code did, for example checking the writer's own prompt for a phrase that prompt always contains) and two were unreachable-in-practice guards. The five tests were rewritten and re-run: **32 of 33 killed**. The survivor is the "ask for publication approval only once" guard, which is redundant with the video's own `owner_approval_status` check (an equivalent mutant); it is kept as defence in depth.

## Phase 5 acceptance criteria

> The dashboard displays actual retrieved data and clearly labels unavailable metrics.

Met **for data retrieved from a mock of Google's documented API**. No real YouTube data has ever been retrieved, so "actual" here means "whatever the
API returns when it is run", and the first real collection is the live test (docs/ANALYTICS.md lists what it confirms). What was verified:

| Behaviour | Evidence |
|---|---|
| Totals, daily figures and retention stored with source, retrieval time and the saved raw response; recollecting updates in place | `test_analytics_collect.py` (15) |
| A figure YouTube refuses, has no rows for, or returns as null has **no row** and a recorded reason; the dashboard shows `unavailable` plus the reason, and revenue is always unavailable | same; `test_api_community.py`; seen in a browser on seeded data |
| Without the analytics permission nothing is requested, and the gap says where to switch it on; asking for extra permission is a separate sign-in, keeps the confirmed channel, saves nothing if Google grants less | `test_youtube_permissions.py` |
| Retention described by measured marks and the steepest loss; too few points is *unavailable*; no claim about why viewers left | `test_analytics_retention.py` (8) |
| Comments stored cleaned and quoted, flagged when they try to give instructions, sorted by `rules-v1`; safety, copyright, medical and personal-data comments are never given a reply; non-English text is marked for a person | `test_community_triage.py` (26), `test_community_comments.py` (7) |
| Replies: owner approves the exact text; hash re-checked at send; development mode, pause, missing permission, quota and a changed policy each park the reply; a lost answer never produces a second reply; success only from a read-back; automatic thank-yous only under every condition | `test_community_replies.py` (25); nine deliberate code mutations across replies, triage and collection: eight made a test fail at once, and the ninth (a missing value stored as zero) survived until a test for null values was added |
| CEO verification: department reports carry re-runnable counts (connections, figures retrieved, figures unavailable, comments, escalations, drafts, replies posted); an inflated claim is contradicted; "YouTube not connected" is replaced once a channel is connected | `test_community_reporting.py` (5) |
| The daily cycle's monitoring phase reports what was collected and what waits for a person (and counts it as needing you) | same |
| In a browser (seeded with mock-Google data): Performance, Comments and Settings render; 390 px width no overflow; no console errors; smoke test passes on a fresh install | Playwright runs |

## Phase 6 acceptance criteria

> A verified lesson from one completed workflow can be approved, stored, communicated to relevant workers, and referenced in a later task.

**Met**, in one tested sequence (`test_a_verified_lesson_is_approved_stored_sent_to_relevant_workers_and_referenced_in_a_later_task`) and in a real browser on seeded data:

| Step | Evidence |
|---|---|
| A completed workflow (cleared video whose QA failed once and was corrected) yields an OBSERVED, low-confidence, channel-specific lesson with `db_record` evidence | `test_improvement_lessons.py` (13) |
| Before approval nothing is an instruction: a task started then references nothing; an observation cannot be activated | same |
| Investigated by the owner, **verified by the CEO role re-reading the evidence** (deleted records make verification fail), approved by the owner, which publishes a new board version | same; `test_api_improvement.py` |
| Communicated to the relevant workers only: a production worker on that channel acknowledges it and the task records it (`lessons_applied`, in the result and in the handler's context); another channel and another department receive nothing | same |
| Referenced in a later task, visibly: the Organization page and a video's task list show "lessons in force"; an adopted experiment is referenced the same way | same; `test_improvement_experiments.py` |
| Experiments: seeded random arms before publication; strict measurement; **no p-value below the planned sample**; exact/seeded permutation test checked against a brute-force count; attrition and repeated looks disclosed; confidence capped at medium; owner-only decisions; adoption becomes an instruction, rejection a recorded failed experiment, repeat a new plan | `test_improvement_experiments.py` (24), `test_improvement_stats.py` (8) |
| Comparisons never mix channels, rank only complete figures, and give the reason for the rest | `test_improvement_compare.py` (5) |
| Daily cycle: queues the mining task once a day, counts what waits for you, flags experiments ready to analyse but never analyses by itself; department claims the CEO re-runs | `test_improvement_cycle.py` (3) |
| Fourteen deliberate code mutations across lessons, statistics, measurement, assignment and decisions: twelve made a test fail at once; two survived (a published video still enrollable through a private upload; a mutation that was not effective) and were closed by a new test | mutation log in the session; both now caught |
| In a browser (seeded data): Lessons (verify → approve), Experiments (analysis, decision), Compare, board, a video's experiment card; no console errors; no overflow at 390 px; smoke test passes on a fresh install | Playwright runs |

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
| Backend unit + API tests (`python -m pytest`) | **878 passed, 3 skipped** (the skipped are the opt-in live tests that use your Claude account; about 2 min with `-n 4`), run locally with FFmpeg 6.1 and again with FFmpeg 7.0.2 (the newer kind Windows installs) |
| Lint (`ruff check app tests migrations`) | clean |
| Migration drift (`alembic check`) and up/down/up | clean |
| Frontend typecheck (`tsc --noEmit`) and production build | pass (18 routes, including `/performance`, `/comments`, `/learning` and `/ai`) |
| Browser smoke test (`frontend/e2e/smoke.cjs`, Chromium, fresh install) | **passed**: first-run setup, all 17 pages, report generation, pause banner < 3 s, resume, audit verify, mobile no-overflow, sign-out guard |
| Live concurrency stress (API + worker + 3rd process) | 150 concurrent writes, 60 tasks, 80 reads: 0 errors, audit chain intact over 352 events |
| Video page in a real browser (demo data) | player, lyrics, rights report, 19 QA rows, provenance table render; no horizontal overflow, no console errors |
| Real processes, AI company (`python scripts/verify_ai_stack.py`: real API + worker + FFmpeg, a stand-in `claude`, driven only through the API) | **9 of 9 checks passed**: not ready until you confirm and set an allowance; connection test; start with the AI company; the worker's own housekeeping reviews and conducts; READY with the publication request pending; 4 recorded calls; every task COMPLETED by a manager |
| Restart check (`python scripts/verify_restart.py`) after the housekeeping change | passed (8 of 8) |
| Migration `0008` | `alembic check` clean; down/up/down cycle clean |
| **GitHub Actions, clean Windows machine** (`windows-latest`, commit `a04bf3d`, run 40350110130) | **all 4 Windows jobs green**: `setup.ps1` installer + prerequisite check; dashboard typecheck and production build; the whole backend suite (878 passed, 3 skipped); the real API + worker processes (restart survival, and the AI company end to end with a stand-in `claude.cmd`) |
| **GitHub Actions, clean Linux machine** (`ubuntu-latest`, same run) | **all 3 Linux jobs green**: backend lint + suite, dashboard build, the real API + worker processes (restart survival, the AI company end to end). All 7 jobs of the run passed; the code is identical to the branch head (later commits only touch this file) |
| Clean-clone install via `scripts/setup.sh` | see "Install verification" below |

Where the tests live: `backend/tests/` — `test_org_security`, `test_audit_settings`, `test_tasks`, `test_board`,
`test_reports`, `test_gate_videos`, `test_finance_backup_runner`, `test_api`, `test_migrations_docs`, `test_config`,
`test_production_core`, `test_render_qa`, `test_originality`, `test_pipeline`, `test_elevenlabs`, `test_api_production`, `test_demo`,
`test_research_safety`, `test_research_ideas`, `test_research_seo`, `test_research_workflow`, `test_api_research`,
`test_youtube_connection`, `test_youtube_upload`, `test_youtube_selftest`, `test_api_youtube`,
`test_ops_scheduler`, `test_ops_health_cycle`, `test_api_ops`; `scripts/verify_restart.py` is the live check.
Mocks are used only in tests (no provider is called anywhere); the ElevenLabs client has only ever run against a mock transport.

## Faults found by running on a clean Windows machine

Everything below passed on Linux. Each was found by the GitHub Windows job, root-caused, fixed, and covered by a test that fails without the fix.

| Fault | What it did | Fix |
|---|---|---|
| **Write endpoints committed after the response was sent** (FastAPI runs a dependency's exit code after the response by default) | The request right after sign-in could answer "Not signed in": the browser had the cookie before the session row was committed. It could also happen on Linux, under load | Every write endpoint commits before the response (`Depends(get_wdb, scope="function")`); `test_commit_before_response.py` counts the committed rows at the moment the response starts |
| **Soundtrack true peak above the limit** | The Windows FFmpeg (7+) left `loudnorm` + AAC at up to +2.5 dBFS; the quality check correctly failed every video, so none reached READY | Rendering encodes the soundtrack first, **measures the encoded file's true peak**, takes level off and re-encodes (up to 4 passes) until it is at or below -1.0 dBFS, then builds the video with that exact audio. The quality check is unchanged and still the judge |
| **A timed-out Claude Code call left its real process running** | On Windows the program is a `.cmd` launcher; stopping only the launcher left the real process (and the output pipes) alive | The program runs in its own process group and the whole tree is stopped on timeout (`taskkill /T` on Windows, `killpg` elsewhere) |
| **The launcher served an old dashboard after an update** | New pages (such as Connections) returned 404 until someone rebuilt by hand | The launcher rebuilds when any dashboard source is newer than the build |

Also changed because of CI: the test database is migrated once per run and copied per test (the Windows suite was too slow), per-test timeouts, and the Linux FFmpeg install is bounded and retried (a stalled package mirror once hung a job for over ten minutes).

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

## Implemented (Phase 5)

Metric, gap, retention, comment and reply-draft records (migration 0006); incremental Google permissions (analytics, comments); collection of totals, daily
figures and retention; retention analysis; comment retrieval and `rules-v1` triage; template thank-yous for praise only; approval-gated, read-back-verified
replies with an optional strict auto-policy; scheduled `analytics.collect` and `community.collect_comments`; department-report claims for analytics and community;
the monitoring phase of the daily cycle; Performance and Comments pages and Settings controls. Details: [docs/ANALYTICS.md](docs/ANALYTICS.md).

## Channel pages (fixed after Phase 6)

The three channel pages originally kept a hard-coded "unavailable" for performance and audience feedback and read a connection flag that Phase 4 never sets, so they said "YouTube not
connected" even when it was. They now show the real connection state, retrieved cumulative views per video, a feedback picture, and recommendations derived from the channel's own records
(comments needing a person, drafts awaiting approval, a retention hypothesis labelled as one), or the exact reason there is nothing to show (`test_the_channel_page_shows_real_connection_performance_and_feedback_or_the_exact_reason_there_is_none`; checked in a browser).

## Implemented (Phase 6)

Experiment, arm and workflow-lesson records (migration 0007) and `lessons_applied` on tasks; lesson mining from QA and originality history; owner-driven board actions with
the CEO's real verification; experiments with seeded assignment, strict measurement, a permutation test, decisions that act on the board; video comparisons; the daily
cycle's learning phase; department claims; the Learning page, a video's experiment card and "lessons in force" on tasks. Details: [docs/IMPROVEMENT.md](docs/IMPROVEMENT.md).

## Implemented (Phase 7)

Persistent schedules with catch-up and skip policies, idempotent slots, owner time zone; the worker's heartbeat; alerts (de-duplicated,
escalating, self-resolving); a hierarchy of health checks; the daily operating cycle; gated autonomous upload queueing; the Operations page
and a badge on every page; `ops-status`; `scripts/verify_restart.py`. Details: [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Not implemented / known limitations

* **YouTube has never been contacted.** Everything in Phase 4 is tested against a mock of Google's documented behaviour. Unconfirmed
  until your first real run: that the resumable-upload and thumbnail calls match the live API, the real quota costs, whether public
  uploads stay public (an unverified API project is *reported* to have them locked private), whether tokens last, and the loopback
  redirect. The same applies to Phase 5: the Analytics request shape, which figures a channel is offered (impressions, click-through rate), whether the
  YouTube Analytics API must be enabled separately, comment reading and replying under `youtube.force-ssl`, and Analytics quota behaviour are all unconfirmed
  (docs/ANALYTICS.md). No post-upload metadata edits, playlists or deletes (other than the self-test's own video).
* **No other provider has been run live either.** The ElevenLabs text-to-speech client exists but was written from public API
  knowledge and tested against a mock transport only (`verified_live` stays false). There is **no** adapter for ElevenLabs
  music (request format not verified), image/video generation, Canva or YouTube. (The language model is Claude Code on your computer: Phase 8.) `docs/PROVIDERS.md`
  grades every provider fact as Observed / Reported / Unknown.
* **No vocals.** The generated soundtrack is an algorithmic instrumental. A plan that says "sung" is flagged
  `VOCALS_MISSING` and cannot pass review.
* **Creative steps are manual unless you start a video with the AI company.** Then the AI brain drafts them (always a draft until a manager reviews it); *Start by hand* keeps them manual.
  The demo lyrics are sample text and are labelled DEMO. What a real model writes is only judged by eye so far (one run).
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
* **Windows is verified on GitHub's clean Windows machine, not on your PC.** `setup.ps1`, the dashboard build, the whole test suite and the real API + worker processes pass there. Still unverified on Windows:
  `start.bat` with a double-click, the real Claude Code program (its `.cmd` launcher is imitated by a stand-in in CI), real FFmpeg-from-winget on your machine, and anything that needs your sign-ins.
* **Alerts are in the dashboard and CLI only.** No e-mail, SMS or phone notification is configured. A stopped worker is noticed by the dashboard (banner within two minutes) and not by anything outside it.
* The scheduler runs inside the worker, so it only works while the launcher is running; there is no Windows service or Task Scheduler entry yet.
* The reporting chain is deterministic. The AI brain does not write reports, lessons or experiment plans. Only the creative director, the script and lyrics writer, the content strategist
  and the managers' draft reviews use it; every other role still defines permissions and duties only.
* **No real experiment has run.** The first needs a connected channel and at least ten public videos (five per arm). If YouTube omits zero-view days from its daily report, small
  channels' windows will stay "incomplete" until the first real collection shows what it returns (docs/IMPROVEMENT.md). Lessons are mined only from QA and originality history;
  nothing proposes, words or judges lessons with the AI brain, and the software never changes its own thresholds because of a lesson.
* **Revenue is never collected** (shown as unavailable). Demographics, traffic sources and competitor figures are not collected. Comment sorting is fixed rules,
  English only and blunt; there is no language-model sentiment or reply wording, and a comment edited or deleted on YouTube after collection is not tracked.
  No moderation action (hide, report, ban) exists; a reply is the only write.
* PostgreSQL is a configuration change in principle but has not been run.
* Artifacts are not included in database backups (copy `data/` for a full backup).

## Needs you (owner decisions / credentials)

1. **Windows run-through on your PC:** run `scripts\windows\setup.ps1` then `start.bat` and tell me what fails (the same steps pass on GitHub's clean Windows machine, so a failure would be specific to your PC).
2. **(Optional now, needed for Phase 3 to be more than templates)** a source of search/trend data you are allowed to use
   (for example an export from a tool you pay for) or the willingness to record observations by hand on ideas.
3. **Provider decisions** (each needs a key, your confirmation of the plan's terms in Settings → Providers, and your
   approval of one small test generation before the app will spend anything):
   * **the AI brain (Claude Code on this computer, no key):** install Claude Code and sign in; Settings → Providers → confirm that your plan allows it; Settings → limits: set *Language-model requests per day* and
     *Provider requests per day* above 0 (a video needs about 4 to 8); AI Brain → Run the connection test; then Content Plan → Start with the AI company. Tell me what the first run does on **Windows** and what the
     real sign-in or usage-limit messages say;
   * ElevenLabs: an API key (paste it under *Connections*, then press *Test*) and a check that your plan's terms allow commercial use via the API. Music is the route to a
     *sung* song; text-to-speech is for narration only;
   * an image source (provider, licensed stock, or your own artwork) to replace the procedural placeholders.
4. **Phase 4 (blocks every real upload):** the Google Cloud setup in [docs/YOUTUBE.md](docs/YOUTUBE.md): project, YouTube Data API, consent
   screen, a Desktop OAuth client (paste it under *Connections*), your confirmation of the API terms, signing in to each channel and answering whether it is made
   for kids, Approval-first mode, then `youtube-selftest`.
5. **Phase 5 (performance data and comments):** also enable *YouTube Analytics API* in the same Google Cloud project, then press *Allow* next to
   *Performance data* and *Comments* for each channel in Settings → YouTube, and run *Collect performance now* once. If you want automatic thank-yous, choose
   that policy in Settings → Autonomy (Controlled autonomy, listed non-medicine channels only). Tell me what the first collection reports as unavailable.
6. **Phase 6 (learning):** check *Learning → Lessons* now and then: finished workflows propose lessons that only you can turn into instructions. To run an experiment, plan it
   under *Learning → Experiments* before the videos are published (at least five per arm), add each video while it is still unpublished, and make treatment videos exactly as their
   page says. Nothing can be concluded until ten public videos have a full first week of figures.
7. Spending limits (default $0) and which channels you ever want in Controlled autonomy.
8. A named human reviewer process for medicine videos (today: you).

## Install verification

Re-run after Phase 9 on Linux from a **clean clone of the pushed branch** (an empty directory, nothing local, commit `70a01e6`): `scripts/setup.sh` (about 99 s, including the dashboard
build) → `python -m pytest -n 4` (**878 passed, 3 skipped**) → `launcher.py --check` (all prerequisites present). On a clean **Windows** machine (GitHub's `windows-latest`, commits `9cb02b3`, `70a01e6` and `a04bf3d`) the same path
ran green: `scripts\windows\setup.ps1` → prerequisite check, the dashboard build, the whole suite, restart survival and the AI company end to end. That is a fresh GitHub machine, not your PC. The first install check (end of Phase 1) found and fixed two real bugs the unit tests had missed:
comments after values in `.env.example` crashed the first start, and blank placeholder API keys were reported as "configured"; both have regression tests. The Windows run found four more (see "Faults found by running on a clean Windows machine").
