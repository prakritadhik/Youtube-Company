from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db import session_scope
from app.enums import CostKind
from app.enums import TaskStatus as S
from app.models.tasks import Task
from app.security.permissions import PermissionDenied
from app.services import backup, control, finance, tasks
from app.services import settings as settings_svc
from app.timeutil import utcnow
from app.worker.runner import HandlerResult, NonRetryableError, PausedError, Runner


def set_budget(db, owner, **kw):
    settings_svc.set_value(db, owner, "budget", kw)


# --- finance -----------------------------------------------------------------------------------------------


def test_default_budget_permits_no_paid_operation(db):
    with pytest.raises(finance.BudgetExceeded) as e:
        finance.authorize(db, estimated_micro_usd=finance.usd_to_micro(0.01))
    assert e.value.code == "DAILY_BUDGET"
    with pytest.raises(finance.BudgetExceeded) as e:
        finance.authorize(db, estimated_micro_usd=0, category="image")
    assert e.value.code == "CATEGORY_LIMIT"


def test_daily_monthly_and_per_video_limits(db, owner, system, agent):
    from app.services import videos

    v1, v2, v3 = (videos.create(db, agent("production-manager"), "happy-birthday-songs", f"v{i}").id for i in range(3))
    set_budget(db, owner, daily_usd=2, monthly_usd=3, per_video_usd=1.5, daily_provider_requests=100, daily_image_generations=100)
    finance.authorize(db, estimated_micro_usd=finance.usd_to_micro(1.0), category="image", video_id=v1)
    finance.record_usage(db, system, provider="img", operation="gen", cost_micro_usd=finance.usd_to_micro(1.0),
                         cost_kind=CostKind.ACTUAL, category="image", video_id=v1)
    with pytest.raises(finance.BudgetExceeded) as e:  # $1 + $1 > $1.5 per video
        finance.authorize(db, estimated_micro_usd=finance.usd_to_micro(1.0), category="image", video_id=v1)
    assert e.value.code == "PER_VIDEO_BUDGET"
    finance.authorize(db, estimated_micro_usd=finance.usd_to_micro(1.0), category="image", video_id=v2)
    finance.record_usage(db, system, provider="img", operation="gen", cost_micro_usd=finance.usd_to_micro(1.0),
                         cost_kind=CostKind.ACTUAL, category="image", video_id=v2)
    with pytest.raises(finance.BudgetExceeded) as e:  # $2 spent today; another $0.5 breaks the daily cap
        finance.authorize(db, estimated_micro_usd=finance.usd_to_micro(0.5), category="image", video_id=v3)
    assert e.value.code == "DAILY_BUDGET"


def test_monthly_limit_binds_even_when_daily_is_generous(db, owner, system):
    set_budget(db, owner, daily_usd=100, monthly_usd=2, per_video_usd=100, daily_provider_requests=100)
    finance.record_usage(db, system, provider="p", operation="o", cost_micro_usd=finance.usd_to_micro(1.9),
                         cost_kind=CostKind.ACTUAL)
    with pytest.raises(finance.BudgetExceeded) as e:
        finance.authorize(db, estimated_micro_usd=finance.usd_to_micro(0.5))
    assert e.value.code == "MONTHLY_BUDGET"


def test_request_and_category_limits(db, owner, system):
    set_budget(db, owner, daily_usd=100, monthly_usd=100, daily_provider_requests=2, daily_audio_generations=1)
    finance.authorize(db, category="audio")
    finance.record_usage(db, system, provider="eleven", operation="tts", cost_kind=CostKind.INCLUDED, category="audio")
    with pytest.raises(finance.BudgetExceeded) as e:
        finance.authorize(db, category="audio")
    assert e.value.code == "CATEGORY_LIMIT"
    finance.record_usage(db, system, provider="x", operation="y", cost_kind=CostKind.INCLUDED)
    with pytest.raises(finance.BudgetExceeded) as e:
        finance.authorize(db)
    assert e.value.code == "REQUEST_LIMIT"


def test_subscription_included_usage_is_counted_but_not_priced(db, owner, system):
    set_budget(db, owner, daily_usd=1, monthly_usd=1, daily_provider_requests=10, daily_audio_generations=10)
    for _ in range(3):
        finance.record_usage(db, system, provider="elevenlabs", operation="tts", cost_micro_usd=0,
                             cost_kind=CostKind.INCLUDED, category="audio")
    finance.authorize(db, estimated_micro_usd=finance.usd_to_micro(0.9))  # budget untouched
    summ = finance.spend_summary(db)
    assert summ["today"]["included_in_subscription_units"] == 3 and summ["today"]["actual_usd"] == 0


def test_actual_and_estimated_costs_are_kept_apart_and_sum_exactly(db, owner, system):
    finance.record_usage(db, system, provider="a", operation="o", cost_micro_usd=333_333, cost_kind=CostKind.ACTUAL)
    finance.record_usage(db, system, provider="a", operation="o", cost_micro_usd=333_333, cost_kind=CostKind.ACTUAL)
    finance.record_usage(db, system, provider="b", operation="o", cost_micro_usd=500_000, cost_kind=CostKind.ESTIMATE)
    s = finance.spend_summary(db)
    assert s["today"]["actual_usd"] == 0.666666 and s["today"]["estimated_usd"] == 0.5
    assert "Revenue" in s["note"] and "unavailable" in s["note"]


