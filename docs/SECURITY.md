# Security model

This is a single-owner application that runs on your own computer. The design goal is that **no screen,
agent, or request can do something the backend would not allow**, and that everything significant is auditable.

## Controls and where they are tested

| Control | Implementation | Tests |
|---|---|---|
| Local authentication | scrypt password hashes (stdlib, per-user salt), 12+ char policy, server-side sessions (only a SHA-256 of the token is stored), HttpOnly + SameSite=Lax cookie, 12 h expiry, logout and password change revoke sessions | `test_api.py` (cookie flags, logout replay, password change) |
| Brute-force protection | 5 failures → 15 min lockout per username (persisted), generic error for unknown vs wrong password, dummy-hash timing equalisation | `test_wrong_credentials_use_one_generic_message_and_lock_out` |
| Role-based access (backend) | `Actor` + permission constants; every route declares its permission; owner / viewer / agent / system roles | `test_every_api_route_requires_authentication` (walks the OpenAPI schema), `test_viewer_can_read_the_executive_layer_but_nothing_else` |
| Owner-only powers | Settings, mode, resume, approval decisions and user management require the *human owner*; no agent (including the CEO) holds them, and a tampered agent row cannot grant them | `test_no_agent_can_hold_an_owner_only_permission`, `test_tampered_agent_row_cannot_grant_owner_permissions` |
| Information layers | Layer C (executive) routes never return raw results, errors, tracebacks, check descriptors or verification detail; Layers A/B live under `/api/diagnostics`, owner-only and audit-logged | `test_executive_layer_exposes_no_internal_detail`, `test_diagnostics_*` |
| CSRF | `X-CSRF-Token` required on every state-changing request; `Origin` must be a known local origin | `test_state_changing_requests_need_a_valid_csrf_token`, `test_cross_origin_writes_are_refused` |
| Host allowlist | Requests whose `Host` is not localhost/127.0.0.1 are refused (DNS-rebinding defence in depth) | `test_requests_with_a_foreign_host_header_are_refused` |
| Rate limiting | Per-IP sliding window | `test_rate_limit` |
| Input validation | Pydantic models with length/pattern limits; evidence paths must stay inside `data/` (no `..`, absolute or drive paths) | `test_evidence_cannot_escape_the_data_directory` |
| Secret redaction | Known key patterns, configured secret values and sensitive field names are masked in audit details, task errors and logs | `test_redaction_*`, `test_environment_view_reports_configuration_without_revealing_secrets` |
| Error handling | Unexpected errors return a generic 500; details go to the (redacted) server log | `test_unexpected_errors_do_not_leak_details` |
| Audit log | Append-only, each record hashes the previous one; verified from the dashboard or `python -m app.cli verify-audit`; edits and deletions are detected | `test_audit_chain_*`, stress test below |
| Emergency pause | Stops new tasks, paid generation, publishing, replies; honest report of what it cannot cancel | `test_emergency_pause_*`, `test_pause_report_tells_the_truth_about_running_work` |
| Backups | SQLite online backup + integrity check; restore verifies first and keeps a safety copy | `test_backup_*` |

Also verified on the live stack (not in the unit suite): 150 concurrent API writes + a second process enqueuing
60 tasks + the worker draining them + 80 reads → 0 errors, all tasks completed, audit chain intact.

## What this does **not** protect against

* **Anyone with access to your Windows account or the `data/` folder.** The database is not encrypted and `.env`
  holds provider keys in plain text. Use Windows disk encryption (BitLocker) and keep the folder private.
* **Exposure to the network.** There is no TLS. Keep ports 3000/8000 on `127.0.0.1` (the default). Do not forward
  them to the internet. If you ever serve the dashboard over HTTPS, set `YTC_COOKIE_SECURE=true`.
* **A compromised browser or machine.** Malware that can read your session cookie can act as you.
* **Provider-side risk** (leaked keys at the provider, account bans, policy changes).

## Planned for later phases

* OAuth refresh tokens for YouTube will be encrypted at rest (key kept outside the database) before the first
  real connection (Phase 4).
* **Prompt-injection posture for the LLM phases:** web pages, scripts, comments, uploaded files and model output
  are *data*. They never reach a code path that can change permissions, budgets, gates or settings, because those
  are enforced by ordinary code that does not read model output (see `settings.py`, `gate.py`, `permissions.py`).
  Agents cannot widen their own tool allowlist (`Actor.require_tool`).

## Reporting a problem

This is a private project; record security observations as issues in the repository.
