"""Authentication, authorisation and CSRF dependencies. All enforcement happens here, server-side."""

from __future__ import annotations

import hmac
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request

from app.db import session_scope
from app.security.permissions import Actor
from app.security.sessions import COOKIE_NAME, resolve_session

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass
class Auth:
    actor: Actor
    username: str
    role: str
    csrf_token: str
    session_id: int


def current_auth(request: Request) -> Auth:
    token = request.cookies.get(COOKIE_NAME)
    with session_scope(write=False) as db:
        sess = resolve_session(db, token)
        if sess is None:
            raise HTTPException(status_code=401, detail="Not signed in.")
        u = sess.user
        return Auth(Actor.for_user(u.id, u.username, u.role), u.username, u.role, sess.csrf_token, sess.id)


def verify_csrf(request: Request, auth: Auth) -> None:
    if request.method in SAFE_METHODS:
        return
    sent = request.headers.get("x-csrf-token", "")
    if not sent or not hmac.compare_digest(sent, auth.csrf_token):
        raise HTTPException(status_code=403, detail="Missing or invalid CSRF token.")


def require(permission: str) -> Callable[..., Auth]:
    """Dependency factory: signed in, has ``permission``, and (for writes) presents the CSRF token."""

    def dep(request: Request, auth: Auth = Depends(current_auth)) -> Auth:
        verify_csrf(request, auth)
        if not auth.actor.can(permission):
            raise HTTPException(status_code=403, detail="You do not have permission to do that.")
        return auth

    return dep


def require_signed_in(request: Request, auth: Auth = Depends(current_auth)) -> Auth:
    verify_csrf(request, auth)
    return auth


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""
