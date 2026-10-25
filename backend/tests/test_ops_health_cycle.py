"""Alerts, health checks, the worker heartbeat and the daily operating cycle."""

from __future__ import annotations

import os
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db import session_scope
from app.enums import ReportKind
from app.enums import TaskStatus as S
from app.models.ops import Alert, Schedule, WorkerHeartbeat
from app.models.reports import Report
from app.models.tasks import Task
from app.models.videos import Video
from app.ops import alerts, cycle, health, schedule
from app.research import ideas
from app.services import approvals, control, org, tasks, videos
from app.services import settings as settings_svc
from app.storage import write_bytes_artifact
from app.timeutil import utcnow
from app.worker.runner import HANDLERS, Runner
from tests.google_fixtures import google  # noqa: F401
from tests.prod_helpers import BDAY, MED
from tests.test_gate_videos import make_ready_video
from tests.test_youtube_connection import connected


def by_id(checks):
    return {c["id"]: c for c in checks}


# --- alerts ---------------------------------------------------------------------------------------------------------------------


def test_the_same_problem_is_one_alert_with_a_count_not_a_flood(db):
    a = alerts.raise_alert(db, "disk", "warning", "Disk low", "3 GiB free")
    b = alerts.raise_alert(db, "disk", "warning", "Disk low", "2 GiB free")
    assert a.id == b.id and b.count == 2 and b.detail == "2 GiB free" and len(list(db.scalars(select(Alert)))) == 1
    assert alerts.counts(db) == {"info": 0, "warning": 1, "critical": 0, "open": 1}


def test_an_acknowledged_alert_reopens_when_it_gets_worse(db, owner, agent):
    a = alerts.raise_alert(db, "disk", "warning", "Disk low")
    with pytest.raises(PermissionError):
        alerts.acknowledge(db, agent("ceo"), a.id)
    alerts.acknowledge(db, owner, a.id)
    assert a.status == "ACKNOWLEDGED" and alerts.counts(db)["open"] == 0
    alerts.raise_alert(db, "disk", "warning", "Disk low")
    assert a.status == "ACKNOWLEDGED"  # same severity: stays acknowledged
    alerts.raise_alert(db, "disk", "critical", "Disk nearly full")
    assert a.status == "OPEN" and a.severity == "critical" and a.acknowledged_by is None


def test_resolving_closes_it_and_a_later_recurrence_is_a_new_alert(db):
    a = alerts.raise_alert(db, "x", "info", "Something")
    assert alerts.resolve(db, "x") is True and alerts.resolve(db, "x") is False and a.status == "RESOLVED"
    b = alerts.raise_alert(db, "x", "info", "Something again")
    assert b.id != a.id and [x.id for x in alerts.list_alerts(db)] == [b.id]
    assert {x.id for x in alerts.list_alerts(db, include_resolved=True)} == {a.id, b.id}


def test_severity_must_be_known(db):
    with pytest.raises(ValueError):
        alerts.raise_alert(db, "x", "catastrophic", "no")


def test_alerts_are_listed_worst_first_with_acknowledged_ones_last(db, owner):
    alerts.raise_alert(db, "i", "info", "an info")
    w = alerts.raise_alert(db, "w", "warning", "a warning")
    alerts.raise_alert(db, "c", "critical", "a critical")
    alerts.acknowledge(db, owner, w.id)
    assert [a.key for a in alerts.list_alerts(db)] == ["c", "i", "w"]


# --- worker heartbeat -----------------------------------------------------------------------------------------------------------


def test_a_worker_that_stops_beating_is_reported_as_stopped(db):
    assert health.worker_status(db)["alive"] is False
    db.commit()  # the test session is a write session; let go of it before the worker writes
    Runner("w1", HANDLERS).housekeeping(started=True)
    st = health.worker_status(db)
    assert st["alive"] is True and st["workers"][0]["worker_id"] == "w1" and st["workers"][0]["alive"]
    db.commit()
    db.expire_all()
    row = db.get(WorkerHeartbeat, "w1")
    row.last_beat = utcnow() - timedelta(minutes=3)
    db.commit()
    st = health.worker_status(db)
    assert st["alive"] is False and st["workers"][0]["alive"] is False and st["workers"][0]["seconds_since_beat"] >= 180


# --- health checks ----------------------------------------------------------------------------------------------------------------


