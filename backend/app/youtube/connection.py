"""Connecting an internal channel to a YouTube channel: sign-in, identity confirmation, tokens.

Flow (all steps audited):

1. The owner presses *Connect* for a channel. The app builds a Google sign-in URL with a one-time ``state`` and a PKCE
   challenge; only a hash of the state and an encrypted copy of the verifier are stored (10 minutes, single use).
2. Google redirects the owner's browser to this app's own loopback address with a code. The app exchanges it, requires the
   upload and read permissions, and asks Google which YouTube channel the account manages.
3. The connection is *pending*. The owner confirms that this is the intended channel (by re-typing nothing, but by sending
   the channel id they were shown) and declares the audience setting. Only then can anything be uploaded.

Network calls never run inside a database write transaction.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import session_scope
from app.models.org import Channel
from app.models.youtube import YouTubeAuthState, YouTubeConnection
from app.security.permissions import Actor, P
from app.services import audit, credentials
from app.timeutil import iso, utcnow
from app.youtube import client as yt
from app.youtube.crypto import KeyMissing, TokenBox

TRANSPORT: httpx.BaseTransport | None = None  # replaced in tests by a mock Google

STATE_LIFETIME = timedelta(minutes=10)
CATEGORY_DEFAULT = {"birthday": "10", "medicine": "27", "story": "1"}  # Music, Education, Film & Animation (owner can change)
CATEGORIES = {"1": "Film & Animation", "10": "Music", "20": "Gaming", "22": "People & Blogs", "24": "Entertainment", "26": "Howto & Style",
              "27": "Education", "28": "Science & Technology"}


class YouTubeError(Exception):
    """A connection step cannot proceed. The message is written for the owner."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def configured() -> bool:
    return bool(credentials.get("google_oauth_client_id")) and bool(credentials.get("google_oauth_client_secret"))


def oauth() -> yt.OAuthClient:
    client_id, client_secret = credentials.get("google_oauth_client_id"), credentials.get("google_oauth_client_secret")
    if not (client_id and client_secret):
        raise YouTubeError("NOT_CONFIGURED", "No Google OAuth client is connected. Create one (docs/YOUTUBE.md) and paste its client ID and secret on the Connections page.")
    return yt.OAuthClient(client_id, client_secret, transport=TRANSPORT)


def _channel(db: Session, slug: str) -> Channel:
    ch = db.scalar(select(Channel).where(Channel.slug == slug))
    if ch is None:
        raise YouTubeError("UNKNOWN_CHANNEL", f"unknown channel '{slug}'")
    return ch


def connection_for(db: Session, channel_id: int) -> YouTubeConnection | None:
    return db.scalar(select(YouTubeConnection).where(YouTubeConnection.channel_id == channel_id))


