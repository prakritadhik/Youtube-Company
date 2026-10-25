from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.agents.seed import seed_organization
from app.config import reset_settings_cache
from app.db import session_scope
from app.enums import ApprovalKind, BoardCategory, Confidence
from app.main import create_app
from app.models.tasks import Task
from app.security import passwords
from app.security.permissions import Actor
from app.services import approvals, board, org, tasks
from tests.helpers import file_evidence

PW = "Correct-Horse-9"
PUBLIC = {("GET", "/api/health"), ("GET", "/api/auth/status"), ("POST", "/api/auth/login"),
          ("POST", "/api/auth/bootstrap")}
DOCS = {"/api/docs", "/api/openapi.json"}


@pytest.fixture(autouse=True)
def fast_hashing(monkeypatch):
    monkeypatch.setattr(passwords, "_N", 2**10)


@pytest.fixture
def api_env(env):
    with session_scope() as s:
        seed_organization(s)
    return env


def make_client(host="127.0.0.1") -> TestClient:
    return TestClient(create_app(), base_url="http://localhost", client=(host, 50000), raise_server_exceptions=False)


@pytest.fixture
def client(api_env) -> TestClient:
    return make_client()


def bootstrap(c: TestClient) -> str:
    r = c.post("/api/auth/bootstrap", json={"username": "owner", "password": PW})
    assert r.status_code == 200, r.text
    return r.json()["csrf_token"]


@pytest.fixture
def owner_client(client) -> TestClient:
    client.headers["x-csrf-token"] = bootstrap(client)
    return client


@pytest.fixture
def viewer_client(owner_client, api_env) -> TestClient:
    r = owner_client.post("/api/auth/users", json={"username": "viewer", "password": PW, "role": "viewer"})
    assert r.status_code == 200, r.text
    c = make_client()
    r = c.post("/api/auth/login", json={"username": "viewer", "password": PW})
    assert r.status_code == 200
    c.headers["x-csrf-token"] = r.json()["csrf_token"]
    return c


def ceo_actor():
    with session_scope(write=False) as s:
        return org.actor_for_slug(s, "ceo")


# --- authentication ------------------------------------------------------------------------------------------


def test_every_api_route_requires_authentication(client):
    paths = client.get("/api/openapi.json").json()["paths"]
    checked = 0
    for path, methods in paths.items():
        if path in DOCS:
            continue
        for method in methods:
            if (method.upper(), path) in PUBLIC:
                continue
            url = re.sub(r"\{[^}]+\}", "1", path)
            r = client.request(method.upper(), url, json={} if method != "get" else None)
            assert r.status_code == 401, f"{method.upper()} {path} is reachable without signing in ({r.status_code})"
            checked += 1
    assert checked >= 30  # guards against the enumeration silently finding nothing


def test_first_run_bootstrap_works_once_and_only_locally(api_env):
    c = make_client()
    assert c.get("/api/auth/status").json() == {"initialized": False, "authenticated": False, "user": None, "csrf_token": None}
    assert make_client("10.1.2.3").post("/api/auth/bootstrap", json={"username": "owner", "password": PW}).status_code == 403
    assert c.post("/api/auth/bootstrap", json={"username": "owner", "password": "weakpassword"}).status_code == 422
    assert c.post("/api/auth/bootstrap", json={"username": "owner", "password": PW}).status_code == 200
    st = c.get("/api/auth/status").json()
    assert st["initialized"] and st["authenticated"] and st["user"]["role"] == "owner" and st["csrf_token"]
    assert make_client().post("/api/auth/bootstrap", json={"username": "evil", "password": PW}).status_code == 409


def test_session_cookie_is_httponly_and_logout_revokes_it(client):
    r = client.post("/api/auth/bootstrap", json={"username": "owner", "password": PW})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "ytc_session=" in cookie
    csrf = r.json()["csrf_token"]
    saved = client.cookies.get("ytc_session")
    assert client.get("/api/executive/overview").status_code == 200
    assert client.post("/api/auth/logout", headers={"x-csrf-token": csrf}).status_code == 200
    client.cookies.set("ytc_session", saved)  # replay the old cookie
    assert client.get("/api/executive/overview").status_code == 401


