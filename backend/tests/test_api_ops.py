from __future__ import annotations

from app.db import session_scope
from app.models.tasks import Task
from app.ops import alerts, schedule
from app.worker.runner import HANDLERS, Runner
from tests.api_fixtures import api_env, client, fast_hashing, make_client, owner_client, viewer_client  # noqa: F401


def seed():
    with session_scope() as s:
        schedule.seed_defaults(s)


def test_everything_requires_signing_in(client):
    for method, url in (("GET", "/api/ops/status"), ("GET", "/api/ops/summary"), ("GET", "/api/ops/alerts"), ("POST", "/api/ops/alerts/1/acknowledge"),
                        ("POST", "/api/ops/schedules/x/run-now"), ("POST", "/api/ops/schedules/x/enable"), ("PUT", "/api/ops/schedules/x")):
        assert client.request(method, url, json={}).status_code == 401, (method, url)


def test_status_shows_a_stopped_worker_schedules_and_honest_notes(owner_client):
    seed()
    d = owner_client.get("/api/ops/status").json()
    assert d["worker"]["alive"] is False and d["timezone"] == "Asia/Kathmandu" and d["health"] is None and d["daily_cycle"] is None
    assert {s["slug"] for s in d["schedules"]} == {"health-check", "daily-cycle", "backup"}
    assert all(s["next_run"] for s in d["schedules"]) and any("no e-mail" in n for n in d["notes"])
    assert owner_client.get("/api/ops/summary").json() == {"alerts": {"info": 0, "warning": 0, "critical": 0, "open": 0}, "worker_alive": False}


def test_a_worker_that_beats_is_seen_and_the_cycle_and_health_results_appear(owner_client):
    seed()
    r = Runner("w", HANDLERS)
    r.housekeeping(started=True)
    for slug in ("health-check", "daily-cycle"):
        assert owner_client.post(f"/api/ops/schedules/{slug}/run-now").status_code == 200
    while r.run_once() is not None:
        pass
    d = owner_client.get("/api/ops/status").json()
    assert d["worker"]["alive"] is True and d["worker"]["workers"][0]["worker_id"] == "w"
    assert {c["id"] for c in d["health"]["checks"]} >= {"database", "audit", "disk", "worker", "backup"} and d["health"]["checked_at"]
    cyc = d["daily_cycle"]
    assert cyc["quiet"] is True and [p["phase"] for p in cyc["phases"]][0] == "review" and cyc["phases"][-1]["phase"] == "report"
    assert d["alert_counts"]["open"] >= 1  # the health check found real things (no backup yet) and says so


def test_alerts_can_be_acknowledged_only_by_the_owner(owner_client, viewer_client):
    with session_scope() as s:
        a = alerts.raise_alert(s, "disk", "warning", "Disk low", "3 GiB")
        aid = a.id
    assert viewer_client.get("/api/ops/alerts").status_code == 200
    assert viewer_client.post(f"/api/ops/alerts/{aid}/acknowledge").status_code == 403
    r = owner_client.post(f"/api/ops/alerts/{aid}/acknowledge")
    assert r.status_code == 200 and r.json()["status"] == "ACKNOWLEDGED" and r.json()["acknowledged_by"] == "user:owner"
    assert owner_client.post("/api/ops/alerts/9999/acknowledge").status_code == 404
    with session_scope() as s:
        alerts.resolve(s, "disk")
    assert owner_client.post(f"/api/ops/alerts/{aid}/acknowledge").status_code == 404  # nothing left to acknowledge
    assert owner_client.get("/api/ops/alerts", params={"include_resolved": True}).json()["alerts"][0]["status"] == "RESOLVED"


def test_schedules_can_be_paused_changed_and_run_by_the_owner_only(owner_client, viewer_client):
    seed()
    for url in ("/api/ops/schedules/backup/run-now", "/api/ops/schedules/backup/disable", "/api/ops/schedules/backup/enable"):
        assert viewer_client.post(url, json={}).status_code == 403
    assert viewer_client.put("/api/ops/schedules/backup", json={"cadence": {"kind": "daily", "at": "03:00"}}).status_code == 403
    assert owner_client.post("/api/ops/schedules/backup/disable").json()["enabled"] is False
    assert owner_client.post("/api/ops/schedules/backup/enable").json()["enabled"] is True
    changed = owner_client.put("/api/ops/schedules/backup", json={"cadence": {"kind": "daily", "at": "03:15"}}).json()
    assert changed["cadence"] == {"kind": "daily", "at": "03:15"} and changed["next_run"]
    assert owner_client.put("/api/ops/schedules/backup", json={"cadence": {"kind": "daily", "at": "25:99x"}}).status_code == 409
    assert owner_client.post("/api/ops/schedules/nope/run-now").status_code == 409
    tid = owner_client.post("/api/ops/schedules/backup/run-now").json()["task_id"]
    with session_scope(write=False) as s:
        assert s.get(Task, tid).task_type == "system.backup_database"


def test_the_timezone_is_an_owner_setting(owner_client, viewer_client):
    assert viewer_client.put("/api/settings/timezone", json={"value": "Asia/Tokyo"}).status_code == 403
    assert owner_client.put("/api/settings/timezone", json={"value": "Mars/Olympus"}).status_code == 422
    assert owner_client.put("/api/settings/timezone", json={"value": "Asia/Tokyo"}).status_code == 200
    seed()
    assert owner_client.get("/api/ops/status").json()["timezone"] == "Asia/Tokyo"
