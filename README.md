# AI Media Company

A locally-run, multi-agent platform for managing three YouTube channels — **Happy Birthday Songs**,
**Medicine Information** and **AI Story Videos** — with an AI CEO that verifies what its managers claim, a
shared intelligence board that turns *verified* lessons into instructions, hard publication gates, budget
controls and an owner dashboard at <http://localhost:3000>.

> **Honest status:** Phase 1 (the management foundation) is built and tested. Video production, research/SEO,
> YouTube upload, analytics/comments and the autonomous scheduler are **not built yet**. Nothing in this
> repository publishes to YouTube, calls an AI provider, or renders a video today. See [STATUS.md](STATUS.md).

## What works today (Phase 1)

| Area | What exists |
|---|---|
| Organization | 50 roles in a real reporting tree (CEO → COO → 7 department managers → 41 specialists), stored in the database, each with responsibilities, inputs, outputs, acceptance criteria and a tool allowlist → [AGENT_HIERARCHY.md](docs/AGENT_HIERARCHY.md) |
| Task queue | Persistent, restart-safe jobs with dependencies, bounded exponential-backoff retries, leases, crash recovery, idempotency keys, emergency pause. A worker can only *submit*; its manager verifies the evidence (file hashes, saved API responses, test results) before the task is `COMPLETED` |
| Intelligence board | Lessons move `OBSERVED → UNDER INVESTIGATION → VERIFIED → APPROVED FOR TESTING / ACTIVE INSTRUCTION` (or `REJECTED/EXPIRED`). Only the CEO verifies (after re-checking evidence); editing verified content withdraws it; channel lessons never leak across channels; workers must acknowledge the current board version before starting work |
| CEO verification | Manager reports are consolidated by the COO; the CEO re-runs every claim against the records. Contradicted claims are rejected and shown as disagreements; missing data is shown as *unavailable*, never zero |
| Publication gate | Fail-closed backend rule: file/thumbnail/metadata present, QA passed, originality `PASS`, CEO rights sign-off, no unlicensed assets, **human** medical review for the medicine channel, operating mode and owner approval. Rights and medical conditions can never be waived |
| Owner control | Operating modes (Development / Approval-first / Controlled autonomy / Paused), spending limits (default $0 = no paid use), emergency pause, approval center, hash-chained audit log, backup/restore |
| Dashboard | Executive overview, three channel pages, organization, intelligence board, approvals, originality & rights, executive reports, settings, diagnostics. Owner sees the verified executive layer by default; raw detail is a separate, audited, owner-only layer |

## Requirements

* Windows 10/11 (primary target), macOS or Linux
* Python 3.11+ · Node.js 20+ · (FFmpeg — only needed from Phase 2)
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
docs/        ENVIRONMENT, IMPLEMENTATION_PLAN, SECURITY, OPERATIONS, TROUBLESHOOTING, PROVIDERS, AGENT_HIERARCHY
data/        (git-ignored) database, artifacts, backups, logs
```

## Commands

```bash
cd backend && source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m app.cli --help                       # migrate, seed, setup, api, worker, doctor, backup, restore, ...
python -m pytest                               # 179 backend tests
ruff check app tests migrations
cd ../frontend && npx tsc --noEmit && npm run build
node e2e/smoke.cjs                             # browser smoke test (needs Playwright; fresh data dir only)
```

## Configuration

Copy [.env.example](.env.example) to `.env`. Provider keys are placeholders; they are not used until the phase that
needs them. Subscriptions (Claude Pro, ElevenLabs Pro, Canva Pro) do not automatically include API access or
commercial rights — [PROVIDERS.md](docs/PROVIDERS.md) lists what must be verified before each is enabled.

## Roadmap

| Phase | Scope | State |
|---|---|---|
| 1 | Foundation (this release) | **built, tested** |
| 2 | Real video production: briefs, scripts, assets/provenance, FFmpeg, captions, thumbnails, originality review | not started |
| 3 | Research, SEO, content planning | not started |
| 4 | YouTube OAuth, upload, scheduling, quota | not started (needs your Google Cloud OAuth client) |
| 5 | Analytics, comments, reply drafting | not started |
| 6 | Worker→manager→COO→CEO reporting with LLM narrative, experiments | foundation exists; LLM parts not started |
| 7 | Scheduler, daily cycle, health, alerts | worker/queue/recovery exist; scheduler not started |