def test_wrong_credentials_use_one_generic_message_and_lock_out(owner_client):
    c = make_client()
    msgs = set()
    for _ in range(5):
        r = c.post("/api/auth/login", json={"username": "owner", "password": "Wrong-Pass-123"})
        assert r.status_code == 401
        msgs.add(r.json()["detail"])
    r = c.post("/api/auth/login", json={"username": "nobody", "password": "Wrong-Pass-123"})
    msgs.add(r.json()["detail"])
    assert len(msgs) == 1
    locked = c.post("/api/auth/login", json={"username": "owner", "password": PW})  # even the right password
    assert locked.status_code == 429 and int(locked.headers["retry-after"]) > 0


def test_password_change_signs_out_every_session(owner_client):
    r = owner_client.post("/api/auth/change-password", json={"current_password": "nope", "new_password": "New-Passw0rd-xyz"})
    assert r.status_code == 403
    r = owner_client.post("/api/auth/change-password", json={"current_password": PW, "new_password": "short"})
    assert r.status_code == 422
    r = owner_client.post("/api/auth/change-password", json={"current_password": PW, "new_password": "New-Passw0rd-xyz"})
    assert r.status_code == 200
    assert owner_client.get("/api/executive/overview").status_code == 401
    c = make_client()
    assert c.post("/api/auth/login", json={"username": "owner", "password": "New-Passw0rd-xyz"}).status_code == 200


# --- CSRF, origin, headers, rate limit -----------------------------------------------------------------------------


def test_state_changing_requests_need_a_valid_csrf_token(owner_client):
    body = {"reason": "testing the pause"}
    good = owner_client.headers.pop("x-csrf-token")
    assert owner_client.post("/api/control/pause", json=body).status_code == 403
    assert owner_client.post("/api/control/pause", json=body, headers={"x-csrf-token": "forged"}).status_code == 403
    assert owner_client.post("/api/control/pause", json=body, headers={"x-csrf-token": good}).status_code == 200


