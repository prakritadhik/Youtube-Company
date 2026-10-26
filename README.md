# AI Media Company

A locally-run, multi-agent platform for managing three YouTube channels — **Happy Birthday Songs**,
**Medicine Information** and **AI Story Videos** — with an AI CEO that verifies what its managers claim, a
shared intelligence board that turns *verified* lessons into instructions, hard publication gates, budget
controls and an owner dashboard at <http://localhost:3000>.

> **Honest status:** Phases 1 (management foundation), 2 (a real, validated local video pipeline), 3 (research,
> planning and packaging), 4 (YouTube integration), 5 (performance data and comments) and 6 (lessons, experiments and comparisons) are built and tested. YouTube upload, performance collection and comment replies are built but **mock-tested only** (none of it has touched the real YouTube; see
> [docs/YOUTUBE.md](docs/YOUTUBE.md) and [docs/ANALYTICS.md](docs/ANALYTICS.md)). The scheduler, daily operating cycle, alerts and health checks **are** built (restart-verified on Linux). Nothing here has published to
> YouTube. **The AI brain is Claude Code on your own computer** (signed in with your Claude account, no API key): it writes briefs, lyrics/scripts and ideas, and the
> managers' reviews of those drafts are model-assisted ([docs/AI_BRAIN.md](docs/AI_BRAIN.md)). It is verified against a stand-in program and against the real one on the build
> machine, **not on Windows**, and it needs you to confirm that your plan allows it. The soundtrack is an algorithmic **instrumental
> with no vocals**; trend, keyword and audience data are **unavailable** (nothing is estimated); titles and descriptions are
> plain template drafts. Sung songs and AI illustration need providers that are not
> connected. See [STATUS.md](STATUS.md), [docs/PRODUCTION.md](docs/PRODUCTION.md) and [docs/RESEARCH.md](docs/RESEARCH.md).

## What works today (Phase 1)