def _hash(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


# --- 1. begin ---------------------------------------------------------------------------------------------------------------


def scopes_for(groups: tuple[str, ...]) -> tuple[str, ...]:
    bad = [g for g in groups if g not in yt.SCOPE_GROUPS]
    if bad:
        raise YouTubeError("BAD_PERMISSION", f"unknown permission group(s) {bad}; choose from {sorted(yt.SCOPE_GROUPS)}")
    return tuple(dict.fromkeys((*yt.PHASE4_SCOPES, *(s for g in groups for s in yt.SCOPE_GROUPS[g]))))


def has_group(conn: YouTubeConnection | None, group: str) -> bool:
    return bool(conn and conn.status == "CONNECTED" and all(s in (conn.scopes or []) for s in yt.SCOPE_GROUPS[group]))


def begin(db: Session, actor: Actor, channel_slug: str, redirect_base: str, groups: tuple[str, ...] = ()) -> str:
    actor.require(P.SETTINGS_WRITE)
    if not actor.is_owner:
        raise YouTubeError("OWNER_ONLY", "Only the owner can connect a YouTube channel.")
    client = oauth()
    ch = _channel(db, channel_slug)
    verifier, challenge = yt.pkce_pair()
    state = secrets.token_urlsafe(32)
    redirect = redirect_base.rstrip("/") + "/api/youtube/callback"
    scopes = scopes_for(groups)
    db.add(YouTubeAuthState(state_hash=_hash(state), channel_id=ch.id, verifier_enc=TokenBox().encrypt(verifier), redirect_uri=redirect, scopes=list(scopes),
                            created_by=actor.label, expires_at=utcnow() + STATE_LIFETIME))
    audit.record(db, actor, "youtube.connect_started", "channel", ch.id, {"channel": channel_slug, "redirect": redirect, "permissions": list(groups)})
    return yt.auth_url(client.client_id, redirect, state, challenge, scopes)


# --- 2. complete ------------------------------------------------------------------------------------------------------------


def complete_sign_in(actor: Actor, state: str, code: str) -> str:
    """Finish the sign-in. Returns the internal channel slug. No database transaction is open during network calls."""
    if not actor.is_owner:
        raise YouTubeError("OWNER_ONLY", "Only the owner can connect a YouTube channel.")
    with session_scope() as db:  # single use: burn the state before doing anything else with it
        st = db.scalar(select(YouTubeAuthState).where(YouTubeAuthState.state_hash == _hash(state or "")))
        if st is None or st.used_at is not None or st.expires_at < utcnow():
            raise YouTubeError("BAD_STATE", "This sign-in link is not valid any more (it is single-use and expires after 10 minutes). Start again.")
        st.used_at = utcnow()
        verifier, redirect, channel_id = TokenBox().decrypt(st.verifier_enc), st.redirect_uri, st.channel_id
        requested, channel_slug = list(st.scopes or yt.PHASE4_SCOPES), db.get(Channel, st.channel_id).slug  # type: ignore[union-attr]
    try:
        tokens = oauth().exchange_code(code, redirect, verifier)
    except yt.YouTubeApiError as e:
        raise YouTubeError("EXCHANGE_FAILED", f"Google did not accept the sign-in: {e.message}") from e
    granted = set(str(tokens.get("scope", "")).split())
    missing = [s for s in requested if s not in granted]
    if missing:
        raise YouTubeError("SCOPES_MISSING", "Not every requested permission was granted, so nothing was saved. Missing: "
                                             + ", ".join(m.rsplit("/", 1)[-1] for m in missing) + ". Tick every box on Google's screen and try again.")
    refresh = tokens.get("refresh_token")
    if not refresh or not tokens.get("access_token"):
        raise YouTubeError("NO_REFRESH_TOKEN", "Google did not return a long-lived permission (a refresh token). Remove this app at "
                                               "https://myaccount.google.com/permissions and connect again.")
    try:
        mine = yt.YouTubeClient(tokens["access_token"], transport=TRANSPORT).channels_mine()
    except yt.YouTubeApiError as e:
        raise YouTubeError("IDENTITY_FAILED", f"Signed in, but YouTube would not say which channel this is: {e.message}") from e
    items = mine.get("items") or []
    if not items:
        raise YouTubeError("NO_CHANNEL", "This Google account has no YouTube channel. Sign in with the account (or pick the brand account) that owns the channel.")
    ident = items[0]
    box = TokenBox()
    with session_scope() as db:
        from app.youtube import quota
        quota.record(db, "channels.list", note="identity check after sign-in")
        other = db.scalar(select(YouTubeConnection).where(YouTubeConnection.google_channel_id == ident["id"], YouTubeConnection.channel_id != channel_id,
                                                          YouTubeConnection.status.in_(("CONNECTED", "PENDING_CONFIRMATION"))))
        if other is not None:
            raise YouTubeError("ALREADY_USED", "That YouTube channel is already connected to a different channel in this app. Each internal channel needs its own.")
        conn = connection_for(db, channel_id) or YouTubeConnection(channel_id=channel_id)
        db.add(conn)
        same_channel = conn.status == "CONNECTED" and conn.google_channel_id == ident["id"]  # adding permissions to a confirmed channel changes nothing else
        conn.status = "CONNECTED" if same_channel else "PENDING_CONFIRMATION"
        conn.google_channel_id = ident["id"]
        conn.google_channel_title = str(ident.get("snippet", {}).get("title", ""))[:200]
        conn.google_channel_handle = (str(ident.get("snippet", {}).get("customUrl", "")) or None)
        conn.scopes = sorted(granted | set(conn.scopes or []) if same_channel else granted)
        conn.refresh_token_enc, conn.access_token_enc = box.encrypt(refresh), box.encrypt(tokens["access_token"])
        conn.access_expires_at = utcnow() + timedelta(seconds=int(tokens.get("expires_in", 3600)))
        conn.last_error = None
        if not same_channel:
            conn.confirmed_by = conn.confirmed_at = None
        ch = db.get(Channel, channel_id)
        conn.category_id = conn.category_id or CATEGORY_DEFAULT.get(ch.kind, "22")  # type: ignore[union-attr]
        db.flush()
        audit.record(db, actor, "youtube.signed_in", "youtube_connection", conn.id,
                     {"channel": channel_slug, "google_channel_id": conn.google_channel_id, "title": conn.google_channel_title, "scopes": conn.scopes})
    return channel_slug


# --- 3. confirm -------------------------------------------------------------------------------------------------------------


def confirm(db: Session, actor: Actor, channel_slug: str, *, google_channel_id: str, made_for_kids: bool, category_id: str | None = None) -> YouTubeConnection:
    actor.require(P.SETTINGS_WRITE)
    if not actor.is_owner:
        raise YouTubeError("OWNER_ONLY", "Only the owner can confirm a YouTube channel.")
    ch = _channel(db, channel_slug)
    conn = connection_for(db, ch.id)
    if conn is None or conn.status not in ("PENDING_CONFIRMATION", "CONNECTED", "NEEDS_RECONNECT") or not conn.refresh_token_enc:
        raise YouTubeError("NOT_SIGNED_IN", "Sign in to YouTube for this channel first.")
    if conn.google_channel_id != google_channel_id:
        raise YouTubeError("CHANNEL_CHANGED", "The channel signed in is not the one you were shown. Reload the page and check it again.")
    if not isinstance(made_for_kids, bool):
        raise YouTubeError("AUDIENCE_REQUIRED", "Say whether this channel's videos are made for kids; YouTube requires it and the answer is yours to give.")
    cat = category_id or conn.category_id
    if cat not in CATEGORIES:
        raise YouTubeError("BAD_CATEGORY", f"category must be one of {', '.join(f'{k} ({v})' for k, v in CATEGORIES.items())}")
    conn.status, conn.made_for_kids, conn.category_id = "CONNECTED", made_for_kids, cat
    conn.confirmed_by, conn.confirmed_at, conn.last_error = actor.label, utcnow(), None
    audit.record(db, actor, "youtube.confirmed", "youtube_connection", conn.id,
                 {"channel": channel_slug, "google_channel_id": google_channel_id, "made_for_kids": made_for_kids, "category": cat})
    return conn


# --- tokens -----------------------------------------------------------------------------------------------------------------


def access_token(conn_id: int) -> str:
    """A valid access token, refreshed if needed. A refused refresh marks the connection as needing a reconnect."""
    box = TokenBox()
    try:
        with session_scope(write=False) as db:
            conn = db.get(YouTubeConnection, conn_id)
            if conn is None or not conn.refresh_token_enc:
                raise YouTubeError("NOT_CONNECTED", "This channel is not connected to YouTube.")
            if conn.status in ("REVOKED", "NEEDS_RECONNECT"):
                raise YouTubeError("NEEDS_RECONNECT", "The YouTube permission for this channel is no longer valid. Reconnect it in Settings.")
            if conn.access_token_enc and conn.access_expires_at and conn.access_expires_at > utcnow() + timedelta(seconds=90):
                return box.decrypt(conn.access_token_enc)
            refresh = box.decrypt(conn.refresh_token_enc)
    except KeyMissing as e:
        raise YouTubeError("KEY_MISSING", str(e)) from e
    try:
        fresh = oauth().refresh(refresh)
    except yt.YouTubeApiError as e:
        if e.reason == "invalid_grant" or e.is_auth:
            with session_scope() as db:
                c = db.get(YouTubeConnection, conn_id)
                c.status, c.last_error = "NEEDS_RECONNECT", f"Google refused the stored permission: {e.message}"  # type: ignore[union-attr]
                audit.record(db, Actor.system("youtube"), "youtube.needs_reconnect", "youtube_connection", conn_id, {"reason": e.reason})
            raise YouTubeError("NEEDS_RECONNECT", "Google no longer accepts the stored permission (it may have been revoked, or expired because the app is in "
                                                  "'Testing' status). Reconnect the channel in Settings.") from e
        raise YouTubeError("REFRESH_FAILED", f"Could not refresh the YouTube permission: {e.message}") from e
    token = fresh.get("access_token")
    if not token:
        raise YouTubeError("REFRESH_FAILED", "Google answered without an access token.")
    with session_scope() as db:
        c = db.get(YouTubeConnection, conn_id)
        c.access_token_enc = box.encrypt(token)  # type: ignore[union-attr]
        c.access_expires_at = utcnow() + timedelta(seconds=int(fresh.get("expires_in", 3600)))  # type: ignore[union-attr]
    return str(token)


def disconnect(actor: Actor, channel_slug: str) -> None:
    if not actor.is_owner:
        raise YouTubeError("OWNER_ONLY", "Only the owner can disconnect a YouTube channel.")
    box = TokenBox()
    with session_scope() as db:
        conn = connection_for(db, _channel(db, channel_slug).id)
        if conn is None or not conn.refresh_token_enc:
            raise YouTubeError("NOT_CONNECTED", "This channel is not connected.")
        try:
            token = box.decrypt(conn.refresh_token_enc)
        except Exception:  # noqa: BLE001 - an unreadable token still has to be forgettable
            token = None
        cid = conn.id
    revoked = False
    if token and configured():
        try:
            oauth().revoke(token)
            revoked = True
        except yt.YouTubeApiError:
            revoked = False  # we still forget it locally; the owner can remove the app at myaccount.google.com/permissions
    with session_scope() as db:
        c = db.get(YouTubeConnection, cid)
        c.status, c.refresh_token_enc, c.access_token_enc, c.access_expires_at = "REVOKED", None, None, None  # type: ignore[union-attr]
        audit.record(db, actor, "youtube.disconnected", "youtube_connection", cid, {"channel": channel_slug, "revoked_at_google": revoked})


# --- views ------------------------------------------------------------------------------------------------------------------


def serialize(c: YouTubeConnection | None) -> dict[str, Any]:
    if c is None:
        return {"status": "NOT_CONNECTED"}
    return {"status": c.status, "google_channel_id": c.google_channel_id, "google_channel_title": c.google_channel_title,
            "google_channel_handle": c.google_channel_handle, "scopes": [s.rsplit("/", 1)[-1] for s in c.scopes or []],
            "can_read_analytics": has_group(c, "analytics"), "can_manage_comments": has_group(c, "comments"),
            "made_for_kids": c.made_for_kids, "category_id": c.category_id, "category_name": CATEGORIES.get(c.category_id),
            "confirmed_by": c.confirmed_by, "confirmed_at": iso(c.confirmed_at) if c.confirmed_at else None, "last_error": c.last_error}


def status(db: Session) -> dict[str, Any]:
    chans = []
    for ch in db.scalars(select(Channel).order_by(Channel.id)):
        chans.append({"channel": ch.slug, "channel_name": ch.name, "connection": serialize(connection_for(db, ch.id))})
    return {"oauth_client_configured": configured(), "channels": chans, "categories": CATEGORIES}
