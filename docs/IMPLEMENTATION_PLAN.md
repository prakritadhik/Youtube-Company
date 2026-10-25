# Implementation plan

Source of truth for requirements: the owner's master project prompt (kept outside the repository).
This plan records the architecture choices and how the seven delivery phases are sequenced.

## 1. Architecture (selected after environment inspection)

```
 Browser (localhost:3000)
   │  same-origin /api/* (Next.js rewrite → backend)
   ▼
 Next.js 15 + TypeScript + Tailwind  ── owner dashboard (Layer C by default)
   │
   ▼
 FastAPI (127.0.0.1:8000)  ── RBAC, CSRF, audit, rate limit
   │            │
   │            └── Services (tasks, board, approvals, reports, finance, gates …)
   ▼
 SQLite (WAL) via SQLAlchemy 2 + Alembic        ◄── Worker process (job queue runner, scheduler)
   │                                                   │
   └── data/ (artifacts, assets, backups) ◄── FFmpeg ◄─┘   Provider adapters (LLM, TTS, music,
                                                            images, YouTube) → unavailable until
                                                            credentials exist
```

* **Two processes + UI**: API server, worker (persistent queue runner), Next.js. `launcher.py`
  starts all three; Windows `.bat` files wrap it.
* **Sync SQLAlchemy** with SQLite WAL and short transactions. The worker claims jobs with an atomic
  `UPDATE … WHERE status='QUEUED'`, so two workers cannot run the same job.
* **Agents are rows, not processes.** An agent is a role (permissions, allowed tools, schemas,
  manager, limits). Deterministic work runs as ordinary Python handlers. LLM calls are made only by
  handlers that need them, through a provider adapter with budget checks.

## 2. Non-negotiable invariants (enforced in backend code and covered by tests)

| # | Invariant | Where |
|---|---|---|
| 1 | Authorization is checked in the backend; the UI hides nothing that the API still serves | `security/permissions.py`, `api/deps.py` |
| 2 | Owner-controlled settings can only be changed by the owner user | `services/settings.py` |
| 3 | Unverified findings never become active instructions | `services/board.py` state machine |
| 4 | A task is `COMPLETED` only after its manager verified evidence | `services/tasks.py` |
| 5 | The CEO re-checks claims against records; unsupported claims are rejected | `services/reports.py` |
| 6 | Missing data is reported as *unavailable*, never as zero | report + overview builders |
| 7 | Publication gate fails closed | `services/gate.py` |
| 8 | Emergency pause stops new work and external actions; the report states what could not be stopped | `services/control.py` |
| 9 | Spending limits stop discretionary paid operations; retries cannot bypass them | `services/finance.py` |
| 10 | Every significant action is audit-logged in a tamper-evident hash chain | `services/audit.py` |
| 11 | Secrets never appear in logs, audit details or API responses | `security/redaction.py` |

## 3. Phases and acceptance

| Phase | Scope | Acceptance (from the master prompt) |
|---|---|---|
| 1 Foundation | project, DB + migrations, auth/RBAC, org hierarchy (50 roles), task queue, approvals, intelligence board, basic CEO report, audit, pause/mode, budget guard, publication gate, backup, dashboard | App starts locally, owner signs in, tasks persist, organization visible, board updatable, CEO report separates actual records from missing data |
| 2 Real video production | briefs, scripts/lyrics, assets + provenance, adapters, FFmpeg assembly, captions, thumbnails, validation, originality review | One complete local video produced and validated with recorded provenance; unconfigured providers labelled unavailable |
| 3 Research and SEO | source tracking, content planning, SEO, originality comparison, manager reports | A brief moves research → script → metadata → approval with evidence |
| 4 YouTube | OAuth, channel verify, upload, thumbnails, scheduling, quota, approval-first | Real integration test with an authorised account, or documented remaining configuration |
| 5 Analytics and community | metrics, retention, comments, categorisation, reply drafting | Dashboard shows retrieved data and labels unavailable metrics |
| 6 Management and improvement | worker→manager→COO→CEO reporting, experiments, acknowledgements | A verified lesson is approved, stored, communicated, and referenced by a later task |
| 7 Autonomy | scheduler, daily cycle, recovery, health, alerting | A scheduled development workflow survives restarts without the chat open |

## 4. Naming reconciliation

The master prompt uses a few role names that are not in its organisation chart. They are mapped,
not duplicated:

| Prompt term | Implemented as |
|---|---|
| Production Manager, Publishing Manager | `production-manager`, `publishing-manager` (one manager per department) |
| Music Director | `original-music-specialist` |
| Audio Production Specialist | `voice-audio-specialist` |
| Audience Research Analyst | `audience-research-specialist` (Research & Growth) |
| Original Birthday Music Specialist | added as `birthday-music-specialist` (Production) |

## 5. Honest limits to keep in view

* A localhost app only runs while the PC is on, awake, and online.
* No originality, similarity or fingerprint tool proves copyright safety; they are screening aids.
* Singing synthesis, video generation and Canva automation depend on what each provider actually
  exposes to API clients and under what licence; this is verified per phase in `docs/PROVIDERS.md`.
* Medical content always requires human review before publication; this is not configurable.