| Area | What exists |
|---|---|
| Organization | 50 roles in a real reporting tree (CEO → COO → 7 department managers → 41 specialists), stored in the database, each with responsibilities, inputs, outputs, acceptance criteria and a tool allowlist → [AGENT_HIERARCHY.md](docs/AGENT_HIERARCHY.md) |
| Task queue | Persistent, restart-safe jobs with dependencies, bounded exponential-backoff retries, leases, crash recovery, idempotency keys, emergency pause. A worker can only *submit*; its manager verifies the evidence (file hashes, saved API responses, test results) before the task is `COMPLETED` |
| Intelligence board | Lessons move `OBSERVED → UNDER INVESTIGATION → VERIFIED → APPROVED FOR TESTING / ACTIVE INSTRUCTION` (or `REJECTED/EXPIRED`). Only the CEO verifies (after re-checking evidence); editing verified content withdraws it; channel lessons never leak across channels; workers must acknowledge the current board version before starting work |
| CEO verification | Manager reports are consolidated by the COO; the CEO re-runs every claim against the records. Contradicted claims are rejected and shown as disagreements; missing data is shown as *unavailable*, never zero |
| Publication gate | Fail-closed backend rule: file/thumbnail/metadata present, QA passed, originality `PASS`, CEO rights sign-off, no unlicensed assets, **human** medical review for the medicine channel, operating mode and owner approval. Rights and medical conditions can never be waived |
| Owner control | Operating modes (Development / Approval-first / Controlled autonomy / Paused), spending limits (default $0 = no paid use), emergency pause, approval center, hash-chained audit log, backup/restore |
| Production | Brief → lyrics → soundtrack → scenes → captions → **FFmpeg render** → thumbnail → **19-check QA** → originality & rights review → CEO re-check, as a manager-verified task chain. Every asset carries provenance and a licence; failed QA opens correction tasks. `python -m app.cli demo-production` makes a labelled demo video in ~20 s → [PRODUCTION.md](docs/PRODUCTION.md) |
| Research & packaging | Content plan with an explainable score (no invented demand data), idea assessment against the channel's own catalogue and the live provider registry, a source register that treats outside text as data, a safe web fetcher, metadata checks (limits, stuffing, clickbait, medical overclaims, accuracy against the video's own material, chapters only from measured time), thumbnail-concept checks, manager approval, and a publication gate that requires approved metadata → [RESEARCH.md](docs/RESEARCH.md) |
| YouTube | OAuth (PKCE) sign-in per channel with owner confirmation of identity and audience, encrypted tokens, resumable upload that resumes instead of duplicating, thumbnail, scheduling, success only from a read-back, a quota ledger, and a one-minute private `youtube-selftest` → [YOUTUBE.md](docs/YOUTUBE.md). **Never run against the real YouTube yet** |
| Performance and comments | Daily collection of views, watch time, retention and similar figures with their source and time, where an unavailable figure shows YouTube's reason and never zero; public comments sorted by fixed, English-only rules (safety, copyright and medical comments are escalated, never answered); reply drafts that need your approval of the exact text, a thank-you template only for plain praise, an optional automatic thank-you under strict conditions, one reply per comment, success only from a read-back → [ANALYTICS.md](docs/ANALYTICS.md) |
| Learning | Lessons mined from finished workflows (a QA check that failed and was corrected, an originality finding that was resolved) with evidence the CEO re-checks, driven through the board by you, recorded on every later task of that channel; experiments with seeded random arms, no test before the planned sample, an honest permutation test and your decision; like-for-like video comparisons → [IMPROVEMENT.md](docs/IMPROVEMENT.md) |
| Operations | A persistent scheduler (catch-up, idempotent slots, your time zone), an hourly health check, a daily operating cycle that only does what is safe and says when nothing happened, alerts that de-duplicate and clear themselves, a worker heartbeat; Operations page + badge → [OPERATIONS.md](docs/OPERATIONS.md) |
| AI brain | *Content Plan → Start with the AI company*: Claude Code on your computer (no API key, no tools) writes the brief and the lyrics/script, managers review them (bounded), the music/scenes/captions/render/QA/rights steps run, the CEO re-checks the rights evidence, clean metadata is approved, and the video arrives at **READY with a request for your approval**. It stops at every decision that is yours (failed QA, rights concerns, medical review, publication). Every call is recorded with its exact prompt → [AI_BRAIN.md](docs/AI_BRAIN.md) |
| Dashboard | Executive overview, three channel pages, content plan, organization, intelligence board, approvals, performance, comments, operations, originality & rights, executive reports, learning, settings, diagnostics. Owner sees the verified executive layer by default; raw detail is a separate, audited, owner-only layer |

## Requirements

* Windows 10/11 (primary target), macOS or Linux
* Python 3.11+ · Node.js 20+ · FFmpeg (needed for video rendering)
* No GPU is required or assumed.

## Install and start

