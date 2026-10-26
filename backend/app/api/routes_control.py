from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import Auth, require
from app.api.schemas import PauseRequest, ResumeRequest, SettingValue
from app.db import get_db, get_wdb
from app.security.permissions import P
from app.services import control, org
from app.services import settings as settings_svc

router = APIRouter(prefix="/api", tags=["control"])


@router.get("/settings")
def get_settings_all(db: Session = Depends(get_db), auth: Auth = Depends(require(P.SETTINGS_READ))):
    return {"settings": settings_svc.get_all(db),
            "notes": {"budget": "A limit of 0 means no discretionary paid use is permitted.",
                      "operating_mode": "DEVELOPMENT disables real publishing and public replies."}}


@router.put("/settings/{key}")
def put_setting(key: str, body: SettingValue, db: Session = Depends(get_wdb, scope="function"),
                auth: Auth = Depends(require(P.SETTINGS_WRITE))):
    try:
        value = settings_svc.set_value(db, auth.actor, key, body.value)
    except settings_svc.SettingError as e:
        raise HTTPException(422, str(e)) from e
    return {"key": key, "value": value}


@router.get("/control/status")
def control_status(db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    return {"operating_mode": settings_svc.operating_mode(db).value,
            "emergency_pause": settings_svc.emergency_pause_state(db), "what_pause_does": control.pause_report(db)}


@router.post("/control/pause")
def pause(body: PauseRequest, db: Session = Depends(get_wdb, scope="function"), auth: Auth = Depends(require(P.CONTROL_PAUSE))):
    return control.pause(db, auth.actor, body.reason)


@router.post("/control/resume")
def resume(body: ResumeRequest, db: Session = Depends(get_wdb, scope="function"), auth: Auth = Depends(require(P.CONTROL_RESUME))):
    control.resume(db, auth.actor, body.note)
    return {"ok": True}


@router.get("/system/agents/{slug}")
def agent_definition(slug: str, db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    """Role definition shown on the Organization page (responsibilities, tools, acceptance criteria)."""
    try:
        a = org.get_agent(db, slug)
    except org.UnknownAgent as e:
        raise HTTPException(404, "Unknown role.") from e
    return {"slug": a.slug, "name": a.name, "level": a.level, "responsibilities": a.responsibilities,
            "required_inputs": a.required_inputs, "output_format": a.output_format,
            "acceptance_criteria": a.acceptance_criteria, "allowed_tools": a.allowed_tools,
            "execution_limits": a.execution_limits, "uses_ai_model": a.uses_ai_model}
