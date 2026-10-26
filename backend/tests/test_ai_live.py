"""Opt-in: the whole AI company against the REAL Claude Code program (``YTC_LIVE_CLAUDE=1 python -m pytest tests/test_ai_live.py -s``).

An idea becomes a video: the real model writes the brief and the lyrics, the real model reviews them as the manager, and the rest (music, scenes,
captions, render, QA, rights, metadata) runs for real. It spends a handful of requests from the signed-in account's plan and takes several minutes.
It proves the prompts and schemas work with a real model; the ordinary suite never runs it, and what a real model writes will differ every time.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import select

from app.ai import conductor
from app.config import reset_settings_cache
from app.db import session_scope
from app.models.ai import AiGeneration
from app.models.production import CreativeBrief, ScriptVersion
from app.models.videos import Video
from app.providers import claude_code as cc
from app.services import settings as settings_svc
from app.worker.runner import HANDLERS, Runner
from tests.ai_helpers import SPEC, approved_idea, run_company
from tests.prod_helpers import HAVE_FFMPEG

pytestmark = pytest.mark.skipif(os.environ.get("YTC_LIVE_CLAUDE") != "1" or cc.locate() is None or not HAVE_FFMPEG,
                                reason="set YTC_LIVE_CLAUDE=1 with Claude Code installed, signed in, and FFmpeg available")


def test_a_real_model_writes_and_reviews_a_birthday_song_and_the_company_builds_it(db, owner, monkeypatch):
    monkeypatch.delenv("YTC_CLAUDE_CODE_BIN", raising=False)
    reset_settings_cache()
    settings_svc.set_value(db, owner, "provider_attestations", {"claude-code": {"api_access_confirmed": True, "commercial_use_confirmed": True}})
    settings_svc.set_value(db, owner, "budget", {"daily_model_requests": 40, "daily_provider_requests": 40})
    db.commit()
    idea = approved_idea(db, owner, title="Lantern Parade Birthday Song")
    vid = conductor.start_with_ai(db, owner, idea.id, spec=SPEC)["video_id"]
    db.commit()
    run_company(Runner("live", HANDLERS), rounds=60)
    with session_scope(write=False) as s:
        v = s.get(Video, vid)
        calls = [(g.purpose, g.attempt, g.status, g.error_code, g.error_detail) for g in s.scalars(select(AiGeneration).order_by(AiGeneration.id))]
        b = s.scalar(select(CreativeBrief).where(CreativeBrief.video_id == vid).order_by(CreativeBrief.version.desc()))
        sv = s.scalar(select(ScriptVersion).where(ScriptVersion.video_id == vid).order_by(ScriptVersion.version.desc()))
        print("\nCALLS:", *calls, sep="\n  ")
        print("BRIEF:", b.status if b else None, (b.content.get("main_concept") if b else None))
        print("LYRICS:\n" + (sv.text if sv else "(none)"))
        print("VIDEO:", v.stage, "qa", v.qa_passed, v.originality_status, "ceo", v.ceo_rights_verified, "owner", v.owner_approval_status, "|", conductor.progress(s, v))
        assert v.stage == "READY" and v.qa_passed and v.originality_status == "PASS" and v.ceo_rights_verified and v.owner_approval_status == "PENDING"