def test_a_fresh_install_is_healthy_except_for_what_is_honestly_missing(db, monkeypatch):
    monkeypatch.setattr(health.shutil, "disk_usage", lambda p: SimpleNamespace(free=50 * 1024**3))
    c = by_id(health.run_checks(db))
    assert c["database"]["status"] == "ok" and c["audit"]["status"] == "ok" and c["disk"]["status"] == "ok"
    assert c["worker"]["status"] == "warn" and "not running" in c["worker"]["detail"]  # nothing has beaten yet
    assert c["backup"]["status"] == "warn" and "no backup" in c["backup"]["detail"]
    assert c["blocked"]["status"] == "ok" and c["failed"]["status"] == "ok" and c["control"]["status"] == "ok"
    assert "token_key" not in c  # no YouTube permission is stored, so there is no key to lose


def test_disk_pressure_is_graded(db, monkeypatch):
    for gib, status in ((50, "ok"), (3, "warn"), (0.4, "fail")):
        monkeypatch.setattr(health.shutil, "disk_usage", lambda p, g=gib: SimpleNamespace(free=int(g * 1024**3)))
        assert by_id(health.run_checks(db))["disk"]["status"] == status


def test_old_blocked_tasks_failed_tasks_and_overdue_approvals_are_flagged(db, agent, owner):
    t = tasks.create(db, agent("production-manager"), title="Stuck thing", task_type="system.noop", assigned_agent_slug="creative-director")
    t.status, t.blocked_reason = S.BLOCKED.value, "waiting"
    t.updated_at = utcnow() - timedelta(days=2)
    for i in range(3):
        f = tasks.create(db, agent("production-manager"), title=f"Failed {i}", task_type="system.noop", assigned_agent_slug="creative-director")
        f.status = S.FAILED.value
    db.flush()
    c = by_id(health.run_checks(db))
    assert c["blocked"]["status"] == "warn" and "over a day" in c["blocked"]["detail"]
    assert c["failed"]["status"] == "warn" and "3 failed" in c["failed"]["detail"]


def test_a_blocked_task_with_an_unreconciled_external_action_is_called_out(db, agent):
    t = tasks.create(db, agent("production-manager"), title="Uploading", task_type="system.noop", assigned_agent_slug="creative-director")
    t.status, t.output_refs = S.BLOCKED.value, {"external_action_started": {"at": "x"}}
    db.flush()
    c = by_id(health.run_checks(db))
    assert c["blocked"]["status"] == "warn" and "reconcile" in c["blocked"]["detail"]


def test_an_overdue_schedule_is_reported_as_not_firing(db):
    db.add(Schedule(slug="late", title="Late", cadence={"kind": "interval", "every_minutes": 60}, task_type="system.noop", created_at=utcnow() - timedelta(days=2),
                    last_slot=utcnow() - timedelta(days=1)))
    db.flush()
    assert by_id(health.run_checks(db))["schedules"]["status"] == "warn"


def test_a_lost_token_key_is_critical_once_permissions_are_stored(db, owner, google):
    connected(db, owner, google)
    db.commit()
    assert by_id(health.run_checks(db))["token_key"]["status"] == "ok"
    from app.youtube.crypto import TokenBox
    TokenBox().key_path.unlink()
    c = by_id(health.run_checks(db))
    assert c["token_key"]["status"] == "fail" and "reconnect" in c["token_key"]["detail"]


def test_a_connection_that_needs_reconnecting_is_flagged(db, owner, google):
    from app.models.youtube import YouTubeConnection
    connected(db, owner, google)
    db.query(YouTubeConnection).one().status = "NEEDS_RECONNECT"
    db.flush()
    assert by_id(health.run_checks(db))["youtube_connections"]["status"] == "warn"


def test_emergency_pause_is_shown_as_the_operating_state(db, owner):
    control.pause(db, owner, "testing")
    db.flush()
    assert by_id(health.run_checks(db))["control"]["status"] == "warn"


def test_health_results_raise_alerts_and_clear_them_when_the_problem_goes(db, monkeypatch):
    monkeypatch.setattr(health.shutil, "disk_usage", lambda p: SimpleNamespace(free=int(0.4 * 1024**3)))
    health.apply(db, health.run_checks(db))
    a = alerts.open_for(db, "health:disk")
    assert a is not None and a.severity == "critical" and "0.4 GiB" in a.detail
    monkeypatch.setattr(health.shutil, "disk_usage", lambda p: SimpleNamespace(free=int(40 * 1024**3)))
    out = health.apply(db, health.run_checks(db))
    assert alerts.open_for(db, "health:disk") is None and out["resolved"] >= 1


