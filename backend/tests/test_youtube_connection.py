"""Connecting a channel: sign-in, identity confirmation, token protection and refresh."""

from __future__ import annotations

import os
import stat
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select, text

from app.models.audit import AuditEvent
from app.models.youtube import QuotaEntry, YouTubeAuthState, YouTubeConnection
from app.timeutil import utcnow
from app.youtube import client as yt
from app.youtube import connection as conn_svc
from app.youtube.connection import YouTubeError
from app.youtube.crypto import KeyMissing, TokenBox
from tests.google_fixtures import google  # noqa: F401
from tests.prod_helpers import BDAY, MED

REDIRECT = "http://127.0.0.1:8000"


def sign_in(db, owner, google, slug=BDAY, code="good-code"):
    url = conn_svc.begin(db, owner, slug, REDIRECT)
    db.commit()
    q = parse_qs(urlsplit(url).query)
    google.challenge = q["code_challenge"][0]
    google.expect_redirect = REDIRECT + "/api/youtube/callback"
    return conn_svc.complete_sign_in(owner, q["state"][0], code), q


def connected(db, owner, google, slug=BDAY):
    sign_in(db, owner, google, slug)
    db.expire_all()
    return conn_svc.confirm(db, owner, slug, google_channel_id=google.channel["id"], made_for_kids=True)


def test_the_sign_in_url_uses_pkce_a_single_use_state_and_only_the_minimum_scopes(db, owner, google):
    url = conn_svc.begin(db, owner, BDAY, REDIRECT)
    q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert urlsplit(url).netloc == "accounts.google.com" and q["code_challenge_method"] == "S256" and q["access_type"] == "offline"
    assert q["redirect_uri"] == REDIRECT + "/api/youtube/callback" and q["client_id"] == google_client_id()
    assert set(q["scope"].split()) == set(yt.PHASE4_SCOPES)  # upload and read-back; nothing for analytics or comments yet
    st = db.scalar(select(YouTubeAuthState))
    assert st.state_hash != q["state"] and len(st.state_hash) == 64 and st.used_at is None  # only a hash of the state is kept
    assert "verifier" not in st.verifier_enc and len(TokenBox().decrypt(st.verifier_enc)) >= 43


def google_client_id():
    from tests.fake_google import CLIENT_ID
    return CLIENT_ID


def test_nothing_starts_without_an_oauth_client_or_without_the_owner(db, owner, agent, env, monkeypatch):
    from app.config import reset_settings_cache
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    reset_settings_cache()
    with pytest.raises(YouTubeError) as e:
        conn_svc.begin(db, owner, BDAY, REDIRECT)
    assert e.value.code == "NOT_CONFIGURED" and "docs/YOUTUBE.md" in str(e.value)
    from app.security.permissions import PermissionDenied
    with pytest.raises((YouTubeError, PermissionDenied)):
        conn_svc.begin(db, agent("ceo"), BDAY, REDIRECT)


def test_a_successful_sign_in_is_pending_until_the_owner_confirms_the_channel(db, owner, google):
    slug, _ = sign_in(db, owner, google)
    db.expire_all()
    c = db.scalar(select(YouTubeConnection))
    assert slug == BDAY and c.status == "PENDING_CONFIRMATION" and c.google_channel_id == "UC_birthday_123"
    assert c.google_channel_handle == "@hbsongs" and c.made_for_kids is None and c.confirmed_at is None
    assert sorted(c.scopes) == sorted(yt.PHASE4_SCOPES)
    assert db.scalar(select(QuotaEntry.method).where(QuotaEntry.method == "channels.list")) == "channels.list"  # the identity call is counted


def test_tokens_are_encrypted_at_rest_and_never_logged(db, owner, google):
    sign_in(db, owner, google)
    db.expire_all()
    raw = db.execute(text("select refresh_token_enc, access_token_enc from youtube_connections")).one()
    assert "refresh-secret-1" not in raw[0] and "access-1" not in raw[1]
    assert TokenBox().decrypt(raw[0]) == "refresh-secret-1" and TokenBox().decrypt(raw[1]) == "access-1"
    dump = " ".join(str(r) for r in db.execute(text("select details from audit_events")).scalars())
    assert "refresh-secret-1" not in dump and "access-1" not in dump and "good-code" not in dump


