"""Owner-controlled settings and the safe operating mode."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from app.enums import OperatingMode
from app.models.settings import SettingRow
from app.security.permissions import Actor, P, PermissionDenied
from app.services import audit
from app.timeutil import iso, utcnow


class SettingError(ValueError):
    pass


DEFAULTS: dict[str, Any] = {
    "operating_mode": OperatingMode.DEVELOPMENT.value,
    # Zero means "no discretionary paid spending permitted" until the owner sets a limit.
    "budget": {
        "daily_usd": 0.0,
        "monthly_usd": 0.0,
        "per_video_usd": 0.0,
        "daily_model_requests": 0,
        "daily_provider_requests": 0,
        "daily_image_generations": 0,
        "daily_audio_generations": 0,
        "max_task_attempts": 3,
        "max_concurrent_tasks": 2,
    },
    "autonomy": {
        "comment_reply_policy": "draft_only",  # draft_only | auto_low_risk
        "autopublish_channels": [],  # channel slugs allowed to publish without per-video approval
        "max_publishes_per_day_per_channel": 0,
    },
    "emergency_pause": {"active": False, "since": None, "by": None, "reason": None},
    # provider id -> {"api_access_confirmed": bool, "commercial_use_confirmed": bool, "note": str, "confirmed_at": iso}
    "provider_attestations": {},
}

# Settings that only the human owner may change. (Pausing is separately open to the CEO/system.)
OWNER_ONLY_KEYS = {"operating_mode", "budget", "autonomy", "provider_attestations"}

_BUDGET_NUMERIC = {
    "daily_usd", "monthly_usd", "per_video_usd", "daily_model_requests", "daily_provider_requests",
    "daily_image_generations", "daily_audio_generations", "max_task_attempts", "max_concurrent_tasks",
}


def get(db: Session, key: str) -> Any:
    if key not in DEFAULTS:
        raise SettingError(f"unknown setting '{key}'")
    row = db.get(SettingRow, key)
    base = deepcopy(DEFAULTS[key])
    if row is None:
        return base
    if isinstance(base, dict) and isinstance(row.value, dict):
        base.update(row.value)
        return base
    return row.value


def get_all(db: Session) -> dict[str, Any]:
    return {k: get(db, k) for k in DEFAULTS}


def _validate(key: str, value: Any) -> Any:
    if key == "operating_mode":
        try:
            return OperatingMode(value).value
        except ValueError as e:
            raise SettingError(f"invalid operating mode: {value!r}") from e
    if key == "budget":
        if not isinstance(value, dict):
            raise SettingError("budget must be an object")
        out = {}
        for k, v in value.items():
            if k not in _BUDGET_NUMERIC:
                raise SettingError(f"unknown budget field '{k}'")
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                raise SettingError(f"budget field '{k}' must be a non-negative number")
            out[k] = v
        return out
    if key == "autonomy":
        if not isinstance(value, dict):
            raise SettingError("autonomy must be an object")
        allowed = {"comment_reply_policy", "autopublish_channels", "max_publishes_per_day_per_channel"}
        extra = set(value) - allowed
        if extra:
            raise SettingError(f"unknown autonomy field(s): {sorted(extra)}")
        if value.get("comment_reply_policy", "draft_only") not in ("draft_only", "auto_low_risk"):
            raise SettingError("comment_reply_policy must be draft_only or auto_low_risk")
        return value
    if key == "provider_attestations":
        if not isinstance(value, dict):
            raise SettingError("provider_attestations must be an object")
        out = {}
        for pid, att in value.items():
            if not isinstance(att, dict) or set(att) - {"api_access_confirmed", "commercial_use_confirmed", "note"}:
                raise SettingError(f"invalid attestation for '{pid}'")
            if not all(isinstance(att.get(k, False), bool) for k in ("api_access_confirmed", "commercial_use_confirmed")):
                raise SettingError("attestation flags must be true or false")
            out[str(pid)] = {"api_access_confirmed": bool(att.get("api_access_confirmed")),
                             "commercial_use_confirmed": bool(att.get("commercial_use_confirmed")),
                             "note": str(att.get("note", ""))[:500], "confirmed_at": iso(utcnow())}
        return out
    raise SettingError(f"setting '{key}' cannot be written directly")


def set_value(db: Session, actor: Actor, key: str, value: Any) -> Any:
    """Change an owner-controlled setting. Only the human owner may do so."""
    if key not in OWNER_ONLY_KEYS:
        raise SettingError(f"setting '{key}' cannot be written directly")
    actor.require(P.SETTINGS_WRITE)
    if not actor.is_owner:
        raise PermissionDenied(actor, P.SETTINGS_WRITE)
    clean = _validate(key, value)
    before = get(db, key)
    merged = ({**before, **clean} if key != "provider_attestations" else clean) if isinstance(clean, dict) else clean
    row = db.get(SettingRow, key)
    if row is None:
        db.add(SettingRow(key=key, value=merged, updated_by=actor.label, updated_at=utcnow()))
    else:
        row.value = merged
        row.updated_by = actor.label
    db.flush()
    audit.record(db, actor, "settings.changed", "setting", key, {"before": before, "after": merged})
    return merged


def _write_internal(db: Session, actor: Actor, key: str, value: Any) -> None:
    row = db.get(SettingRow, key)
    if row is None:
        db.add(SettingRow(key=key, value=value, updated_by=actor.label, updated_at=utcnow()))
    else:
        row.value = value
        row.updated_by = actor.label
    db.flush()


def operating_mode(db: Session) -> OperatingMode:
    return OperatingMode(get(db, "operating_mode"))


def emergency_pause_state(db: Session) -> dict[str, Any]:
    return get(db, "emergency_pause")


def is_emergency_paused(db: Session) -> bool:
    return bool(emergency_pause_state(db).get("active"))
