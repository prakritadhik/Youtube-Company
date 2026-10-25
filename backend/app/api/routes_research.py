"""Content plan, research register and packaging (metadata and thumbnail concepts).

Reads need the executive permission; every write is an audited action by the signed-in owner and goes through the same
services the agents use, so the same rules apply (authors cannot approve their own work, blocking findings cannot be
waived, and so on).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Auth, require
from app.db import get_db, get_wdb
from app.models.org import Channel
from app.models.research import ContentIdea, MetadataSet, ResearchNote, ThumbnailConcept
from app.models.videos import Video
from app.research import ideas, notes, seo, thumbs
from app.security.permissions import P
from app.services import gate
from app.timeutil import utcnow

router = APIRouter(prefix="/api", tags=["research"])


class IdeaIn(BaseModel):
    channel: str = Field(min_length=3, max_length=40)
    title: str = Field(min_length=1, max_length=200)
    concept: str = Field(min_length=1, max_length=3000)
    audience: str = Field(min_length=1, max_length=300)
    rationale: str = Field(default="", max_length=2000)
    audience_relevance: int | None = Field(default=None, ge=1, le=5)
    est_cost_usd: float | None = Field(default=None, ge=0, le=10_000)
    est_production_minutes: int | None = Field(default=None, ge=1, le=100_000)
    requires: list[str] = Field(default_factory=list, max_length=10)
    risks: list[str] = Field(default_factory=list, max_length=20)


class DecisionIn(BaseModel):
    approve: bool
    note: str = Field(min_length=1, max_length=2000)
    planned_for: str | None = Field(default=None, max_length=10)


class FigureIn(BaseModel):
    label: str = Field(max_length=100)
    value: str = Field(max_length=100)
    as_of: str = Field(default="", max_length=40)
    source: str = Field(default="", max_length=300)


class NoteIn(BaseModel):
    kind: str = Field(max_length=24)
    url: str = Field(default="", max_length=1000)
    title: str = Field(default="", max_length=300)
    publisher: str = Field(default="", max_length=200)
    retrieved_at: datetime | None = None
    text: str = Field(default="", max_length=20_000)
    supports: str = Field(default="", max_length=1000)
    note: str = Field(default="", max_length=1500)
    figures: list[FigureIn] = Field(default_factory=list, max_length=20)


class MetadataIn(BaseModel):
    chosen_title: str = Field(min_length=1, max_length=200)
    description: str = Field(max_length=10_000)
    tags: list[str] = Field(default_factory=list, max_length=60)
    hashtags: list[str] = Field(default_factory=list, max_length=30)
    chapters: list[dict[str, Any]] = Field(default_factory=list, max_length=60)
    playlists: list[str] = Field(default_factory=list, max_length=10)
    pinned_comment: str = Field(default="", max_length=1000)
    call_to_action: str = Field(default="", max_length=500)
    recommendation: str = Field(default=seo.HYPOTHESIS, max_length=2000)
    title_candidates: list[str] = Field(default_factory=list, max_length=12)


class ApprovalIn(BaseModel):
    note: str = Field(min_length=1, max_length=2000)
    waive: dict[str, str] = Field(default_factory=dict)


class ConceptIn(BaseModel):
    overlay_text: str = Field(default="", max_length=80)
    composition: str = Field(default="", max_length=1500)
    palette: list[str] = Field(default_factory=list, max_length=10)
    relation_to_content: str = Field(default="", max_length=1000)


def _video(db: Session, video_id: int) -> Video:
    v = db.get(Video, video_id)
    if v is None:
        raise HTTPException(404, "Video not found.")
    return v


def _idea_detail(db: Session, idea: ContentIdea) -> dict[str, Any]:
    d = ideas.serialize(idea, detail=True)
    d["notes"] = [notes.serialize(n) for n in notes.for_idea(db, idea.id)]
    return d


# --- plan and ideas -------------------------------------------------------------------------------------------------


@router.get("/plan")
def content_plan(channel: str | None = None, db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    return {"channels": ideas.plan(db, channel),
            "note": "Ordered per channel; the score is an ordering aid with its working shown, not a forecast of views. "
                    "Demand figures are unavailable unless someone recorded them with a source."}


@router.get("/ideas/{idea_id}")
def idea_detail(idea_id: int, db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    return _idea_detail(db, ideas.get(db, idea_id))


@router.post("/ideas")
def propose_idea(body: IdeaIn, db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.TASK_CREATE))):
    idea = ideas.propose(db, auth.actor, body.channel, title=body.title, concept=body.concept, audience=body.audience, rationale=body.rationale,
                         audience_relevance=body.audience_relevance, est_cost_usd=body.est_cost_usd, est_production_minutes=body.est_production_minutes,
                         requires=body.requires, risks=body.risks)
    ideas.assess(db, auth.actor, idea.id)
    return _idea_detail(db, idea)


@router.post("/ideas/{idea_id}/assess")
def assess_idea(idea_id: int, db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.TASK_CREATE))):
    return _idea_detail(db, ideas.assess(db, auth.actor, idea_id))


@router.post("/ideas/{idea_id}/decision")
def decide_idea(idea_id: int, body: DecisionIn, db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.VIDEO_MANAGE))):
    return _idea_detail(db, ideas.decide(db, auth.actor, idea_id, approve=body.approve, note=body.note, planned_for=body.planned_for))


@router.post("/ideas/{idea_id}/start")
def start_idea(idea_id: int, db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.VIDEO_MANAGE))):
    v = ideas.start_video(db, auth.actor, idea_id)
    return {"video_id": v.id, "stage": v.stage}


@router.post("/ideas/{idea_id}/notes")
def add_idea_note(idea_id: int, body: NoteIn, db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.TASK_CREATE))):
    n = notes.add_note(db, auth.actor, kind=body.kind, retrieved_at=body.retrieved_at or utcnow(), method="owner_provided", url=body.url, title=body.title,
                       publisher=body.publisher, text=body.text, supports=body.supports, note=body.note, figures=[f.model_dump() for f in body.figures],
                       idea_id=idea_id)
    return notes.serialize(n)


@router.get("/research-notes")
def research_notes(idea_id: int | None = None, video_id: int | None = None, db: Session = Depends(get_db),
                   auth: Auth = Depends(require(P.EXEC_READ))):
    q = select(ResearchNote).order_by(ResearchNote.id.desc()).limit(200)
    if idea_id is not None:
        q = q.where(ResearchNote.idea_id == idea_id)
    if video_id is not None:
        q = q.where(ResearchNote.video_id == video_id)
    return {"notes": [notes.serialize(n) for n in db.scalars(q)],
            "note": "Text in the register comes from outside the company. It is stored and shown as data and is never followed as an instruction."}


# --- packaging ------------------------------------------------------------------------------------------------------


@router.get("/videos/{video_id}/packaging")
def packaging(video_id: int, db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    v = _video(db, video_id)
    sets = list(db.scalars(select(MetadataSet).where(MetadataSet.video_id == video_id).order_by(MetadataSet.version.desc())))
    concepts = list(db.scalars(select(ThumbnailConcept).where(ThumbnailConcept.video_id == video_id).order_by(ThumbnailConcept.version.desc())))
    bounds = seo.boundaries(db, v)
    chapters, why = seo.chapters_from(bounds)
    ch = db.get(Channel, v.channel_id)
    approved = seo.approved(db, video_id)
    return {
        "video_id": video_id, "channel": ch.slug if ch else None,
        "metadata": {"approved": seo.serialize(approved) if approved else None, "latest": seo.serialize(sets[0]) if sets else None,
                     "history": [seo.serialize(s, detail=False) for s in sets], "matches_video": bool(approved and seo.matches_video(approved, v))},
        "thumbnail_concept": {"approved": thumbs.serialize(next((c for c in concepts if c.status == "APPROVED"), None)) if any(c.status == "APPROVED" for c in concepts) else None,
                              "latest": thumbs.serialize(concepts[0]) if concepts else None},
        "chapters": {"available": bool(chapters), "reason": why, "timing": bounds["kind"], "proposed": chapters},
        "limits": seo.LIMITS, "limits_basis": seo.LIMITS_BASIS,
        "gate_issues": [i for i in gate.evaluate(db, v, purpose="clear").as_dict()["issues"] if i["code"].startswith("METADATA")],
        "unavailable": ["search volume and keyword demand", "historical title or thumbnail performance", "controlled title/thumbnail tests (need YouTube analytics)"],
    }


@router.post("/videos/{video_id}/metadata/generate")
def generate_metadata(video_id: int, db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.VIDEO_MANAGE))):
    return seo.serialize(seo.generate(db, auth.actor, video_id))


@router.post("/videos/{video_id}/metadata")
def create_metadata(video_id: int, body: MetadataIn, db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.VIDEO_MANAGE))):
    ms = seo.create_set(db, auth.actor, video_id, chosen_title=body.chosen_title, description=body.description, tags=body.tags, hashtags=body.hashtags,
                        chapters=body.chapters, playlists=body.playlists, pinned_comment=body.pinned_comment, call_to_action=body.call_to_action,
                        recommendation=body.recommendation, title_candidates=body.title_candidates, generated_by="manual")
    return seo.serialize(ms)


@router.post("/metadata/{set_id}/check")
def check_metadata(set_id: int, db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.VIDEO_MANAGE))):
    return seo.serialize(seo.check(db, auth.actor, set_id))


@router.post("/metadata/{set_id}/approve")
def approve_metadata(set_id: int, body: ApprovalIn, db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.VIDEO_MANAGE))):
    return seo.serialize(seo.approve(db, auth.actor, set_id, note=body.note, waive=body.waive))


@router.post("/videos/{video_id}/thumbnail-concepts")
def create_concept(video_id: int, body: ConceptIn, db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.VIDEO_MANAGE))):
    return thumbs.serialize(thumbs.create(db, auth.actor, video_id, overlay_text=body.overlay_text, composition=body.composition, palette=body.palette,
                                          relation_to_content=body.relation_to_content))


@router.post("/thumbnail-concepts/{concept_id}/approve")
def approve_concept(concept_id: int, body: ApprovalIn, db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.VIDEO_MANAGE))):
    return thumbs.serialize(thumbs.approve(db, auth.actor, concept_id, note=body.note, waive=body.waive))
