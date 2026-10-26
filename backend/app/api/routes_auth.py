from __future__ import annotations

import ipaddress

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Auth, client_ip, current_auth, require, require_signed_in
from app.api.schemas import Credentials, NewUser, PasswordChange
from app.config import get_settings
from app.db import get_db, get_wdb, session_scope
from app.enums import Role
from app.models.auth import User
from app.security import passwords, sessions
from app.security.permissions import Actor, P
from app.services import audit

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        sessions.COOKIE_NAME, token, httponly=True, samesite="lax", secure=s.cookie_secure,
        max_age=s.session_ttl_hours * 3600, path="/",
    )


def _anon_actor(name: str) -> Actor:
    return Actor.system(name)


@router.get("/status")
def status(request: Request, db: Session = Depends(get_db)):
    """Public: tells the UI whether first-run setup is needed and whether the caller is signed in."""
    initialized = (db.scalar(select(func.count()).select_from(User)) or 0) > 0
    out = {"initialized": initialized, "authenticated": False, "user": None, "csrf_token": None}
    try:
        auth = current_auth(request)
    except HTTPException:
        return out
    return {**out, "authenticated": True, "user": {"username": auth.username, "role": auth.role,
                                                    "permissions": sorted(auth.actor.permissions)},
            "csrf_token": auth.csrf_token}


@router.post("/bootstrap")
def bootstrap(body: Credentials, request: Request, response: Response, db: Session = Depends(get_wdb, scope="function")):
    """First-run owner creation. Works once, from this computer only."""
    host = client_ip(request)
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        raise HTTPException(403, "First-run setup is only available from this computer.")
    if (db.scalar(select(func.count()).select_from(User)) or 0) > 0:
        raise HTTPException(409, "Setup has already been completed.")
    try:
        passwords.validate_password_strength(body.password)
    except passwords.WeakPassword as e:
        raise HTTPException(422, str(e)) from e
    user = User(username=body.username.lower(), password_hash=passwords.hash_password(body.password),
                role=Role.OWNER.value)
    db.add(user)
    db.flush()
    audit.record(db, _anon_actor("bootstrap"), "auth.owner_created", "user", user.id, {"username": user.username})
    token, sess = sessions.create_session(db, user)
    _set_cookie(response, token)
    return {"ok": True, "csrf_token": sess.csrf_token, "user": {"username": user.username, "role": user.role}}


@router.post("/login")
def login(body: Credentials, request: Request, response: Response, db: Session = Depends(get_wdb, scope="function")):
    ip = client_ip(request)
    try:
        user = sessions.authenticate(db, body.username, body.password, ip)
    except sessions.LoginLocked as e:
        db.commit()
        raise HTTPException(429, "Too many failed attempts. Try again later.",
                            headers={"Retry-After": str(e.retry_after)}) from e
    if user is None:
        audit.record(db, _anon_actor("login"), "auth.login_failed", "user", None, {"username": body.username[:64], "ip": ip})
        db.commit()  # keep the failed attempt even though we return an error
        raise HTTPException(401, "Incorrect username or password.")
    token, sess = sessions.create_session(db, user)
    audit.record(db, Actor.for_user(user.id, user.username, user.role), "auth.login", "user", user.id, {"ip": ip})
    _set_cookie(response, token)
    return {"ok": True, "csrf_token": sess.csrf_token, "user": {"username": user.username, "role": user.role}}


@router.post("/logout")
def logout(request: Request, response: Response, auth: Auth = Depends(require_signed_in)):
    with session_scope() as db:
        sessions.revoke_session(db, request.cookies.get(sessions.COOKIE_NAME))
        audit.record(db, auth.actor, "auth.logout", "user", auth.actor.user_id, {})
    response.delete_cookie(sessions.COOKIE_NAME, path="/")
    return {"ok": True}


@router.post("/change-password")
def change_password(body: PasswordChange, db: Session = Depends(get_wdb, scope="function"), auth: Auth = Depends(require_signed_in)):
    user = db.get(User, auth.actor.user_id)
    assert user is not None
    if not passwords.verify_password(body.current_password, user.password_hash):
        raise HTTPException(403, "Current password is incorrect.")
    try:
        passwords.validate_password_strength(body.new_password)
    except passwords.WeakPassword as e:
        raise HTTPException(422, str(e)) from e
    user.password_hash = passwords.hash_password(body.new_password)
    revoked = sessions.revoke_all_for_user(db, user.id)
    audit.record(db, auth.actor, "auth.password_changed", "user", user.id, {"sessions_revoked": revoked})
    return {"ok": True, "note": "All sessions were signed out. Please sign in again."}


@router.post("/users")
def create_user(body: NewUser, db: Session = Depends(get_wdb, scope="function"), auth: Auth = Depends(require(P.USERS_MANAGE))):
    try:
        passwords.validate_password_strength(body.password)
    except passwords.WeakPassword as e:
        raise HTTPException(422, str(e)) from e
    name = body.username.lower()
    if db.scalar(select(User).where(User.username == name)):
        raise HTTPException(409, "That username is taken.")
    u = User(username=name, password_hash=passwords.hash_password(body.password), role=body.role)
    db.add(u)
    db.flush()
    audit.record(db, auth.actor, "auth.user_created", "user", u.id, {"username": name, "role": body.role})
    return {"ok": True, "id": u.id, "username": name, "role": body.role}
