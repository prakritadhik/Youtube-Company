"""A video started with the AI company, through the real queue, real handlers, real FFmpeg and a fake ``claude`` program.

The fake answers are fixed, so this proves the *wiring* (who asks whom, what is checked, what each step records and where it stops),
not the quality of anything a real model would write.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.ai import conductor
from app.db import session_scope
from app.enums import ApprovalKind
from app.enums import TaskStatus as S
from app.models.ai import AiGeneration
from app.models.approvals import ApprovalRequest
from app.models.finance import UsageRecord
from app.models.production import CreativeBrief, ScriptVersion
from app.models.tasks import Task
from app.models.videos import Video
from app.services import gate
from app.worker.runner import HANDLERS, Runner
from tests.ai_helpers import SPEC, approved_idea, brief_json, connect_brain, lyrics_json, review_json, run_company, say
from tests.prod_helpers import HAVE_FFMPEG

pytestmark = pytest.mark.skipif(not HAVE_FFMPEG, reason="FFmpeg/ffprobe not installed")


def start(db, owner, tmp_path, monkeypatch, *replies, **extra):
    fake = connect_brain(db, owner, tmp_path, monkeypatch)
    say(fake, *replies, **extra)
    idea = approved_idea(db, owner)
    out = conductor.start_with_ai(db, owner, idea.id, spec=SPEC)
    db.commit()
    return fake, out["video_id"]


def test_an_idea_becomes_a_video_waiting_for_the_owners_approval_with_every_step_recorded(db, owner, tmp_path, monkeypatch):
    brief = brief_json()
    fake, vid = start(db, owner, tmp_path, monkeypatch, brief, review_json(), lyrics_json(brief), review_json())
    run_company(Runner("w1", HANDLERS))

    with session_scope(write=False) as s:
        v = s.get(Video, vid)
        assert (v.stage, v.qa_passed, v.originality_status, v.ceo_rights_verified) == ("READY", True, "PASS", True)
        assert v.owner_approval_status == "PENDING" and gate.evaluate(s, v, purpose="clear").codes() == []
        assert conductor.progress(s, v)["step"] == "publication_approval"
        req = s.scalar(select(ApprovalRequest).where(ApprovalRequest.kind == ApprovalKind.VIDEO_PUBLICATION.value, ApprovalRequest.subject_id == vid))
        assert req is not None and req.status == "PENDING"
        # the brief and the lyrics were written by the AI, approved by the manager, and say so
        b = s.scalar(select(CreativeBrief).where(CreativeBrief.video_id == vid))
        assert b.status == "APPROVED" and b.content["_provenance"]["written_by"].startswith("AI:") and b.reviewed_by == "agent:production-manager"
        sv = s.scalar(select(ScriptVersion).where(ScriptVersion.video_id == vid))
        assert sv.kind == "lyrics" and sv.author_kind == "agent" and sv.author_label.startswith("AI:")
        # four model calls in all, each recorded with its prompt hash, usage and a file
        gens = list(s.scalars(select(AiGeneration).order_by(AiGeneration.id)))
        assert [g.purpose for g in gens] == ["brief.draft", "review.brief", "script.draft", "review.script"] and {g.status for g in gens} == {"OK"}
        assert all(g.artifact_path and len(g.prompt_sha256) == 64 and g.input_tokens == 12 for g in gens)
        usage = list(s.scalars(select(UsageRecord).where(UsageRecord.provider == "claude-code")))
        assert len(usage) == 4 and {u.cost_kind for u in usage} == {"included_in_subscription"} and {u.category for u in usage} == {"model"}
        # every task of the chain was verified by a manager, none by the AI alone
        assert {t.status for t in s.scalars(select(Task).where(Task.video_id == vid))} == {S.COMPLETED.value}
    assert len(fake.calls()) == 4
