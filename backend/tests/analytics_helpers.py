"""Shared set-up for the analytics and community tests: a connected channel with chosen permissions and an uploaded video."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from app.youtube import connection as conn_svc
from tests.prod_helpers import BDAY, make_video
from tests.test_youtube_connection import REDIRECT

DAYS = ["2026-10-20", "2026-10-21", "2026-10-22"]
CORE = ("views", "estimatedMinutesWatched", "averageViewDuration", "averageViewPercentage", "likes", "comments", "subscribersGained", "subscribersLost")


def connect(db, owner, google, groups=("analytics", "comments"), slug=BDAY):
    """Sign in asking for the given permission groups, grant exactly those, and confirm the channel."""
    google.granted_scopes = list(conn_svc.scopes_for(tuple(groups)))
    url = conn_svc.begin(db, owner, slug, REDIRECT, tuple(groups))
    db.commit()
    q = parse_qs(urlsplit(url).query)
    google.challenge = q["code_challenge"][0]
    google.expect_redirect = REDIRECT + "/api/youtube/callback"
    conn_svc.complete_sign_in(owner, q["state"][0], "good-code")
    db.expire_all()
    c = conn_svc.confirm(db, owner, slug, google_channel_id=google.channel["id"], made_for_kids=True)
    db.commit()
    return c


def uploaded(db, agent, google, *, yid="vid001", channel=BDAY, title="Garden Morning Song", views=120, likes=9, comments=3):
    """A video that is on YouTube (as far as the mock is concerned) with the given statistics."""
    v = make_video(db, agent, channel, title)
    v.youtube_video_id = yid
    google.stats[yid] = {"viewCount": str(views), "likeCount": str(likes), "commentCount": str(comments)}
    db.commit()
    return v.id


def daily_rows(days=DAYS, **over):
    base = {"views": 40, "estimatedMinutesWatched": 60, "averageViewDuration": 90, "averageViewPercentage": 55.5, "likes": 3, "comments": 1,
            "subscribersGained": 2, "subscribersLost": 0, "impressions": 900, "impressionsClickThroughRate": 4.2}
    return [{"day": d, **{**base, **over}} for d in days]


def thread(cid, text, *, author="Viewer", author_channel="UC_viewer_1", likes=0, published="2026-10-20T10:00:00Z", replies=()):
    """A commentThread resource shaped like YouTube's."""
    def comment(i, t, a, ac, parent=None):
        s = {"authorDisplayName": a, "authorChannelId": {"value": ac}, "textOriginal": t, "textDisplay": t, "likeCount": likes, "publishedAt": published}
        if parent:
            s["parentId"] = parent
        return {"id": i, "snippet": s}
    return {"id": cid, "snippet": {"topLevelComment": comment(cid, text, author, author_channel)},
            "replies": {"comments": [comment(rid, rt, ra, rac, cid) for rid, rt, ra, rac in replies]} if replies else None}
