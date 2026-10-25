"""The ``demo-production`` command runs the whole real pipeline, including research packaging, on a labelled demo video."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import session_scope
from app.models.research import MetadataSet, ThumbnailConcept
from app.models.videos import Video
from app.production import assets, demo
from tests.prod_helpers import HAVE_FFMPEG

pytestmark = pytest.mark.skipif(not HAVE_FFMPEG, reason="FFmpeg not installed")


def test_the_demo_runs_the_whole_chain_and_only_its_demo_status_stops_it(db):
    out = demo.run(width=320, height=180, preset="ultrafast", progress=lambda *_: None)
    assert out["qa"]["passed"] and out["originality_status"] == "PASS"
    assert out["publication_gate_blockers"] == ["DEMO_CONTENT"], out["publication_gate_blockers"]
    assert out["metadata"]["status"] == "APPROVED" and out["metadata"]["title"].startswith("DEMO")
    with session_scope(write=False) as s:
        v = s.get(Video, out["video_id"])
        assert v.title.startswith("DEMO") and v.metadata_json["demo"] is True and v.metadata_json["metadata_set_id"]
        ms = s.scalar(select(MetadataSet).where(MetadataSet.video_id == v.id))
        assert ms.status == "APPROVED" and ms.created_by == "agent:keyword-seo-specialist" and ms.approved_by == "agent:research-manager"
        concept = s.scalar(select(ThumbnailConcept).where(ThumbnailConcept.video_id == v.id))
        assert concept.status == "APPROVED"
    with session_scope(write=False) as s:
        thumb = next(a for a in assets.for_video(s, out["video_id"]) if a.kind == "thumbnail")
        assert thumb.params["concept_id"] == concept.id and thumb.params["title"] == "DEMO Garden Morning"