def test_a_state_works_once_and_expires(db, owner, google):
    sign_in(db, owner, google)
    google.challenge = None  # PKCE is covered elsewhere; this test is about the state value
    url = conn_svc.begin(db, owner, BDAY, REDIRECT)
    db.commit()
    state = parse_qs(urlsplit(url).query)["state"][0]
    conn_svc.complete_sign_in(owner, state, "good-code")
    with pytest.raises(YouTubeError) as e:
        conn_svc.complete_sign_in(owner, state, "good-code")  # replayed
    assert e.value.code == "BAD_STATE"
    with pytest.raises(YouTubeError) as e:
        conn_svc.complete_sign_in(owner, "never-issued", "good-code")
    assert e.value.code == "BAD_STATE"
    url2 = conn_svc.begin(db, owner, BDAY, REDIRECT)
    for st in db.scalars(select(YouTubeAuthState).where(YouTubeAuthState.used_at.is_(None))):
        st.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    with pytest.raises(YouTubeError) as e:
        conn_svc.complete_sign_in(owner, parse_qs(urlsplit(url2).query)["state"][0], "good-code")
    assert e.value.code == "BAD_STATE"


def test_a_pkce_mismatch_is_rejected_by_google_and_reported(db, owner, google):
    url = conn_svc.begin(db, owner, BDAY, REDIRECT)
    db.commit()
    google.challenge = "not-the-challenge-for-this-verifier"
    with pytest.raises(YouTubeError) as e:
        conn_svc.complete_sign_in(owner, parse_qs(urlsplit(url).query)["state"][0], "good-code")
    assert e.value.code == "EXCHANGE_FAILED"
    assert db.scalar(select(YouTubeConnection)) is None


@pytest.mark.parametrize("setup,code", [
    (lambda g: setattr(g, "granted_scopes", ["https://www.googleapis.com/auth/youtube.readonly"]), "SCOPES_MISSING"),
    (lambda g: setattr(g, "send_refresh_token", False), "NO_REFRESH_TOKEN"),
    (lambda g: setattr(g, "no_channel", True), "NO_CHANNEL"),
])
def test_incomplete_sign_ins_save_nothing(db, owner, google, setup, code):
    setup(google)
    with pytest.raises(YouTubeError) as e:
        sign_in(db, owner, google)
    assert e.value.code == code
    db.expire_all()
    assert db.scalar(select(YouTubeConnection)) is None


def test_the_owner_confirms_the_channel_and_declares_the_audience(db, owner, google, agent):
    sign_in(db, owner, google)
    db.expire_all()
    with pytest.raises(YouTubeError) as e:
        conn_svc.confirm(db, owner, BDAY, google_channel_id="UC_someone_else", made_for_kids=True)
    assert e.value.code == "CHANNEL_CHANGED"
    with pytest.raises(YouTubeError) as e:
        conn_svc.confirm(db, owner, BDAY, google_channel_id="UC_birthday_123", made_for_kids=None)  # type: ignore[arg-type]
    assert e.value.code == "AUDIENCE_REQUIRED"
    with pytest.raises(YouTubeError) as e:
        conn_svc.confirm(db, owner, BDAY, google_channel_id="UC_birthday_123", made_for_kids=False, category_id="999")
    assert e.value.code == "BAD_CATEGORY"
    from app.security.permissions import PermissionDenied
    with pytest.raises((YouTubeError, PermissionDenied)):
        conn_svc.confirm(db, agent("ceo"), BDAY, google_channel_id="UC_birthday_123", made_for_kids=False)
    c = conn_svc.confirm(db, owner, BDAY, google_channel_id="UC_birthday_123", made_for_kids=True)
    assert c.status == "CONNECTED" and c.made_for_kids is True and c.category_id == "10" and c.confirmed_by == "user:owner"  # music is the default for birthday songs


def test_one_youtube_channel_cannot_serve_two_internal_channels(db, owner, google):
    connected(db, owner, google)
    db.commit()
    with pytest.raises(YouTubeError) as e:
        sign_in(db, owner, google, slug=MED)  # the same Google channel again
    assert e.value.code == "ALREADY_USED"


