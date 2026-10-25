"""Server-side sessions. The cookie holds a random token; only its SHA-256 is stored."""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.auth import LoginAttempt, User, UserSession
from app.security.passwords import DUMMY_HASH, verify_password
from app.timeutil import utcnow

COOKIE_NAME = "ytc_session"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user: User) -> tuple[str, UserSession]:
    token = secrets.token_urlsafe(32)
    now = utcnow()
    row = UserSession(
        token_hash=_hash(token),
        csrf_token=secrets.token_urlsafe(24),
        user_id=user.id,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(hours=get_settings().session_ttl_hours),
    )
    db.add(row)
    db.flush()
    return token, row


def resolve_session(db: Session, token: str | None) -> UserSession | None:
    if not token:
        return None
    row = db.scalar(select(UserSession).where(UserSession.token_hash == _hash(token)))
    if row is None or row.revoked or row.expires_at <= utcnow():
        return None
    if not row.user.is_active:
        return None
    return row


def revoke_session(db: Session, token: str | None) -> None:
    if not token:
        return
    row = db.scalar(select(UserSession).where(UserSession.token_hash == _hash(token)))
    if row is not None:
        row.revoked = True


def revoke_all_for_user(db: Session, user_id: int) -> int:
    rows = db.scalars(
        select(UserSession).where(UserSession.user_id == user_id, UserSession.revoked.is_(False))
    ).all()
    for r in rows:
        r.revoked = True
    return len(rows)


class LoginLocked(Exception):
    def __init__(self, retry_after: int):
        super().__init__("Too many failed sign-in attempts.")
        self.retry_after = retry_after


def _recent_failures(db: Session, username: str) -> list[LoginAttempt]:
    s = get_settings()
    since = utcnow() - timedelta(seconds=s.login_window_seconds)
    return list(
        db.scalars(
            select(LoginAttempt)
            .where(LoginAttempt.username == username, LoginAttempt.ts >= since)
            .order_by(LoginAttempt.ts.desc())
        )
    )


def check_lockout(db: Session, username: str) -> None:
    s = get_settings()
    attempts = _recent_failures(db, username)
    # Count failures since the last success.
    fails: list[LoginAttempt] = []
    for a in attempts:
        if a.success:
            break
        fails.append(a)
    if len(fails) >= s.login_max_failures:
        unlock_at = fails[0].ts + timedelta(seconds=s.login_lockout_seconds)
        remaining = int((unlock_at - utcnow()).total_seconds())
        if remaining > 0:
            raise LoginLocked(remaining)


def authenticate(db: Session, username: str, password: str, ip: str = "") -> User | None:
    """Return the user on success. Records the attempt. Raises ``LoginLocked`` when locked out."""
    username = username.strip().lower()[:64]
    check_lockout(db, username)
    user = db.scalar(select(User).where(User.username == username))
    ok = verify_password(password, user.password_hash if user else DUMMY_HASH)
    success = bool(user and user.is_active and ok)
    db.add(LoginAttempt(username=username, ip=ip[:64], success=success))
    if success:
        assert user is not None
        user.last_login_at = utcnow()
        return user
    return None
