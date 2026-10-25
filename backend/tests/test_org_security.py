from __future__ import annotations

import logging

import pytest
from sqlalchemy import func, select

from app.agents.catalog import CHANNELS, DEPARTMENTS, ROLES
from app.agents.seed import OrgIntegrityError, seed_organization, validate_hierarchy
from app.models.org import Agent, Channel, Department
from app.security import passwords, redaction
from app.security.permissions import OWNER_ONLY, Actor, P, PermissionDenied
from app.services import org


def test_seed_creates_complete_organization_and_is_idempotent(db):
    assert db.scalar(select(func.count()).select_from(Agent)) == len(ROLES) == 50
    assert db.scalar(select(func.count()).select_from(Department)) == len(DEPARTMENTS) == 7
    assert db.scalar(select(func.count()).select_from(Channel)) == len(CHANNELS) == 3
    again = seed_organization(db)
    assert again == {"departments": 0, "channels": 0, "agents": 0}
    assert db.scalar(select(func.count()).select_from(Agent)) == 50


def test_every_worker_reports_to_a_manager_of_the_same_department(db):
    workers = db.scalars(select(Agent).where(Agent.level == "worker")).all()
    assert len(workers) == 41
    for w in workers:
        mgr = db.get(Agent, w.reports_to_id)
        assert mgr.level == "manager" and mgr.department_id == w.department_id, w.slug
    managers = db.scalars(select(Agent).where(Agent.level == "manager")).all()
    assert {db.get(Agent, m.reports_to_id).slug for m in managers} == {"coo"}
    assert db.get(Agent, org.get_agent(db, "coo").reports_to_id).slug == "ceo"
    assert org.get_agent(db, "ceo").reports_to_id is None


def test_named_prompt_roles_exist(db):
    for slug in ("copyright-originality-reviewer", "birthday-music-specialist", "original-music-specialist",
                 "voice-audio-specialist", "final-qa-specialist", "youtube-upload-specialist"):
        assert org.get_agent(db, slug)
    assert org.get_agent(db, "copyright-originality-reviewer").extra_permissions == [P.ORIGINALITY_REVIEW]


def test_every_role_declares_responsibility_inputs_outputs_acceptance(db):
    for a in db.scalars(select(Agent)):
        assert a.responsibilities and a.required_inputs and a.output_format and a.acceptance_criteria, a.slug
        assert "board.read" in a.allowed_tools, a.slug


def test_broken_hierarchy_is_detected(db):
    w = org.get_agent(db, "research-specialist")
    w.reports_to_id = org.get_agent(db, "publishing-manager").id  # wrong department
    db.flush()
    with pytest.raises(OrgIntegrityError, match="different department"):
        validate_hierarchy(db)


def test_no_agent_can_hold_an_owner_only_permission(db):
    for a in db.scalars(select(Agent)):
        actor = org.actor_for(a)
        assert not (actor.permissions & OWNER_ONLY), a.slug


def test_tampered_agent_row_cannot_grant_owner_permissions(db):
    a = org.get_agent(db, "research-specialist")
    a.extra_permissions = [P.APPROVAL_DECIDE, P.SETTINGS_WRITE, P.CONTROL_RESUME, P.ORIGINALITY_REVIEW]
    actor = org.actor_for(a)
    assert not actor.can(P.APPROVAL_DECIDE) and not actor.can(P.SETTINGS_WRITE) and not actor.can(P.CONTROL_RESUME)
    assert actor.can(P.ORIGINALITY_REVIEW)  # an allowlisted extra still works


def test_ceo_lacks_owner_powers_but_has_verification_powers(ceo):
    assert not ceo.can(P.APPROVAL_DECIDE) and not ceo.can(P.SETTINGS_WRITE) and not ceo.can(P.CONTROL_RESUME)
    assert ceo.can(P.BOARD_VERIFY) and ceo.can(P.REPORT_VERIFY) and ceo.can(P.CONTROL_PAUSE)


def test_tool_allowlist_is_enforced_for_agents(agent):
    writer = agent("script-lyrics-writer")
    writer.require_tool("llm.generate")
    with pytest.raises(PermissionDenied):
        writer.require_tool("youtube.upload")
    agent("youtube-upload-specialist").require_tool("youtube.upload")


# --- passwords ----------------------------------------------------------------------------------------


def test_password_hash_roundtrip_and_rejection():
    h = passwords.hash_password("Correct-Horse-9", n=2**10)
    assert passwords.verify_password("Correct-Horse-9", h)
    assert not passwords.verify_password("correct-horse-9", h)
    assert not passwords.verify_password("x", "garbage")
    assert "Correct-Horse" not in h
    assert passwords.hash_password("Correct-Horse-9", n=2**10) != h  # unique salt


@pytest.mark.parametrize("pw", ["short1A", "alllowercase123", "ALLUPPERCASE123", "NoDigitsHereAtAll"])
def test_weak_passwords_rejected(pw):
    with pytest.raises(passwords.WeakPassword):
        passwords.validate_password_strength(pw)


def test_strong_password_accepted():
    passwords.validate_password_strength("Correct-Horse-9")


# --- redaction -------------------------------------------------------------------------------------------


def test_redaction_masks_keys_tokens_and_known_secrets():
    redaction.register_secrets(["my-very-secret-value-123"])
    data = {
        "api_key": "abc", "nested": {"Authorization": "Bearer abcdef123456", "ok": "visible"},
        "text": "key sk-ant-api03-abcdefghijklmnop and my-very-secret-value-123 and AIza" + "x" * 35,
        "list": [{"password": "hunter2"}, "ya29.A0ARrdaM-abcdefghij"],
    }
    out = redaction.redact(data)
    blob = repr(out)
    for leaked in ("abc'", "abcdef123456", "sk-ant-api03", "my-very-secret-value-123", "hunter2", "ya29.A0"):
        assert leaked not in blob
    assert out["nested"]["ok"] == "visible"
    assert data["api_key"] == "abc"  # input untouched


def test_log_filter_redacts_secrets(caplog):
    logger = logging.getLogger("redaction.test")
    handler = logging.StreamHandler()
    handler.addFilter(redaction.RedactingFilter())
    rec = logging.LogRecord("x", logging.INFO, "f", 1, "token sk-ant-api03-abcdefghijklmnop leaked", None, None)
    assert handler.filter(rec) and "sk-ant" not in rec.msg and logger is not None


def test_actor_labels_and_owner_flag(owner, viewer, system, ceo):
    assert owner.is_owner and not viewer.is_owner and not system.is_owner and not ceo.is_owner
    assert viewer.can(P.EXEC_READ) and not viewer.can(P.DIAG_READ) and owner.can(P.DIAG_READ)
    assert isinstance(Actor.system("x").permissions, frozenset)