def test_access_tokens_are_reused_until_they_expire_then_refreshed(db, owner, google):
    c = connected(db, owner, google)
    db.commit()
    first = conn_svc.access_token(c.id)
    assert first == "access-1" and not google.calls("grant_type=refresh") and conn_svc.access_token(c.id) == first
    db.get(YouTubeConnection, c.id).access_expires_at = utcnow() + timedelta(seconds=30)
    db.commit()
    assert conn_svc.access_token(c.id) == "access-2"  # inside the 90-second safety margin, so it is renewed


def test_a_refusal_to_refresh_marks_the_connection_for_reconnecting(db, owner, google):
    c = connected(db, owner, google)
    db.get(YouTubeConnection, c.id).access_expires_at = utcnow() - timedelta(hours=1)
    db.commit()
    google.revoked = True  # the owner removed the app in their Google account, or the 'Testing' token expired
    with pytest.raises(YouTubeError) as e:
        conn_svc.access_token(c.id)
    assert e.value.code == "NEEDS_RECONNECT" and "Testing" in str(e.value)
    db.expire_all()
    assert db.get(YouTubeConnection, c.id).status == "NEEDS_RECONNECT"
    with pytest.raises(YouTubeError):
        conn_svc.access_token(c.id)  # and it stays refused until reconnected, without asking Google again
    assert len(google.calls("oauth2.googleapis.com/token")) == 2  # exchange + one refresh attempt


def test_disconnecting_revokes_at_google_and_forgets_the_tokens(db, owner, google):
    c = connected(db, owner, google)
    db.commit()
    conn_svc.disconnect(owner, BDAY)
    db.expire_all()
    row = db.get(YouTubeConnection, c.id)
    assert google.revoked and row.status == "REVOKED" and row.refresh_token_enc is None and row.access_token_enc is None
    with pytest.raises(YouTubeError):
        conn_svc.access_token(c.id)
    events = [e.action for e in db.scalars(select(AuditEvent))]
    assert "youtube.connect_started" in events and "youtube.signed_in" in events and "youtube.confirmed" in events and "youtube.disconnected" in events


# --- token box -----------------------------------------------------------------------------------------------------------


def test_the_key_lives_outside_the_database_with_owner_only_permissions(env):
    box = TokenBox()
    token = box.encrypt("secret-value")
    assert box.key_path.is_file() and "secrets" in box.key_path.parts and token != "secret-value"
    if os.name != "nt":
        assert stat.S_IMODE(box.key_path.stat().st_mode) == 0o600
    assert box.decrypt(token) == "secret-value"
    assert box.encrypt("secret-value") != token  # a fresh nonce every time


def test_a_missing_key_is_reported_and_never_silently_replaced(env):
    box = TokenBox()
    token = box.encrypt("secret-value")
    box.key_path.unlink()
    with pytest.raises(KeyMissing, match="Reconnect"):
        box.decrypt(token)
    assert not box.key_path.exists()  # decrypting never invents a new key
    other = TokenBox()
    other.encrypt("x")  # encrypting may create one...
    with pytest.raises(KeyMissing):
        other.decrypt(token)  # ...but it cannot open what the lost key sealed


def test_tampered_ciphertext_is_refused(env):
    box = TokenBox()
    token = box.encrypt("secret-value")
    with pytest.raises(KeyMissing):
        box.decrypt(token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB"))


def test_session_uris_are_only_ever_google_https_addresses():
    for bad in ("http://upload.googleapis.com/x", "https://evil.example/upload", "https://googleapis.com.evil.example/x", "file:///etc/passwd", ""):
        with pytest.raises(yt.YouTubeApiError):
            yt.check_session_uri(bad)
    yt.check_session_uri("https://upload.googleapis.com/upload/youtube/v3/videos?upload_id=1")


def test_the_quota_ledger_is_untouched_by_a_failed_sign_in(db, owner, google):
    google.send_refresh_token = False
    with pytest.raises(YouTubeError):
        sign_in(db, owner, google)
    assert db.scalar(select(QuotaEntry)) is None  # the identity call was never reached