def test_pause_blocks_all_paid_operations(db, owner):
    set_budget(db, owner, daily_usd=100, monthly_usd=100, daily_provider_requests=100)
    control.pause(db, owner, "stop")
    with pytest.raises(control.ExternalActionBlocked):
        finance.authorize(db, estimated_micro_usd=1)


def test_recording_usage_needs_the_finance_permission_and_rejects_negatives(db, agent, system):
    with pytest.raises(PermissionDenied):
        finance.record_usage(db, agent("script-lyrics-writer"), provider="x", operation="y")
    finance.record_usage(db, agent("cost-usage-analyst"), provider="x", operation="y")
    with pytest.raises(ValueError):
        finance.record_usage(db, system, provider="x", operation="y", cost_micro_usd=-5)


# --- backup / restore ----------------------------------------------------------------------------------------


def test_backup_is_verified_and_restore_recovers_deleted_data(db, system):
    t = tasks.create(db, system, title="precious", task_type="x", review_required=False)
    db.commit()
    info = backup.create_backup("test")
    assert info["ok"] and info["integrity"] == "ok" and info["rows"]["tasks"] == 1
    db.execute(Task.__table__.delete())
    db.commit()
    db.close()
    res = backup.restore_backup(Path(info["path"]))
    assert res["verified"] and res["safety_copy"] and Path(res["safety_copy"]).is_file()
    with session_scope(write=False) as fresh:
        assert fresh.scalar(select(Task.title).where(Task.id == t.id)) == "precious"
        assert fresh.scalar(select(func.count()).select_from(Task)) == 1


def test_corrupt_or_missing_backup_is_refused(db, tmp_path):
    bad = tmp_path / "company-bad.sqlite3"
    bad.write_bytes(b"this is not a database at all" * 50)
    assert backup.verify_backup(bad)["ok"] is False
    with pytest.raises(backup.BackupError):
        backup.restore_backup(bad)
    with pytest.raises(backup.BackupError):
        backup.verify_backup(tmp_path / "missing.sqlite3")


def test_backup_retention_prunes_oldest(db, monkeypatch):
    from app.config import reset_settings_cache

    monkeypatch.setenv("YTC_BACKUP_RETENTION_COUNT", "2")
    reset_settings_cache()
    db.commit()
    names = [Path(backup.create_backup(f"n{i}")["path"]).name for i in range(4)]
    kept = [b["name"] for b in backup.list_backups()]
    assert len(kept) == 2 and set(kept) == set(names[-2:])


# --- runner ---------------------------------------------------------------------------------------------------


def make_task(**kw):
    with session_scope() as s:
        t = tasks.create(s, kw.pop("actor", None) or __import__("app.security.permissions", fromlist=["Actor"]).Actor.system("t"),
                         title=kw.pop("title", "job"), review_required=False, **kw)
        return t.id


def status(tid):
    with session_scope(write=False) as s:
        t = s.get(Task, tid)
        return t.status, t.attempts, t.blocked_reason, (t.error or {}).get("message")


def test_real_system_handler_runs_to_completion_with_verifiable_evidence(db):
    db.commit()
    db.close()
    tid = make_task(title="noop", task_type="system.noop")
    from app.worker.runner import HANDLERS

    assert Runner("w1", HANDLERS).run_once() == tid
    assert status(tid)[0] == S.COMPLETED.value
    with session_scope(write=False) as s:
        from app.models.tasks import Evidence
        from app.services import evidence

        ev = s.scalars(select(Evidence).where(Evidence.task_id == tid)).all()
        assert len(ev) == 1 and evidence._check(s, ev[0])[0] == "verified"


def test_runner_retries_with_backoff_then_gives_up(db):
    db.commit()
    db.close()
    calls = []

    def flaky(ctx):
        calls.append(ctx.task_id)
        raise RuntimeError("provider 503")

    tid = make_task(task_type="flaky")
    r = Runner("w1", {"flaky": flaky})
    base = utcnow()
    assert r.run_once(base) == tid and status(tid)[0] == S.RETRY_SCHEDULED.value
    assert r.run_once(base + timedelta(seconds=1)) is None  # backing off
    assert r.run_once(base + timedelta(hours=1)) == tid and status(tid)[0] == S.RETRY_SCHEDULED.value
    assert r.run_once(base + timedelta(hours=3)) == tid
    st, attempts, _, msg = status(tid)
    assert st == S.FAILED.value and attempts == 3 and "provider 503" in msg and len(calls) == 3
    assert r.run_once(base + timedelta(days=2)) is None  # no infinite loop


def test_non_retryable_failure_is_final(db):
    db.commit()
    db.close()

    def bad(ctx):
        raise NonRetryableError("invalid input")

    tid = make_task(task_type="bad")
    Runner("w", {"bad": bad}).run_once()
    assert status(tid)[:2] == (S.FAILED.value, 1)


