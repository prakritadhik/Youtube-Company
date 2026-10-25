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
| Outbound web fetching (research) | The only place the company reaches arbitrary URLs. `http`/`https` on ports 80/443 only, no embedded credentials; **every address the host resolves to must be globally routable** (loopback, private, link-local incl. cloud metadata, CGNAT, multicast, reserved and IPv4-in-IPv6 or 6to4/Teredo forms are refused) and the **connection is pinned to the address that was checked**, so a host that changes its DNS answer between check and connect cannot steer a request inward; redirects re-validated (max 4); `robots.txt` honoured and fail-closed; size and time capped on the *decompressed* body; HTML/plain text only; hidden elements and scripts are not extracted | `test_research_safety.py` (66 tests; three deliberate code mutations were confirmed to fail it) |
| Untrusted external text | Pages, comments and competitor material are cleaned (hidden/bidi characters stripped, length capped), scanned for instruction-like phrasing, stored as data and quoted as data. A flag is informational: no code path lets text change a permission, gate, budget or approval | `test_text_from_outside_is_data_and_changes_nothing`, `test_numbers_without_a_source_are_refused_and_planted_instructions_are_only_flagged` |
| YouTube tokens | OAuth with PKCE and a single-use `state` (hash stored); minimal scopes (`youtube.upload`, `youtube.readonly`, plus analytics and comments only on request); refresh/access tokens and upload-session URLs encrypted with Fernet, key in `data/secrets/` outside the database and its backups; never written to audit, logs or API output; token sent only to Google https hosts; owner-only connect/confirm/disconnect | `test_youtube_connection.py`, `test_api_youtube.py` |
| Publishing safety | Upload re-runs the full publication gate, the file hash, attestation, connection and quota at run time; one upload per video; interrupted uploads resume the same session; success only from a read-back on the expected channel | `test_youtube_upload.py` (five deliberate code mutations confirmed to fail it) |
| Comments are untrusted | Comment text and author names are cleaned, length-capped, scanned for instruction-like text, shown quoted and never followed; a flag raises the risk and removes the template option. Safety, copyright, medical and personal-data comments are never answered by the software | `test_community_triage.py`, `test_comment_text_that_gives_orders_is_flagged_and_changes_nothing` |
| Public replies | Draft-only by default; the software writes only a thank-you template for plain praise; the owner approves the exact text (hash checked at approval and again at send); automatic thank-yous only in Controlled autonomy, on listed non-medicine channels, re-checked at send time; one reply per comment (the thread is checked first); success only from a read-back; Development mode and pause block it | `test_community_replies.py` (five deliberate code mutations of the sending path, and four more across triage, collection and permissions, were confirmed to fail the tests) |
| Extra YouTube permissions | Analytics and comment scopes are requested only when the owner presses *Allow*, as a separate sign-in; if Google grants fewer scopes than requested nothing is saved; the revenue scope is never requested | `test_youtube_permissions.py` |
| Lessons and experiments | A lesson is mined only from recorded QA/originality history, with evidence the CEO re-reads; it is policy class, so only the owner makes it an instruction; the dashboard's *verify* runs the board's real check as the CEO role and is audited with who asked; editing withdraws it. Experiments: seeded random arms drawn before publication, no test below the planned sample, a missing day is never zero, confidence capped at medium, only the owner decides. A lesson never reaches another channel's workers | `test_improvement_lessons.py`, `test_improvement_experiments.py`, `test_improvement_stats.py` (fourteen deliberate code mutations; two survived at first and were closed) |
| Research tool allowlist | Fetching needs the role's `web.research` tool (`Actor.require_tool`); roles without it cannot record a fetched page | `test_a_role_without_the_web_tool_cannot_fetch` |

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

* **Prompt-injection posture for the LLM phases** (the data-handling half exists now, see the research and comment rows above): web pages, scripts, comments, uploaded files and model output
  are *data*. They never reach a code path that can change permissions, budgets, gates or settings, because those
  are enforced by ordinary code that does not read model output (see `settings.py`, `gate.py`, `permissions.py`).
  Agents cannot widen their own tool allowlist (`Actor.require_tool`).

## Reporting a problem

This is a private project; record security observations as issues in the repository.
