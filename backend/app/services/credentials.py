"""Keys and client settings the owner pastes into the dashboard, stored encrypted, with the ``.env`` file as the fallback.

Why: editing ``.env`` and restarting is the part of "connecting" that goes wrong for most owners. A value saved here is read by the
API and the worker at the moment it is needed, so nothing needs restarting. A value in ``.env`` still works; the dashboard value wins.

Safety:
* only the owner can save or remove one; every change is audited **without the value**;
* a secret is never returned by any API: only whether it is set, where it comes from and its last four characters;
* values are encrypted with the key in ``data/secrets/token.key`` (see ``app/youtube/crypto.py`` for what that does and does not protect against);
* a value that is read is registered with the log redaction, so it is masked if it ever reaches a log or an error message.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import session_scope
from app.models.credentials import Credential
from app.security import redaction
from app.security.permissions import Actor, P, PermissionDenied
from app.services import audit
from app.timeutil import utcnow
from app.youtube.crypto import KeyMissing, TokenBox


class CredentialError(ValueError):
    pass


@dataclass(frozen=True)
class Spec:
    service: str
    label: str
    secret: bool  # a secret is never shown again; a public identifier (the Google client ID) is


SPECS: dict[str, Spec] = {
    "elevenlabs_api_key": Spec("elevenlabs", "ElevenLabs API key", True),
    "google_oauth_client_id": Spec("youtube", "Google OAuth client ID", False),
    "google_oauth_client_secret": Spec("youtube", "Google OAuth client secret", True),
}


def _env(name: str) -> str | None:
    v = getattr(get_settings(), name, None)
    if v is None:
        return None
    raw = v.get_secret_value() if hasattr(v, "get_secret_value") else str(v)
    return raw.strip() or None


def _stored(db: Session, name: str) -> tuple[str | None, str | None]:
    """``(value, problem)``: the saved value, or why it cannot be read."""
    row = db.get(Credential, name)
    if row is None:
        return None, None
    try:
        return TokenBox().decrypt(row.value_enc), None
    except KeyMissing as e:
        return None, str(e)


def get(name: str, db: Session | None = None) -> str | None:
    """The value to use: the dashboard's, else ``.env``'s, else None. Never raises for a missing table or key (the caller sees 'not set')."""
    if name not in SPECS:
        return _env(name)
    value: str | None = None
    try:
        if db is not None:
            value, _ = _stored(db, name)
        else:
            with session_scope(write=False) as s:
                value, _ = _stored(s, name)
    except Exception:  # noqa: BLE001 - before the first migration there is no table; .env still works
        value = None
    value = value or _env(name)
    if value and SPECS[name].secret:
        redaction.register_secrets([value])
    return value


def source(name: str, db: Session | None = None) -> str | None:
    """``'dashboard'``, ``'env'`` or None."""
    try:
        if db is not None:
            has = db.get(Credential, name) is not None
        else:
            with session_scope(write=False) as s:
                has = s.get(Credential, name) is not None
    except Exception:  # noqa: BLE001
        has = False
    if has:
        return "dashboard"
    return "env" if _env(name) else None


def register_all() -> None:
    """Mask every saved secret in logs. Called when a process starts."""
    for name, spec in SPECS.items():
        if spec.secret:
            get(name)


def _validate(name: str, value: str) -> str:
    v = (value or "").strip()
    if not v:
        raise CredentialError("Paste the value first.")
    if any(c.isspace() for c in v):
        raise CredentialError("The value contains a space or a line break. Copy it again, without anything around it.")
    if not 8 <= len(v) <= 600:
        raise CredentialError("That does not look like a real key (it is too short or too long).")
    if name == "google_oauth_client_id" and not v.endswith(".apps.googleusercontent.com"):
        raise CredentialError("A Google OAuth client ID ends with .apps.googleusercontent.com. Copy the 'Client ID' (not the project ID or the secret).")
    return v


def _require_owner(actor: Actor) -> None:
    actor.require(P.SETTINGS_WRITE)
    if not actor.is_owner:
        raise PermissionDenied(actor, "only the owner can change credentials")


def save(db: Session, actor: Actor, name: str, value: str) -> dict:
    _require_owner(actor)
    if name not in SPECS:
        raise CredentialError(f"'{name}' is not a setting that can be saved here.")
    v = _validate(name, value)
    spec = SPECS[name]
    row = db.get(Credential, name)
    hint = v[-4:] if spec.secret else ""
    enc = TokenBox().encrypt(v)
    if row is None:
        db.add(Credential(name=name, value_enc=enc, hint=hint, updated_by=actor.label, updated_at=utcnow()))
    else:
        row.value_enc, row.hint, row.updated_by, row.updated_at = enc, hint, actor.label, utcnow()
    db.flush()
    if spec.secret:
        redaction.register_secrets([v])
    audit.record(db, actor, "credential.saved", "credential", name, {"service": spec.service, "ends_with": hint or None})  # never the value
    return {"name": name, "set": True}


def remove(db: Session, actor: Actor, name: str) -> dict:
    _require_owner(actor)
    row = db.get(Credential, name)
    if row is None:
        raise CredentialError("Nothing is saved in the dashboard under that name. (A value in .env, if any, is changed by editing .env.)")
    db.delete(row)
    db.flush()
    audit.record(db, actor, "credential.removed", "credential", name, {"service": SPECS[name].service})
    return {"name": name, "set": False}


def describe(db: Session, name: str) -> dict:
    spec = SPECS[name]
    row = db.get(Credential, name)
    _, problem = _stored(db, name) if row is not None else (None, None)
    src = "dashboard" if row is not None and problem is None else ("env" if _env(name) else None)
    shown = None
    if src == "dashboard" and not spec.secret:
        shown = get(name, db)
    elif src == "dashboard":
        shown = f"…{row.hint}" if row and row.hint else "saved"
    elif src == "env" and not spec.secret:
        shown = _env(name)
    elif src == "env":
        shown = "set in .env"
    return {"name": name, "label": spec.label, "service": spec.service, "secret": spec.secret, "set": src is not None, "source": src, "shown": shown,
            "problem": problem, "updated_at": row.updated_at.isoformat() if row else None}
