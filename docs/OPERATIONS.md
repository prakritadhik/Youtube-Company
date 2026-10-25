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
large). Copy the whole `data/` folder to an external drive for a full backup.

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
