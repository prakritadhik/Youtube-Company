# Operating guide

## Starting and stopping

`python3 launcher.py` (Windows: `scripts\windows\start.bat`) applies pending migrations, starts the API, the worker
and the dashboard, and stops all three on Ctrl+C or when the window is closed. Logs: `data/logs/{api,worker,frontend}.log`.
If any process dies, the launcher stops the others and prints the end of its log.

The platform only runs while the computer is on, awake and the launcher is open. Work in progress survives a
restart: jobs are persisted, leases expire, and recovery re-queues interrupted work (see "What restarts can and
cannot repeat").

## Operating modes

| Mode | Publishing / public replies | Use |
|---|---|---|
| DEVELOPMENT (default) | Disabled | Building and testing |
| APPROVAL-FIRST | Only after you approve each video | First real integration tests |
| CONTROLLED AUTONOMY | Allowed for channels you list, only when every mandatory gate passes | Later, once trusted |
| PAUSED | Nothing external starts | Holding |

Medicine content is never published without a human medical review, in any mode.

## Trying the production pipeline

```
cd backend
python -m app.cli demo-production            # about 20 s; --width/--height/--preset change quality
```

Renders a clearly-labelled DEMO video through the real task chain (needs FFmpeg on PATH). It appears under Birthday
Songs with its QA and rights reports. Demo videos can never be published. Run it against a scratch data folder
(`YTC_DATA_DIR=...`) if you do not want a demo video in your real data.

FFmpeg on Windows: `winget install Gyan.FFmpeg`, then open a new terminal. `python launcher.py --check` prints a note
when it is missing.

## Planning content and packaging a video

* **Content plan** (sidebar): propose an idea per channel. It is assessed immediately. Approve or reject it with a reason;
  approving re-runs the assessment. Ideas that need something that does not exist yet (for example sung vocals) are
  parked, not approved. **Start production** turns an approved idea into a video with its sources attached.
* **Video page → Title, description and tags**: *Draft metadata* makes a template draft from the approved brief and lyrics
  or script; the checks list what must be fixed (cannot be waived) and what needs revising (can be accepted with a recorded
  reason). *Approve* applies it to the video. Editing the title or description afterwards re-blocks the publication gate until
  the new text is approved.
* **Research notes**: add sources, trend observations or audience questions to an idea. A number (views, search volume) is
  accepted only with its own source and date (API). Text pasted from the web is stored as data and never followed.
* Fetching a public page for a source runs as a queue task (`research.fetch_source`) by a role with the web tool; it refuses
  internal addresses and pages that forbid robots (see SECURITY.md).

## YouTube

Setup and the first real test: [YOUTUBE.md](YOUTUBE.md). Day to day: *Settings → YouTube* shows each channel's connection and today's
quota; a video's page has *Queue upload* (private first), the read-back evidence, and the owner's *Abandon*. `python -m app.cli
youtube-status` prints the same offline. **Back up `data/secrets/token.key` along with `data/backups/`**; the key is deliberately not in
the database backup. After a restore on a new machine without the key, reconnect each channel.

## The scheduler, the daily cycle and alerts (Phase 7)

`python launcher.py` starts the API, the **worker** and the dashboard. The worker also runs the scheduler: every 15 seconds it records a
heartbeat and creates tasks for any schedule whose time has come. Nothing needs a browser or an AI chat to stay open; it needs the
computer on and the launcher running.

| Schedule (default) | What it does |
|---|---|
| Health check, hourly | Database integrity, audit-log chain, disk, FFmpeg, worker heartbeat, stuck and blocked tasks, backups, overdue approvals and schedules, YouTube connections and quota, the token key, pause state. Failing checks raise alerts; passing checks clear them |
| Daily operating cycle, 07:30 | Review, research (re-assess ideas whose assessment is over a day old), planning (what waits for your decision), production (manual steps and blocked tasks), quality (open corrections), publishing, monitoring and learning (both *unavailable* for now, and it says so), then the CEO's verified executive report. A quiet day is reported as quiet |
| Database backup, 02:00 | A verified SQLite backup |

Times are read in your time zone (Settings → `timezone`, default `Asia/Kathmandu`). **If the app was off**, a schedule runs its most recent
missed slot once, never every missed one; a new or re-enabled schedule does not owe slots from before it existed. Each slot creates at most
one task (the slot is part of the task's idempotency key), so two workers or a crash cannot run it twice. Change times, turn schedules off, or
*Run now* on the **Operations** page (owner only).

**What the cycle may do by itself** is deliberately small. In *Controlled autonomy* only, for channels you listed, inside your daily
limit, with every publication-gate condition satisfied (medical review, rights, approved metadata ...) it queues an upload with the privacy
you set (default `private`). It cannot approve, publish medicine, spend money or bypass a gate. In any other mode it only lists cleared
videos that wait for you.

**Alerts** show on the Operations page and as a badge on every page. There is **no e-mail or phone notification**: none is configured. The same
problem is one alert with a count; it clears itself when the check passes; acknowledging says you have seen it and it re-opens if it
gets worse. `python -m app.cli ops-status` prints the worker, schedules and alerts without the dashboard. If the **worker stops**, the
dashboard shows a banner within two minutes (a dead worker cannot raise its own alert).

**Prove it on your computer:** `python scripts/verify_restart.py` builds a throwaway installation, starts the real API and worker, lets the
daily cycle fire by itself, stops everything, simulates a week of missed slots and queued work, restarts, and checks that the work ran, the
cycle ran exactly once more, earlier reports and tasks are intact and the audit chain verifies. (Linux-tested; not yet run on Windows.)

## Emergency pause

Overview → **Emergency pause** (reason required). Immediately:

* new production tasks do not start; queued tasks are held
* paid generation, uploads, publishing and automated replies are refused

What it **cannot** do, and says so in its report: a task that is already running finishes its current step (long
handlers check for the pause between steps and hand the task back), and a request already sent to a provider (for
example an upload in flight) cannot be recalled. Only you can resume (Settings → Resume company).

## Spending limits

Settings → Spending and usage limits. **A limit of 0 means no paid use is permitted.** Limits cover daily, monthly
and per-video dollars and daily request counts per category. Retries re-check the limits, so a retry loop cannot
spend past them. Actual charges, estimates and subscription-included usage are always shown separately; revenue is
shown as unavailable until an authorised source exists.

## What restarts can and cannot repeat

* A task that was running when the app stopped is retried after its lease expires — **unless an external action had
  already started** (an upload or paid call). Those tasks become BLOCKED and are never repeated automatically; the
  owner must confirm the real provider state first and then release them. This is what prevents duplicate uploads.
* Tasks have an idempotency key option so the same external intent cannot be queued twice.

## Backups

`scripts\windows\backup.bat` or `python -m app.cli backup` creates a verified copy in `data/backups/` (14 kept by
default; `YTC_BACKUP_RETENTION_COUNT`). It is safe to run while the app is running. Dashboard: Diagnostics →
Environment & backups.

**Restore** (stop the launcher first): `python -m app.cli restore data\backups\company-<stamp>.sqlite3`. The backup
is verified first and the current database is copied to `*.pre-restore-*.sqlite3` before it is replaced.

Backups contain the **database only**. Generated media in `data/artifacts/` must be copied separately (they can be
large). Copy the whole `data/` folder (including `data/secrets/`, which holds the key that opens the stored YouTube permissions) to an external drive for a full backup.

## Upgrading

`git pull`, then `scripts\windows\setup.ps1` again (it is safe to repeat). The API refuses to start if the database
is behind the code and tells you to run `python -m app.cli migrate`.

## Moving to PostgreSQL later

All access goes through SQLAlchemy and Alembic. Set `YTC_DATABASE_URL=postgresql+psycopg://...`, install the driver,
run `python -m app.cli migrate`. SQLite-specific code is confined to `app/db.py` and `app/services/backup.py`
(use `pg_dump` there). Data migration from the SQLite file is not automated yet.

## Data layout

```
data/db/company.sqlite3     database (+ -wal/-shm while running)
data/artifacts/<kind>/      files produced by tasks (hash-verified when used as evidence)
data/backups/               verified database backups
data/logs/                  api.log, worker.log, frontend.log
```
