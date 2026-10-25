"""Asking Google for extra permissions (analytics, comments) only when the owner switches the feature on."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

from app.models.youtube import YouTubeAuthState, YouTubeConnection
from app.youtube import client as yt
from app.youtube import connection as conn_svc
from app.youtube.connection import YouTubeError
from tests.analytics_helpers import connect
from tests.google_fixtures import google  # noqa: F401
from tests.prod_helpers import BDAY
from tests.test_youtube_connection import REDIRECT, connected, sign_in


def scopes_in(url):
    return set(parse_qs(urlsplit(url).query)["scope"][0].split())


def test_each_permission_group_adds_exactly_its_own_scope(db, owner, google):
    base = scopes_in(conn_svc.begin(db, owner, BDAY, REDIRECT))
    assert base == set(yt.PHASE4_SCOPES)
    assert scopes_in(conn_svc.begin(db, owner, BDAY, REDIRECT, ("analytics",))) == base | {yt.SCOPE_ANALYTICS}
    assert scopes_in(conn_svc.begin(db, owner, BDAY, REDIRECT, ("comments",))) == base | {yt.SCOPE_COMMENTS}
    assert scopes_in(conn_svc.begin(db, owner, BDAY, REDIRECT, ("analytics", "comments", "analytics"))) == base | {yt.SCOPE_ANALYTICS, yt.SCOPE_COMMENTS}
    assert not any("monetary" in sc for g in yt.SCOPE_GROUPS.values() for sc in g)  # revenue is never requested
    states = db.scalars(select(YouTubeAuthState)).all()
    assert all(set(s.scopes) >= base for s in states)  # the sign-in remembers what it asked for


def test_an_unknown_permission_group_is_refused(db, owner, google):
    with pytest.raises(YouTubeError) as e:
        conn_svc.begin(db, owner, BDAY, REDIRECT, ("revenue",))
    assert e.value.code == "BAD_PERMISSION"


def test_adding_a_permission_keeps_the_confirmed_channel_and_unions_the_scopes(db, owner, google):
    connected(db, owner, google)
    db.commit()
    c = db.scalar(select(YouTubeConnection))
    assert not conn_svc.has_group(c, "analytics") and not conn_svc.has_group(c, "comments")
    connect(db, owner, google, groups=("analytics",))
    db.expire_all()
    c = db.scalar(select(YouTubeConnection))
    assert c.status == "CONNECTED" and c.made_for_kids is True  # nothing else about the channel changed
    assert conn_svc.has_group(c, "analytics") and not conn_svc.has_group(c, "comments")
    connect(db, owner, google, groups=("comments",))
    db.expire_all()
    c = db.scalar(select(YouTubeConnection))
    assert conn_svc.has_group(c, "analytics") and conn_svc.has_group(c, "comments")  # asking for one does not drop the other
    s = conn_svc.serialize(c)
    assert s["can_read_analytics"] and s["can_manage_comments"] and "yt-analytics.readonly" in s["scopes"]


def test_if_google_does_not_grant_every_requested_permission_nothing_is_saved(db, owner, google):
    connected(db, owner, google)
    db.commit()
    before = list(db.scalar(select(YouTubeConnection)).scopes)
    url = conn_svc.begin(db, owner, BDAY, REDIRECT, ("analytics",))
    db.commit()
    q = parse_qs(urlsplit(url).query)
    google.granted_scopes = list(yt.PHASE4_SCOPES)  # the owner unticked the analytics box
    google.challenge = q["code_challenge"][0]
    with pytest.raises(YouTubeError) as e:
        conn_svc.complete_sign_in(owner, q["state"][0], "good-code")
    assert e.value.code == "SCOPES_MISSING" and "yt-analytics.readonly" in str(e.value)
    db.expire_all()
    c = db.scalar(select(YouTubeConnection))
    assert c.status == "CONNECTED" and list(c.scopes) == before


def test_a_different_google_channel_on_a_permission_upgrade_must_be_confirmed_again(db, owner, google):
    connected(db, owner, google)
    db.commit()
    google.channel = {"id": "UC_someone_else", "snippet": {"title": "Not the same channel"}}
    sign_in(db, owner, google)
    db.expire_all()
    c = db.scalar(select(YouTubeConnection))
    assert c.status == "PENDING_CONFIRMATION" and c.google_channel_id == "UC_someone_else" and c.confirmed_at is None