def test_handler_noticing_pause_hands_the_task_back(db):
    db.commit()
    db.close()

    def long_job(ctx):
        raise PausedError()

    tid = make_task(task_type="long")
    Runner("w", {"long": long_job}).run_once()
    assert status(tid)[:2] == (S.QUEUED.value, 0)


def test_budget_exceeded_inside_a_handler_blocks_instead_of_retrying(db):
    db.commit()
    db.close()

    def paid(ctx):
        with session_scope() as s:
            finance.authorize(s, estimated_micro_usd=1_000_000)
        return HandlerResult(result={})

    tid = make_task(task_type="paid")
    Runner("w", {"paid": paid}).run_once()
    st, attempts, reason, _ = status(tid)
    assert st == S.BLOCKED.value and "DAILY_BUDGET" in reason and attempts == 1


def test_runner_only_claims_task_types_it_has_handlers_for(db):
    db.commit()
    db.close()
    tid = make_task(task_type="nobody.handles.this")
    assert Runner("w", {"other": lambda c: HandlerResult(result={})}).run_once() is None
    assert status(tid)[0] == S.QUEUED.value


def test_emergency_pause_stops_the_runner_and_resume_restarts_it(db, owner):
    control.pause(db, owner, "incident")
    db.commit()
    db.close()
    tid = make_task(task_type="ok")
    r = Runner("w", {"ok": lambda c: HandlerResult(result={"fine": True})})
    assert r.run_once() is None and status(tid)[0] == S.QUEUED.value
    with session_scope() as s:
        from app.security.permissions import Actor

        control.resume(s, Actor.for_user(1, "owner", "owner"))
    assert r.run_once() == tid and status(tid)[0] == S.COMPLETED.value


def test_crash_and_restart_recovers_without_losing_or_repeating_work(db):
    db.commit()
    db.close()
    ran = []
    tid = make_task(task_type="job")
    # "process 1" claims the task and then dies without reporting
    with session_scope() as s:
        from app.security.permissions import Actor

        assert tasks.claim_next(s, Actor.system("dead"), "dead-worker", {"job"}).id == tid
    assert status(tid)[0] == S.RUNNING.value
    # "process 2" starts after the lease has expired
    survivor = Runner("w2", {"job": lambda c: ran.append(c.task_id) or HandlerResult(result={"ok": True})})
    later = utcnow() + timedelta(hours=1)
    assert survivor.run_once(later) is None  # recovered and scheduled for retry (backoff)
    assert status(tid)[0] == S.RETRY_SCHEDULED.value
    assert survivor.run_once(later + timedelta(hours=1)) == tid
    assert status(tid)[0] == S.COMPLETED.value and ran == [tid]


def test_crash_after_an_external_action_started_is_never_repeated(db):
    db.commit()
    db.close()
    uploads = []

    def upload(ctx):
        ctx.mark_external_action_started({"operation": "videos.insert"})
        uploads.append(1)
        raise ConnectionError("connection reset after the request was sent")

    tid = make_task(task_type="youtube.upload")
    r = Runner("w", {"youtube.upload": upload})
    r.run_once()
    st, _, reason, _ = status(tid)
    assert st == S.BLOCKED.value and "will not be retried automatically" in reason
    assert r.run_once(utcnow() + timedelta(days=1)) is None and uploads == [1]


def test_reporting_handler_produces_a_verified_executive_report(db):
    db.commit()
    db.close()
    tid = make_task(task_type="system.generate_executive_report")
    from app.worker.runner import HANDLERS

    Runner("w", HANDLERS).run_once()
    assert status(tid)[0] == S.COMPLETED.value
    with session_scope(write=False) as s:
        from app.services import reports

        assert reports.latest_executive(s).status == "VERIFIED"


def test_health_and_backup_handlers(db):
    db.commit()
    db.close()
    from app.worker.runner import HANDLERS

    a = make_task(task_type="system.health_check")
    b = make_task(task_type="system.backup_database")
    r = Runner("w", HANDLERS)
    r.run_once()
    r.run_once()
    assert status(a)[0] == S.COMPLETED.value and status(b)[0] == S.COMPLETED.value


def test_background_loop_processes_work_and_stops_cleanly(db, monkeypatch):
    import threading
    import time

    from app.config import reset_settings_cache

    monkeypatch.setenv("YTC_WORKER_POLL_SECONDS", "0.05")
    reset_settings_cache()
    db.commit()
    db.close()
    stop = threading.Event()
    r = Runner("loop", {"ok": lambda c: HandlerResult(result={"fine": True})})
    th = threading.Thread(target=r.run_forever, args=(stop,), daemon=True)
    th.start()
    tid = make_task(task_type="ok")
    deadline = time.time() + 10
    while time.time() < deadline and status(tid)[0] != S.COMPLETED.value:
        time.sleep(0.05)
    stop.set()
    th.join(timeout=5)
    assert status(tid)[0] == S.COMPLETED.value and not th.is_alive()
