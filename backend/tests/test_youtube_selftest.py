"""The owner's first contact with YouTube: a small private upload that is read back and deleted again."""

from __future__ import annotations

import pytest

from app.db import session_scope
from app.models.youtube import YouTubeUpload
from app.services import settings as settings_svc
from app.youtube import selftest
from tests.google_fixtures import google  # noqa: F401
from tests.prod_helpers import BDAY, HAVE_FFMPEG
from tests.test_youtube_connection import connected

pytestmark = pytest.mark.skipif(not HAVE_FFMPEG, reason="FFmpeg not installed")


@pytest.fixture
def armed(db, owner, google):  # noqa: F811
    connected(db, owner, google)
    settings_svc.set_value(db, owner, "operating_mode", "APPROVAL_FIRST")
    settings_svc.set_value(db, owner, "provider_attestations", {"youtube": {"api_access_confirmed": True}})
    db.commit()
    return google


def test_it_uploads_private_reads_back_sets_a_thumbnail_and_deletes(armed):
    out = selftest.run(BDAY, progress=lambda *_: None)
    assert out["result"] == "VERIFIED" and out["thumbnail"] == "SET" and out["deleted"] is True
    assert all(c["ok"] for c in out["read_back"]) and any(c["check"] == "privacy as requested" for c in out["read_back"])
    assert armed.videos == {}  # the test video is gone from YouTube
    assert armed.sessions["sess1"]["meta"]["status"]["privacyStatus"] == "private"  # never anything but private
    assert "ZZ connection test" in armed.sessions["sess1"]["meta"]["snippet"]["title"]
    assert out["quota_units_used_today"] >= 1650


def test_keep_leaves_the_private_video_in_place(armed):
    out = selftest.run(BDAY, keep=True, progress=lambda *_: None)
    assert out["deleted"] is None and len(armed.videos) == 1


def test_a_refused_thumbnail_is_reported_with_the_fix(armed):
    armed.thumbnail_error = (403, "forbidden", "The authenticated user doesn't have permissions to upload and set custom video thumbnails.")
    out = selftest.run(BDAY, progress=lambda *_: None)
    assert out["thumbnail"] == "FAILED" and "verify the channel" in out["thumbnail_detail"]


def test_a_failed_deletion_is_reported_not_hidden(armed):
    armed.fail["videos.list"] = []
    orig = armed._handle

    def refuse_delete(req):
        if req.method == "DELETE":
            armed.requests.append(req)
            return armed._err(403, "forbidden", "insufficient permissions to delete")
        return orig(req)
    import httpx

    from app.youtube import connection as c
    c.TRANSPORT = httpx.MockTransport(refuse_delete)
    out = selftest.run(BDAY, progress=lambda *_: None)
    assert out["deleted"] is False and "insufficient permissions" in out["delete_error"] and len(armed.videos) == 1


def test_it_refuses_in_development_mode_and_without_a_connection(db, owner, google):
    from app.services.control import ExternalActionBlocked
    with pytest.raises(selftest.SelfTestError):
        selftest.run(BDAY, progress=lambda *_: None)  # not connected
    connected(db, owner, google)
    settings_svc.set_value(db, owner, "provider_attestations", {"youtube": {"api_access_confirmed": True}})
    db.commit()
    with pytest.raises(ExternalActionBlocked, match="DEVELOPMENT"):
        selftest.run(BDAY, progress=lambda *_: None)
    assert not google.sessions


def test_it_never_touches_the_catalogue(armed):
    from sqlalchemy import select

    from app.models.videos import Video
    selftest.run(BDAY, progress=lambda *_: None)
    with session_scope(write=False) as s:
        assert s.scalars(select(Video)).all() == []
        up = s.scalar(select(YouTubeUpload))
        assert up.is_test and up.video_id is None