def test_cross_origin_writes_are_refused(owner_client):
    r = owner_client.post("/api/control/pause", json={"reason": "from evil.example"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    r = owner_client.post("/api/control/pause", json={"reason": "from the dashboard"}, headers={"origin": "http://localhost:3000"})
    assert r.status_code == 200


def test_security_headers_and_no_store(client):
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store" and "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_rate_limit(api_env, monkeypatch):
    monkeypatch.setenv("YTC_API_RATE_LIMIT_PER_MINUTE", "5")
    reset_settings_cache()
    c = make_client()
    codes = [c.get("/api/health").status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]


def test_api_docs_are_disabled_in_production(api_env, monkeypatch):
    monkeypatch.setenv("YTC_ENV", "production")
    reset_settings_cache()
    c = make_client()
    assert c.get("/api/docs").status_code == 404 and c.get("/api/openapi.json").status_code == 404


def test_unexpected_errors_do_not_leak_details(owner_client, monkeypatch):
    from app.services import overview

    def boom(_db):
        raise RuntimeError("database password is sk-ant-api03-abcdefghijklmnop")

    monkeypatch.setattr(overview, "executive_overview", boom)
    r = owner_client.get("/api/executive/overview")
    assert r.status_code == 500 and "sk-ant" not in r.text and "password" not in r.text


# --- roles: viewer vs owner --------------------------------------------------------------------------------------


def test_viewer_can_read_the_executive_layer_but_nothing_else(viewer_client):
    c = viewer_client
    for url in ("/api/executive/overview", "/api/executive/reports/latest", "/api/channels", "/api/organization",
                "/api/board", "/api/rights", "/api/costs", "/api/approvals", "/api/videos"):
        assert c.get(url).status_code == 200, url
    for url in ("/api/diagnostics/tasks", "/api/diagnostics/audit", "/api/diagnostics/board", "/api/diagnostics/environment",
                "/api/settings"):
        assert c.get(url).status_code == 403, url
    assert c.put("/api/settings/budget", json={"value": {"daily_usd": 999}}).status_code == 403
    assert c.post("/api/control/pause", json={"reason": "viewer should not"}).status_code == 403
    assert c.post("/api/control/resume", json={}).status_code == 403
    assert c.post("/api/approvals/1/decide", json={"approve": True}).status_code == 403
    assert c.post("/api/executive/reports/generate").status_code == 403
    assert c.post("/api/diagnostics/backup").status_code == 403
    assert c.post("/api/auth/users", json={"username": "x" * 5, "password": PW, "role": "owner"}).status_code == 403


def test_owner_creates_users_and_duplicates_are_refused(owner_client):
    assert owner_client.post("/api/auth/users", json={"username": "second", "password": PW, "role": "viewer"}).status_code == 200
    assert owner_client.post("/api/auth/users", json={"username": "second", "password": PW, "role": "viewer"}).status_code == 409
    assert owner_client.post("/api/auth/users", json={"username": "weak", "password": "abc", "role": "viewer"}).status_code == 422
    assert owner_client.post("/api/auth/users", json={"username": "r", "password": PW, "role": "root"}).status_code == 422


# --- layering: executive vs diagnostics ----------------------------------------------------------------------------


FORBIDDEN_IN_EXECUTIVE = {"traceback", "error", "result", "input_refs", "output_refs", "verification", "password_hash",
                          "token_hash", "locked_by", "lease_expires_at", "idempotency_key", "check", "chain_of_thought"}


def keys_of(obj, found=None):
    found = set() if found is None else found
    if isinstance(obj, dict):
        for k, v in obj.items():
            found.add(k)
            keys_of(v, found)
    elif isinstance(obj, list):
        for v in obj:
            keys_of(v, found)
    return found


def seed_internal_work():
    """Create work that has raw results, errors and evidence, plus a raw (unverified) board finding."""
    with session_scope() as s:
        mgr, w = org.actor_for_slug(s, "production-manager"), org.actor_for_slug(s, "script-lyrics-writer")
        t = tasks.create(s, mgr, title="Write lyrics", assigned_agent_slug="script-lyrics-writer")
        tasks.begin(s, w, t.id)
        tasks.submit(s, w, t.id, result={"draft": "SECRET RAW LYRICS DRAFT"}, evidence_specs=[file_evidence("lyr.txt")])
        sysact = Actor.system("t")
        f = tasks.create(s, sysact, title="Failing job", task_type="x", review_required=False, max_attempts=1)
        tasks.claim_next(s, sysact, "w", {"x"})
        tasks.fail_run(s, sysact, f.id, "w", "provider exploded with key sk-ant-api03-abcdefghijklmnop")
        board.propose(s, org.actor_for_slug(s, "continuous-improvement-scientist"), title="Raw hunch about red thumbnails",
                      description="seen once", category=BoardCategory.THUMBNAIL_TITLE, channel_slug="happy-birthday-songs",
                      confidence=Confidence.LOW)
        return t.id, f.id


def test_executive_layer_exposes_no_internal_detail(owner_client):
    seed_internal_work()
    assert owner_client.post("/api/executive/reports/generate").status_code == 200
    urls = ["/api/executive/overview", "/api/executive/reports", "/api/executive/reports/latest", "/api/organization",
            "/api/channels/happy-birthday-songs", "/api/board", "/api/rights", "/api/costs", "/api/approvals", "/api/videos"]
    for url in urls:
        r = owner_client.get(url)
        assert r.status_code == 200, url
        leaked = keys_of(r.json()) & FORBIDDEN_IN_EXECUTIVE
        assert not leaked, f"{url} leaks internal keys {leaked}"
        assert "SECRET RAW LYRICS" not in r.text and "sk-ant" not in r.text and "Traceback" not in r.text, url
    org_view = owner_client.get("/api/organization").json()
    assert all("evidence" not in t or isinstance(t["evidence"], list) for a in org_view["agents"] for t in a["open_tasks"])
    assert "Raw hunch" not in owner_client.get("/api/board").text


def test_executive_report_route_serves_only_executive_reports(owner_client):
    owner_client.post("/api/executive/reports/generate")
    reps = owner_client.get("/api/diagnostics/reports").json()
    kinds = {r["kind"] for r in reps}
    assert kinds == {"department", "operations", "executive"}
    for r in reps:
        code = owner_client.get(f"/api/executive/reports/{r['id']}").status_code
        assert code == (200 if r["kind"] == "executive" else 404), r["kind"]


def test_ceo_verification_detail_is_only_in_diagnostics(owner_client):
    rep = owner_client.post("/api/executive/reports/generate").json()["report"]
    assert "verification" not in rep
    detail = owner_client.get(f"/api/diagnostics/reports/{rep['id']}").json()
    assert detail["verification"]["summary"]["total"] > 0 and detail["verification"]["results"]


def test_raw_board_findings_visible_only_in_diagnostics(owner_client):
    seed_internal_work()
    assert "Raw hunch" not in owner_client.get("/api/board").text
    full = owner_client.get("/api/diagnostics/board").text
    assert "Raw hunch" in full
    pid = re.search(r"LSN-\d{6}", full).group(0)
    assert owner_client.get(f"/api/board/{pid}").status_code == 404
    assert owner_client.get(f"/api/diagnostics/board/{pid}").status_code == 200


def test_diagnostics_show_raw_detail_to_the_owner_with_secrets_redacted_and_are_audited(owner_client):
    task_id, failed_id = seed_internal_work()
    t = owner_client.get(f"/api/diagnostics/tasks/{task_id}").json()
    assert t["result"] == {"draft": "SECRET RAW LYRICS DRAFT"} and t["evidence"]
    f = owner_client.get(f"/api/diagnostics/tasks/{failed_id}").json()
    assert "provider exploded" in f["error"]["message"] and "sk-ant" not in str(f)
    log = owner_client.get("/api/diagnostics/audit?action=diagnostics.viewed").json()
    assert len(log) >= 2 and all(e["actor"] == "user:owner" for e in log)


def test_environment_view_reports_configuration_without_revealing_secrets(owner_client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-SUPERSECRETVALUE123456")
    reset_settings_cache()
    r = owner_client.get("/api/diagnostics/environment")
    assert r.status_code == 200 and "SUPERSECRET" not in r.text
    assert r.json()["environment"]["providers_configured"]["anthropic"] is True


# --- control, settings, approvals ----------------------------------------------------------------------------------


def test_emergency_pause_and_resume_through_the_api(owner_client):
    assert owner_client.get("/api/executive/overview").json()["company_status"]["emergency_pause"] is False
    r = owner_client.post("/api/control/pause", json={"reason": "suspicious spike"})
    assert r.status_code == 200 and r.json()["new_uploads_blocked"] and "cannot be cancelled" in r.json()["limits"]
    ov = owner_client.get("/api/executive/overview").json()
    assert ov["company_status"]["label"] == "PAUSED" and any("Emergency pause" in x["text"] for x in ov["risks"])
    assert owner_client.post("/api/control/resume", json={"note": "resolved"}).status_code == 200
    assert owner_client.get("/api/executive/overview").json()["company_status"]["emergency_pause"] is False


def test_settings_changes_validate_and_are_audited(owner_client):
    assert owner_client.put("/api/settings/budget", json={"value": {"daily_usd": 4}}).status_code == 200
    assert owner_client.put("/api/settings/budget", json={"value": {"daily_usd": -4}}).status_code == 422
    assert owner_client.put("/api/settings/operating_mode", json={"value": "YOLO"}).status_code == 422
    assert owner_client.put("/api/settings/emergency_pause", json={"value": {"active": False}}).status_code == 422
    assert owner_client.get("/api/settings").json()["settings"]["budget"]["daily_usd"] == 4
    log = owner_client.get("/api/diagnostics/audit?action=settings.changed").json()
    assert log and log[0]["details"]["after"]["daily_usd"] == 4


def test_owner_decides_approvals_via_api_and_the_ceo_cannot(owner_client):
    with session_scope() as s:
        sysact = Actor.system("t")
        t = tasks.create(s, sysact, title="Needs owner OK", task_type="x", review_required=False)
        req = tasks.require_approval(s, sysact, t, kind=ApprovalKind.UNUSUAL_ACTION, summary="do it", why="rare",
                                     if_approved="runs", if_rejected="dropped")
        rid, tid = req.id, t.id
        ceo = org.actor_for_slug(s, "ceo")
        with pytest.raises(Exception):  # noqa: B017,PT011 - PermissionDenied
            approvals.decide(s, ceo, rid, approve=True)
    pend = owner_client.get("/api/approvals").json()["pending"]
    assert pend[0]["id"] == rid and pend[0]["why_it_matters"] and pend[0]["if_approved"] and pend[0]["if_rejected"]
    assert owner_client.post(f"/api/approvals/{rid}/decide", json={"approve": True, "note": "ok"}).status_code == 200
    assert owner_client.post(f"/api/approvals/{rid}/decide", json={"approve": True}).status_code == 409  # already decided
    with session_scope(write=False) as s:
        assert s.get(Task, tid).status == "QUEUED"


def test_audit_log_records_security_relevant_actions_and_stays_valid(owner_client):
    make_client().post("/api/auth/login", json={"username": "owner", "password": "bad-Password-1"})
    owner_client.put("/api/settings/budget", json={"value": {"daily_usd": 2}})
    owner_client.post("/api/control/pause", json={"reason": "audit test"})
    actions = {e["action"] for e in owner_client.get("/api/diagnostics/audit?limit=500").json()}
    assert {"auth.owner_created", "auth.login_failed", "settings.changed", "control.paused", "org.seeded"} <= actions
    assert owner_client.get("/api/diagnostics/audit/verify").json()["ok"] is True


# --- dashboards -------------------------------------------------------------------------------------------------------


def test_organization_shows_the_real_hierarchy_and_only_marks_running_agents_active(owner_client):
    seed_internal_work()
    view = owner_client.get("/api/organization").json()
    assert len(view["agents"]) == 50 and len(view["departments"]) == 7
    writer = next(a for a in view["agents"] if a["slug"] == "script-lyrics-writer")
    assert writer["reports_to"] == "production-manager" and writer["actively_running"] is False
    assert writer["open_tasks"][0]["status"] == "SUBMITTED"
    assert "genuinely RUNNING" in view["note"]


def test_channel_dashboard_labels_missing_data_instead_of_zeros(owner_client):
    d = owner_client.get("/api/channels/medicine-information").json()
    assert d["performance"]["available"] is False and d["audience_feedback"]["available"] is False
    assert d["channel"]["policy"]["human_medical_review_required"] is True
    assert owner_client.get("/api/channels/nope").status_code == 404


def test_overview_distinguishes_missing_data_and_has_no_fake_report(owner_client):
    ov = owner_client.get("/api/executive/overview").json()
    assert ov["latest_executive_report"] is None and "Nothing has been verified" in ov["executive_report_note"]
    assert ov["metrics_availability"]["youtube_analytics"]["available"] is False
    assert ov["company_status"]["operating_mode"] == "DEVELOPMENT" and ov["company_status"]["real_publishing_enabled"] is False
    assert ov["pipeline"]["total"] == 0 and ov["costs"]["limits"]["daily_usd"] == 0
    latest = owner_client.get("/api/executive/reports/latest").json()
    assert latest["available"] is False


def test_requests_with_a_foreign_host_header_are_refused(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 400
    assert client.get("/api/auth/status", headers={"host": "attacker.test:3000"}).status_code == 400
    assert client.get("/api/health", headers={"host": "127.0.0.1:8000"}).status_code == 200


def test_blank_provider_keys_are_not_reported_as_configured(owner_client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "   ")
    reset_settings_cache()
    prov = owner_client.get("/api/diagnostics/environment").json()["environment"]["providers_configured"]
    assert prov == {"anthropic": False, "elevenlabs": False}
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_" + "a" * 32)
    reset_settings_cache()
    assert owner_client.get("/api/diagnostics/environment").json()["environment"]["providers_configured"]["elevenlabs"] is True