def test_the_health_task_runs_through_the_queue_and_records_its_report(db):
    tasks.create(db, org.actor_for_slug(db, "ceo"), title="Health", task_type="ops.health_check", review_required=False)
    db.commit()
    assert Runner("w", HANDLERS).run_once() is not None
    with session_scope(write=False) as s:
        t = s.scalar(select(Task).where(Task.task_type == "ops.health_check"))
        assert t.status == S.COMPLETED.value and set(t.result["summary"]) == {"ok", "warn", "fail"}
        assert s.scalars(select(Alert)).all()  # the fresh install has real findings (no worker beat, no backup), now visible


# --- the daily cycle ----------------------------------------------------------------------------------------------------------------


def phases(result):
    return {p["phase"]: p for p in result["phases"]}


def test_a_quiet_day_is_reported_as_quiet_and_nothing_is_invented(db):
    with session_scope() as s:
        out = cycle.run_cycle(s)
    p = phases(out)
    assert out["quiet"] is True and out["needs_attention"] == 0
    assert [x["phase"] for x in out["phases"]] == ["review", "research", "planning", "production", "quality", "publishing", "monitoring", "learning", "report"]
    for name in ("research", "planning", "production", "quality", "publishing"):
        assert p[name]["status"] == "nothing_to_do", name
    assert p["monitoring"]["status"] == "unavailable" and "not connected" in p["monitoring"]["summary"] and p["learning"]["status"] == "unavailable"
    assert p["report"]["status"] == "done"
    with session_scope(write=False) as s:
        assert s.get(Report, p["report"]["executive_report_id"]).kind == ReportKind.EXECUTIVE.value
        assert alerts.open_for(s, "cycle:needs-you") is None


def test_the_cycle_lists_what_needs_a_person_and_says_so_in_an_alert(db, agent):
    cs = agent("content-strategy-specialist")
    idea = ideas.propose(db, cs, BDAY, title="Lantern Parade Birthday", audience="Children", concept="A gentle original piece about a lantern parade through a quiet village.")
    ideas.assess(db, cs, idea.id)
    tasks.create(db, agent("production-manager"), title="Write the brief — Demo", task_type="manual.brief", assigned_agent_slug="creative-director")
    blocked = tasks.create(db, agent("production-manager"), title="Stuck render", task_type="system.noop", assigned_agent_slug="creative-director")
    blocked.status, blocked.blocked_reason = S.BLOCKED.value, "ASSET: waiting"
    db.commit()
    with session_scope() as s:
        out = cycle.run_cycle(s)
        assert alerts.open_for(s, "cycle:needs-you") is not None
    p = phases(out)
    assert out["quiet"] is False and out["needs_attention"] == 3
    assert p["planning"]["needs_decision"][0]["title"] == "Lantern Parade Birthday"
    assert p["production"]["waiting_for_a_person"][0]["title"].startswith("Write the brief") and p["production"]["blocked"][0]["reason"] == "ASSET: waiting"


def test_stale_ideas_are_re_assessed_and_newly_producible_ones_are_pointed_out(db, agent):
    cs = agent("content-strategy-specialist")
    idea = ideas.propose(db, cs, BDAY, title="Quiet Lake Birthday", audience="Children", concept="A slow piece about morning mist drifting over a quiet lake near the hills.",
                         requires=["instrumental_music"])
    ideas.assess(db, cs, idea.id)
    idea.status = "PARKED"
    idea.assessment = {**idea.assessment, "feasibility": {**idea.assessment["feasibility"], "verdict": "needs_provider"}}  # it was parked, waiting for a capability
    idea.assessed_at = utcnow() - timedelta(days=2)
    db.commit()
    with session_scope() as s:
        p = phases(cycle.run_cycle(s))
    assert p["research"]["reassessed"] == [idea.id] and p["research"]["newly_producible"] == [{"idea": idea.id, "title": "Quiet Lake Birthday"}]
    with session_scope() as s:  # fresh assessments are left alone
        assert phases(cycle.run_cycle(s))["research"]["status"] == "nothing_to_do"


TITLES = ["Garden Morning Drums", "Quiet Lake Harp", "Paper Boats Waltz"]


def ready(db, agent, owner, google, channel=BDAY, n=1):
    out = []
    for i in range(n):
        v = make_ready_video(db, agent, channel, title=TITLES[i + (1 if channel == MED else 0)])
        v.final_video_path = write_bytes_artifact("videos", f"cy{v.id}.mp4", os.urandom(300_000))
        v.thumbnail_path = write_bytes_artifact("videos", f"cy{v.id}.png", b"\x89PNG" + os.urandom(500))
        out.append(v.id)
    return out