**Windows** (the scripts were written on Linux and are **not yet tested on Windows**; see [TROUBLESHOOTING](docs/TROUBLESHOOTING.md)):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1   # once
scripts\windows\start.bat                                            # every time
```

**Linux / macOS** (tested):

```bash
scripts/setup.sh        # once
python3 launcher.py     # every time; Ctrl+C stops everything
```

Open <http://localhost:3000>. On first run the page asks you to create the owner account (works once, from this
computer only). `python -m app.cli doctor` (or `scripts\windows\doctor.bat`) reports what your computer has.

Everything runs on `127.0.0.1`. It only runs while your computer is on, awake and the launcher is open.

## Using the dashboard

Start on **Overview**: what the CEO verified, what is waiting for you, risks, costs and what is unavailable.
Decisions go through **Approvals**. **Settings** holds the mode, spending limits and the autonomy list.
The red **Emergency pause** button stops new work, paid generation, uploads and replies immediately;
work already running finishes its current step and a request already sent to a provider cannot be recalled
([OPERATIONS.md](docs/OPERATIONS.md)). **Diagnostics** shows raw tasks, CEO check detail and the audit log.

## Safety defaults

* Starts in **DEVELOPMENT** mode: real publishing and public replies are disabled.
* Spending limits start at **$0**; any paid operation is refused until you set limits.
* Comment replies are **draft-only** until you authorise a policy.
* The medicine channel **always** requires human medical review and never auto-publishes.
* No originality or similarity check is treated as proof of copyright safety.

## Repository layout

```
backend/     FastAPI + SQLAlchemy + Alembic (app/, migrations/, tests/)
frontend/    Next.js 15 + TypeScript + Tailwind dashboard (+ e2e/smoke.cjs)
launcher.py  starts API + worker + dashboard (cross-platform)
scripts/     setup.sh, windows/*.ps1|*.bat
docs/        ENVIRONMENT, IMPLEMENTATION_PLAN, PRODUCTION, RESEARCH, YOUTUBE, ANALYTICS, IMPROVEMENT, SECURITY, OPERATIONS, TROUBLESHOOTING, PROVIDERS, AGENT_HIERARCHY
data/        (git-ignored) database, artifacts, backups, logs
```

## Commands

```bash
cd backend && source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m app.cli --help                       # migrate, seed, setup, api, worker, doctor, backup, restore, ...
python -m pytest -n 4                          # 758 backend tests (~2 min; omit -n 4 to run serially)
ruff check app tests migrations
cd ../frontend && npx tsc --noEmit && npm run build
node e2e/smoke.cjs                             # browser smoke test (needs Playwright; fresh data dir only)
```

## Configuration

**Connecting services:** open **Connections** in the dashboard. Paste the ElevenLabs key or the Google OAuth client there (stored encrypted, used at once, no restart,
never shown again); each service has a plain list of steps and, where one exists, a read-only Test button. Nothing needs editing in `.env`; its keys still work and are
the fallback. Subscriptions (Claude Pro, ElevenLabs Pro, Canva Pro) do not automatically include API access or commercial rights —
[PROVIDERS.md](docs/PROVIDERS.md) lists what must be verified before each is enabled. Having the code on GitHub does not connect any account: each connection needs
*your* login or key, which only you can create.

**Proof that it runs on clean machines:** every push is installed, linted, tested, built and run (real API and worker processes) on **Linux and Windows** by GitHub
(`.github/workflows/ci.yml`; see the Actions tab).

## Roadmap

| Phase | Scope | State |
|---|---|---|
| 1 | Foundation (this release) | **built, tested** |
| 2 | Real video production: briefs, scripts, assets/provenance, FFmpeg, captions, thumbnails, originality review | **built, tested** (local assets; sung music and AI art need providers; drafting by the AI brain) |
| 3 | Research, SEO, content planning | **built, tested** (no data source connected; trends and keywords unavailable; the AI brain proposes ideas but cannot look anything up) |
| 4 | YouTube OAuth, upload, scheduling, quota | **built, mock-tested only**; needs your Google Cloud OAuth client and a first real run ([YOUTUBE.md](docs/YOUTUBE.md)) |
| 5 | Analytics, comments, reply drafting | **built, mock-tested only**; needs the same Google setup as Phase 4 plus the analytics and comments permissions ([ANALYTICS.md](docs/ANALYTICS.md)) |
| 6 | Reporting chain, lessons, experiments, comparisons | **built, tested** (no real experiment has run: needs real published videos; the AI brain does not write reports or lessons) → [IMPROVEMENT.md](docs/IMPROVEMENT.md) |
| 7 | Scheduler, daily cycle, health, alerts | **built, tested**; restart-verified with real processes on Linux (`scripts/verify_restart.py`); Windows untested |
| 8 | The AI brain: Claude Code on your computer drafts and reviews; the conductor carries a video to your approval | **built, tested** against a stand-in program and, opt-in, the real one; needs your plan-terms confirmation and a daily allowance; Windows untested → [AI_BRAIN.md](docs/AI_BRAIN.md) |
