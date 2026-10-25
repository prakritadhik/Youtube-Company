from __future__ import annotations

import pytest
from sqlalchemy import text

from app.enums import OperatingMode
from app.models.audit import AuditEvent
from app.security.permissions import PermissionDenied
from app.services import audit, control, tasks
from app.services import settings as settings_svc


def test_audit_chain_links_and_verifies(db, owner):
    for i in range(5):
        audit.record(db, owner, "test.event", "thing", i, {"i": i})
    db.commit()
    res = audit.verify_chain(db)
    assert res["ok"] and res["checked"] >= 5
    events = db.query(AuditEvent).order_by(AuditEvent.id).all()
    assert events[1].prev_hash == events[0].hash


def test_audit_chain_detects_edits_and_deletions(db, owner):
    for i in range(4):
        audit.record(db, owner, "test.event", "thing", i, {"i": i})
    db.commit()
    victim = db.query(AuditEvent).filter(AuditEvent.action == "test.event").order_by(AuditEvent.id).all()[1]
    db.execute(text("UPDATE audit_events SET details = :d WHERE id = :i"), {"d": '{"i": 999}', "i": victim.id})
    db.commit()
    res = audit.verify_chain(db)
    assert not res["ok"] and res["first_bad_id"] == victim.id


def test_audit_chain_detects_deleted_row(db, owner):
    for i in range(4):
        audit.record(db, owner, "test.event", "thing", i, {})
    db.commit()
    rows = db.query(AuditEvent).order_by(AuditEvent.id).all()
    db.execute(text("DELETE FROM audit_events WHERE id = :i"), {"i": rows[-3].id})
    db.commit()
    assert not audit.verify_chain(db)["ok"]


def test_audit_redacts_secrets(db, owner):
    ev = audit.record(db, owner, "x", None, None, {"api_key": "sk-ant-api03-abcdefghijklmnop", "note": "fine"})
    assert "sk-ant" not in str(ev.details) and ev.details["note"] == "fine"


def test_defaults_are_safe(db):
    assert settings_svc.operating_mode(db) is OperatingMode.DEVELOPMENT
    b = settings_svc.get(db, "budget")
    assert b["daily_usd"] == 0 and b["monthly_usd"] == 0 and b["per_video_usd"] == 0
    assert settings_svc.get(db, "autonomy")["comment_reply_policy"] == "draft_only"
    assert not settings_svc.is_emergency_paused(db)


def test_only_the_owner_changes_owner_controlled_settings(db, owner, viewer, ceo, coo, system):
    settings_svc.set_value(db, owner, "budget", {"daily_usd": 5, "monthly_usd": 50})
    assert settings_svc.get(db, "budget")["daily_usd"] == 5
    settings_svc.set_value(db, owner, "operating_mode", "APPROVAL_FIRST")
    for who in (ceo, coo, viewer, system):
        with pytest.raises(PermissionDenied):
            settings_svc.set_value(db, who, "budget", {"daily_usd": 9999})
        with pytest.raises(PermissionDenied):
            settings_svc.set_value(db, who, "operating_mode", "CONTROLLED_AUTONOMY")
    assert settings_svc.get(db, "budget")["daily_usd"] == 5
    assert settings_svc.operating_mode(db) is OperatingMode.APPROVAL_FIRST


def test_setting_validation(db, owner):
    with pytest.raises(settings_svc.SettingError):
        settings_svc.set_value(db, owner, "operating_mode", "YOLO")
    with pytest.raises(settings_svc.SettingError):
        settings_svc.set_value(db, owner, "budget", {"daily_usd": -1})
    with pytest.raises(settings_svc.SettingError):
        settings_svc.set_value(db, owner, "budget", {"unknown": 1})
    with pytest.raises(settings_svc.SettingError):
        settings_svc.set_value(db, owner, "emergency_pause", {"active": False})  # not directly writable
    with pytest.raises(settings_svc.SettingError):
        settings_svc.set_value(db, owner, "autonomy", {"comment_reply_policy": "anything_goes"})


def test_setting_changes_are_audited_with_before_and_after(db, owner):
    settings_svc.set_value(db, owner, "budget", {"daily_usd": 3})
    ev = db.query(AuditEvent).filter(AuditEvent.action == "settings.changed").one()
    assert ev.details["before"]["daily_usd"] == 0 and ev.details["after"]["daily_usd"] == 3


def test_ceo_can_pause_but_only_owner_can_resume(db, owner, ceo):
    report = control.pause(db, ceo, "Suspicious upload spike")
    assert settings_svc.is_emergency_paused(db) and report["new_tasks_blocked"]
    with pytest.raises(PermissionDenied):
        control.resume(db, ceo)
    assert settings_svc.is_emergency_paused(db)
    control.resume(db, owner, "false alarm")
    assert not settings_svc.is_emergency_paused(db)


def test_pause_requires_a_reason(db, owner):
    with pytest.raises(settings_svc.SettingError):
        control.pause(db, owner, "  ")


def test_pause_report_tells_the_truth_about_running_work(db, owner, system):
    t = tasks.create(db, system, title="Upload", task_type="youtube.upload", review_required=False)
    tasks.claim_next(db, system, "w1", {"youtube.upload"})
    report = control.pause(db, owner, "test")
    assert report["running_tasks_not_interrupted"] == 1
    assert report["external_actions_in_flight"][0]["task_id"] == t.id
    assert "cannot be cancelled" in report["limits"]


def test_external_actions_follow_mode_and_pause(db, owner):
    control.assert_external_action_allowed(db, "paid_generation")  # DEVELOPMENT allows paid (budget gates it)
    with pytest.raises(control.ExternalActionBlocked) as e:
        control.assert_external_action_allowed(db, "publish")
    assert e.value.code == "MODE_DEVELOPMENT"
    with pytest.raises(control.ExternalActionBlocked):
        control.assert_external_action_allowed(db, "reply")
    settings_svc.set_value(db, owner, "operating_mode", "APPROVAL_FIRST")
    control.assert_external_action_allowed(db, "publish")
    settings_svc.set_value(db, owner, "operating_mode", "PAUSED")
    for kind in ("publish", "reply", "paid_generation"):
        with pytest.raises(control.ExternalActionBlocked):
            control.assert_external_action_allowed(db, kind)
    settings_svc.set_value(db, owner, "operating_mode", "CONTROLLED_AUTONOMY")
    control.pause(db, owner, "stop")
    with pytest.raises(control.ExternalActionBlocked) as e:
        control.assert_external_action_allowed(db, "publish")
    assert e.value.code == "EMERGENCY_PAUSE"