def test_uploads_wait_for_the_owner_unless_the_mode_and_the_channel_list_say_otherwise(db, agent, owner, google):
    ids = ready(db, agent, owner, google)
    for v in ids:
        videos.set_stage(db, agent("production-manager"), v, videos.VideoStage.READY)
    settings_svc.set_value(db, owner, "operating_mode", "APPROVAL_FIRST")
    db.commit()
    with session_scope() as s:
        p = phases(cycle.run_cycle(s))["publishing"]
    assert p["queued"] == [] and "wait for you" in p["summary"] and p["held"][0]["video"] == ids[0]
    with session_scope(write=False) as s:
        assert s.scalars(select(Task).where(Task.task_type == "youtube.upload")).all() == []


def test_controlled_autonomy_queues_only_what_every_rule_allows(db, agent, owner, google):
    ids = ready(db, agent, owner, google, n=2)
    for v in ids:
        videos.set_stage(db, agent("production-manager"), v, videos.VideoStage.READY)
    connected(db, owner, google)
    settings_svc.set_value(db, owner, "operating_mode", "CONTROLLED_AUTONOMY")
    settings_svc.set_value(db, owner, "provider_attestations", {"youtube": {"api_access_confirmed": True}})
    settings_svc.set_value(db, owner, "autonomy", {"autopublish_channels": [BDAY], "max_publishes_per_day_per_channel": 1, "default_privacy": "private"})
    db.commit()
    with session_scope() as s:
        p = phases(cycle.run_cycle(s))["publishing"]
    assert len(p["queued"]) == 1 and p["queued"][0]["privacy"] == "private" and len(p["held"]) == 1 and "limit of 1" in p["held"][0]["why"]
    with session_scope(write=False) as s:
        assert len(s.scalars(select(Task).where(Task.task_type == "youtube.upload")).all()) == 1
    with session_scope() as s:  # a second run the same day must not queue more
        p = phases(cycle.run_cycle(s))["publishing"]
    assert p["queued"] == []


def test_autonomy_never_reaches_a_channel_the_owner_did_not_list_or_medicine(db, agent, owner, google):
    bday = ready(db, agent, owner, google, BDAY)[0]
    med = ready(db, agent, owner, google, MED)[0]
    videos.set_stage(db, agent("production-manager"), bday, videos.VideoStage.READY)
    db.get(Video, med).stage = "READY"  # as if it had been queued; its medical review is still pending, which is exactly what the cycle must respect
    connected(db, owner, google)
    settings_svc.set_value(db, owner, "operating_mode", "CONTROLLED_AUTONOMY")
    settings_svc.set_value(db, owner, "provider_attestations", {"youtube": {"api_access_confirmed": True}})
    settings_svc.set_value(db, owner, "autonomy", {"autopublish_channels": [MED], "max_publishes_per_day_per_channel": 5})  # medicine listed, birthday not
    db.commit()
    with session_scope() as s:
        p = phases(cycle.run_cycle(s))["publishing"]
    assert p["queued"] == []
    why = {h["video"]: h["why"] for h in p["held"]}
    assert "not on your autopublish list" in why[bday] and "MEDICAL_REVIEW_REQUIRED" in why[med]  # a listed medicine channel is still held by the gate


def test_the_cycle_runs_as_a_scheduled_task_and_leaves_evidence(db):
    schedule.seed_defaults(db)
    db.commit()
    assert "daily-cycle" in Runner("w", HANDLERS).housekeeping(now=utcnow() + timedelta(days=2))
    ran = 0
    while Runner("w", HANDLERS).run_once() is not None:
        ran += 1
    with session_scope(write=False) as s:
        t = s.scalar(select(Task).where(Task.task_type == "ops.daily_cycle"))
        assert t.status == S.COMPLETED.value and t.result["executive_report_id"] and t.result["phases"]["monitoring"] == "unavailable"
        from app.models.tasks import Evidence
        assert {e.kind for e in s.scalars(select(Evidence).where(Evidence.task_id == t.id))} == {"file", "db_record"}
    assert ran >= 3  # health check, daily cycle and backup all ran


def test_a_paused_company_runs_no_cycle(db, owner):
    control.pause(db, owner, "stop")
    tasks.create(db, org.actor_for_slug(db, "ceo"), title="Cycle", task_type="ops.daily_cycle", review_required=False)
    db.commit()
    Runner("w", HANDLERS).run_once()
    with session_scope(write=False) as s:
        assert s.scalar(select(Task).where(Task.task_type == "ops.daily_cycle")).status != S.COMPLETED.value
        assert s.scalars(select(Report)).all() == []
    assert approvals is not None
