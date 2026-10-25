"""Content ideas: proposing, assessing, ordering and approving what to make next.

The assessment uses only facts the company holds:

* **distinctness** is measured against this channel's existing videos and other open ideas;
* **feasibility** is read from the live provider registry (what could be produced *today*);
* **evidence** counts the sources actually recorded for the idea;
* **audience relevance** and **cost/time** are the proposer's own judgement or estimate and are labelled that way;
* **demand** (search interest, trend strength) is *unavailable* unless someone recorded it with a source. It is never
  estimated here.

The resulting priority is an ordering aid that shows its working. It is not a prediction of views.
"""

from __future__ import annotations

import re
from datetime import date
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import VideoStage
from app.models.org import Channel
from app.models.production import CreativeBrief
from app.models.research import ContentIdea
from app.models.videos import Video
from app.providers import base as providers
from app.research import notes as notes_svc
from app.research import untrusted
from app.security.permissions import Actor, ActorKind, P, PermissionDenied
from app.services import audit, org, videos
from app.services import settings as settings_svc
from app.timeutil import iso, utcnow

# what an idea may require, mapped to the provider capability that would supply it
REQUIREMENTS: dict[str, tuple[str, str, str]] = {
    "instrumental_music": ("local-procedural", "music.instrumental", "an instrumental soundtrack"),
    "sung_vocals": ("elevenlabs", "music.sung", "sung vocals"),
    "narration": ("elevenlabs", "speech.tts", "a narrated voice"),
    "illustrated_scenes": ("local-procedural", "scene.image", "illustrated scenes"),
    "ai_images": ("elevenlabs", "image.generate", "AI-generated images"),
    "llm_drafting": ("anthropic", "llm.reasoning", "model-drafted writing"),
    "video_render": ("local-render", "video.render", "video assembly"),
}
OPEN_STATUSES = ("PROPOSED", "ASSESSED", "APPROVED", "PARKED")
_STOP = frozenset("a an the and or of to for in on with by from at as is are be this that these those it its into your you our my how what why when "
                  "who which about video videos song songs happy new best".split())
DUPLICATE_TITLE_RATIO = 0.85
DUPLICATE_TOPIC = 0.75
OVERLAP_TOPIC = 0.45
STATUS_ORDER = {"APPROVED": 0, "ASSESSED": 1, "PROPOSED": 2, "PARKED": 3}

WEIGHTS = {"distinctness": 3.0, "feasibility": 2.0, "evidence": 2.0, "audience_relevance": 2.0, "cost": 1.0}


class IdeaError(ValueError):
    pass


def stem(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def topic_terms(text: str, limit: int = 3000) -> set[str]:
    return {stem(w) for w in re.findall(r"[a-z0-9']+", untrusted.clean(text, limit)[0].lower()) if w not in _STOP and len(w) > 2}


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def overlap(title_a: str, concept_a: str, title_b: str, concept_b: str) -> dict[str, Any]:
    ta, tb = " ".join(sorted(topic_terms(title_a))), " ".join(sorted(topic_terms(title_b)))
    title_ratio = SequenceMatcher(None, ta, tb).ratio() if ta and tb else 0.0
    topic = _jaccard(topic_terms(f"{title_a} {concept_a}"), topic_terms(f"{title_b} {concept_b}"))
    verdict = "duplicate" if title_ratio >= DUPLICATE_TITLE_RATIO or topic >= DUPLICATE_TOPIC else "overlaps" if topic >= OVERLAP_TOPIC else "distinct"
    return {"title_similarity": round(title_ratio, 3), "topic_overlap": round(topic, 3), "verdict": verdict}


def _can_propose(actor: Actor) -> bool:
    return actor.is_owner or actor.can(P.TASK_WORK)


def propose(db: Session, actor: Actor, channel_slug: str, *, title: str, concept: str, audience: str, rationale: str = "",
            audience_relevance: int | None = None, est_cost_usd: float | None = None, est_production_minutes: int | None = None,
            requires: list[str] | None = None, risks: list[str] | None = None) -> ContentIdea:
    if not _can_propose(actor):
        raise PermissionDenied(actor, "task:work")
    ch = db.scalar(select(Channel).where(Channel.slug == channel_slug))
    if ch is None:
        raise IdeaError(f"unknown channel '{channel_slug}'")
    title, concept, audience = (untrusted.clean(v, n)[0] for v, n in ((title, 200), (concept, 3000), (audience, 300)))
    if len(title) < 5 or len(concept) < 20 or not audience:
        raise IdeaError("an idea needs a title (5+ characters), a concept (20+ characters) and an audience")
    if audience_relevance is not None and not (isinstance(audience_relevance, int) and not isinstance(audience_relevance, bool) and 1 <= audience_relevance <= 5):
        raise IdeaError("audience relevance is a judgement from 1 to 5")
    if est_cost_usd is not None and (est_cost_usd < 0 or est_cost_usd > 10_000):
        raise IdeaError("the cost estimate must be between 0 and 10000 USD")
    if est_production_minutes is not None and not 1 <= est_production_minutes <= 100_000:
        raise IdeaError("the production-time estimate must be between 1 and 100000 minutes")
    reqs = sorted(set(requires or []))
    unknown = [r for r in reqs if r not in REQUIREMENTS]
    if unknown:
        raise IdeaError(f"unknown requirement(s) {unknown}; choose from {sorted(REQUIREMENTS)}")
    idea = ContentIdea(
        channel_id=ch.id, title=title, concept=concept, audience=audience, rationale=untrusted.clean(rationale, 2000)[0],
        origin="owner" if actor.is_owner else "agent" if actor.kind is ActorKind.AGENT else "manual", status="PROPOSED",
        audience_relevance=audience_relevance, est_cost_micro_usd=None if est_cost_usd is None else round(est_cost_usd * 1_000_000),
        est_production_minutes=est_production_minutes, requires=reqs, risks=[untrusted.clean(r, 300)[0] for r in (risks or []) if r.strip()],
        created_by=actor.label,
    )
    db.add(idea)
    db.flush()
    audit.record(db, actor, "idea.proposed", "idea", idea.id, {"channel": channel_slug, "title": title})
    return idea


def _existing_work(db: Session, idea: ContentIdea) -> list[dict[str, Any]]:
    """Things in the *same channel* a new idea could duplicate: existing videos (not demos) and other open ideas."""
    out: list[dict[str, Any]] = []
    for v in db.scalars(select(Video).where(Video.channel_id == idea.channel_id, Video.stage != VideoStage.REJECTED.value)):
        if (v.metadata_json or {}).get("demo") or v.id == idea.video_id:
            continue
        brief = db.scalar(select(CreativeBrief).where(CreativeBrief.video_id == v.id).order_by(CreativeBrief.version.desc()))
        out.append({"kind": "video", "id": v.id, "title": v.title or v.working_title,
                    "concept": str((brief.content if brief else {}).get("main_concept", ""))})
    for o in db.scalars(select(ContentIdea).where(ContentIdea.channel_id == idea.channel_id, ContentIdea.id != idea.id,
                                                  ContentIdea.status.in_(("PROPOSED", "ASSESSED", "APPROVED", "PARKED", "IN_PRODUCTION")),
                                                  ContentIdea.video_id.is_(None))):
        out.append({"kind": "idea", "id": o.id, "title": o.title, "concept": o.concept})
    return out


def distinctness(db: Session, idea: ContentIdea) -> dict[str, Any]:
    scored = []
    for other in _existing_work(db, idea):
        o = overlap(idea.title, idea.concept, other["title"], other["concept"])
        scored.append({**other, **o})
    scored.sort(key=lambda r: (r["topic_overlap"], r["title_similarity"]), reverse=True)
    order = {"distinct": 0, "overlaps": 1, "duplicate": 2}
    verdict = max((r["verdict"] for r in scored), key=lambda v: order[v], default="distinct")
    return {"verdict": verdict, "compared_against": len(scored), "nearest": scored[:3],
            "basis": "word-overlap screening against this channel's existing videos and open ideas; it cannot see meaning or other channels' catalogues"}


def feasibility(db: Session, idea: ContentIdea) -> dict[str, Any]:
    rows = []
    for key in idea.requires or []:
        provider, cap, label = REQUIREMENTS[key]
        state, detail = providers.capability_state(db, provider, cap)
        rows.append({"requirement": key, "label": label, "state": state, "ready": state == "ready", "detail": detail})
    blocking = [r for r in rows if not r["ready"]]
    verdict = "ready" if not blocking else "needs_provider"
    return {"verdict": verdict, "requirements": rows, "blocking": [r["requirement"] for r in blocking],
            "basis": "the live provider registry: a capability counts only when it is implemented, configured, attested and within budget",
            "note": "" if not blocking else "Not producible today. The idea can stay parked until the missing capability exists, or be re-scoped."}


def evidence(db: Session, idea: ContentIdea) -> dict[str, Any]:
    ch = db.get(Channel, idea.channel_id)
    ns = notes_svc.for_idea(db, idea.id)
    sources = [n for n in ns if n.kind in notes_svc.NEEDS_SUPPORTS]
    by_class = {c: sum(1 for n in sources if n.source_class == c) for c in ("authoritative", "reputable", "general", "unrated")}
    signals = [n for n in ns if n.kind in ("trend", "audience_question", "competitor")]
    needs_sources = bool((ch.policy or {}).get("source_citations_required"))  # type: ignore[union-attr]
    problems = []
    if needs_sources:
        if len(sources) < 2:
            problems.append(f"{ch.name} needs at least two recorded sources (has {len(sources)})")  # type: ignore[union-attr]
        if by_class["authoritative"] < 1:
            problems.append("at least one source should be authoritative (health authority, government or university)")
    return {
        "sources": len(sources), "by_class": by_class, "needs_sources": needs_sources, "problems": problems,
        "demand": ({"state": "recorded", "signals": len(signals), "note": "recorded observations with sources; not a forecast"}
                   if signals else {"state": "unavailable", "note": "no trend, search or audience data has been recorded; YouTube analytics are not connected"}),
        "flagged_notes": sum(1 for n in ns if n.injection_flags),
    }


def _score(db: Session, idea: ContentIdea, dist: dict, feas: dict, ev: dict) -> dict[str, Any]:
    comps: dict[str, dict[str, Any]] = {}
    comps["distinctness"] = {"value": {"distinct": 1.0, "overlaps": 0.4, "duplicate": 0.0}[dist["verdict"]], "basis": "computed",
                             "detail": dist["verdict"]}
    reqs = feas["requirements"]
    comps["feasibility"] = {"value": (sum(1 for r in reqs if r["ready"]) / len(reqs)) if reqs else None, "basis": "computed from the provider registry",
                            "detail": "no requirements stated" if not reqs else f"{sum(1 for r in reqs if r['ready'])} of {len(reqs)} ready"}
    if ev["needs_sources"]:
        comps["evidence"] = {"value": min(1.0, ev["sources"] / 3) * (1.0 if ev["by_class"]["authoritative"] else 0.5), "basis": "computed",
                             "detail": f"{ev['sources']} source(s), {ev['by_class']['authoritative']} authoritative"}
    else:
        comps["evidence"] = {"value": None, "basis": "not required for this channel", "detail": "no source requirement"}
    comps["audience_relevance"] = {"value": None if idea.audience_relevance is None else idea.audience_relevance / 5, "basis": "judgement by the proposer",
                                   "detail": "not given" if idea.audience_relevance is None else f"{idea.audience_relevance} of 5"}
    limit = float(settings_svc.get(db, "budget").get("per_video_usd", 0) or 0)
    cost_usd = None if idea.est_cost_micro_usd is None else idea.est_cost_micro_usd / 1_000_000
    if cost_usd is None:
        comps["cost"] = {"value": None, "basis": "estimate", "detail": "no estimate given"}
    elif cost_usd == 0:
        comps["cost"] = {"value": 1.0, "basis": "estimate", "detail": "estimated at no paid cost"}
    elif limit <= 0:
        comps["cost"] = {"value": 0.0, "basis": "estimate", "detail": f"estimated ${cost_usd:.2f} but the per-video budget is $0"}
    else:
        comps["cost"] = {"value": max(0.0, 1 - cost_usd / limit), "basis": "estimate", "detail": f"estimated ${cost_usd:.2f} of a ${limit:.2f} limit"}
    used = {k: c for k, c in comps.items() if c["value"] is not None}
    total_w = sum(WEIGHTS[k] for k in used)
    score = round(100 * sum(WEIGHTS[k] * c["value"] for k, c in used.items()) / total_w, 1) if total_w else None
    ineligible = dist["verdict"] == "duplicate"
    for k, c in comps.items():
        c["weight"] = WEIGHTS[k]
    return {"score": 0.0 if ineligible else score, "ineligible": ineligible,
            "ineligible_reason": "it duplicates existing work in this channel" if ineligible else "",
            "components": comps, "unavailable_components": [k for k, c in comps.items() if c["value"] is None],
            "note": "An ordering aid built from the components above. It is not a forecast of views, and judgement and estimate components are labelled as such."}


def _compute_assessment(db: Session, idea: ContentIdea) -> dict[str, Any]:
    dist, feas, ev = distinctness(db, idea), feasibility(db, idea), evidence(db, idea)
    ch = db.get(Channel, idea.channel_id)
    auto_risks = []
    if (ch.policy or {}).get("human_medical_review_required"):  # type: ignore[union-attr]
        auto_risks.append("Medical content: a person must review it before publication (always).")
    if "sung_vocals" in (idea.requires or []):
        auto_risks.append("Sung vocals must be checked by a person before the video can clear originality.")
    if ev["flagged_notes"]:
        auto_risks.append(f"{ev['flagged_notes']} research note(s) contain instruction-like text; they are treated as data only.")
    return {"distinctness": dist, "feasibility": feas, "evidence": ev, "priority": _score(db, idea, dist, feas, ev), "risks": list(idea.risks or []),
            "automatic_risks": auto_risks,
            "estimates": {"cost_usd": None if idea.est_cost_micro_usd is None else idea.est_cost_micro_usd / 1e6,
                          "production_minutes": idea.est_production_minutes, "kind": "proposer's estimate"}}


def assess(db: Session, actor: Actor, idea_id: int) -> ContentIdea:
    if not _can_propose(actor):
        raise PermissionDenied(actor, "task:work")
    idea = get(db, idea_id)
    if idea.status in ("REJECTED", "IN_PRODUCTION"):
        raise IdeaError(f"an idea that is {idea.status.lower().replace('_', ' ')} is not re-assessed")
    _store_assessment(db, actor, idea)
    return idea


def _store_assessment(db: Session, actor: Actor, idea: ContentIdea) -> dict[str, Any]:
    a = _compute_assessment(db, idea)
    idea.assessment, idea.priority_score, idea.assessed_at = a, a["priority"]["score"], utcnow()
    if idea.status == "PROPOSED":
        idea.status = "ASSESSED"
    audit.record(db, actor, "idea.assessed", "idea", idea.id, {"score": a["priority"]["score"], "distinctness": a["distinctness"]["verdict"],
                                                              "feasibility": a["feasibility"]["verdict"]})
    return a


def get(db: Session, idea_id: int) -> ContentIdea:
    idea = db.get(ContentIdea, idea_id)
    if idea is None:
        raise IdeaError("idea not found")
    return idea


def _is_research_approver(db: Session, actor: Actor) -> bool:
    if actor.is_owner:
        return True
    if actor.kind is not ActorKind.AGENT or not actor.can(P.TASK_REVIEW):
        return False
    me = org.get_agent(db, actor.id)
    dept = me.department.slug if getattr(me, "department", None) else None
    return actor.role in ("coo", "ceo") or dept == "research-growth"


def decide(db: Session, actor: Actor, idea_id: int, *, approve: bool, note: str, planned_for: str | None = None) -> ContentIdea:
    if not _is_research_approver(db, actor):
        raise PermissionDenied(actor, "idea:decide (research manager, COO, CEO or owner)")
    idea = get(db, idea_id)
    if idea.status not in ("ASSESSED", "PARKED", "PROPOSED", "APPROVED"):
        raise IdeaError(f"an idea that is {idea.status.lower().replace('_', ' ')} cannot be decided")
    if not note.strip():
        raise IdeaError("give a reason; it is kept with the decision")
    if approve:
        if idea.created_by == actor.label and not actor.is_owner:
            raise IdeaError("the author of an idea cannot approve it")
        a = _store_assessment(db, actor, idea)  # decisions always rest on a fresh assessment, never a stored one
        if a["distinctness"]["verdict"] == "duplicate":
            raise IdeaError("this idea duplicates existing work in the channel")
        if a["feasibility"]["verdict"] != "ready":
            idea.status = "PARKED"
            idea.decision_note = f"Parked: needs {', '.join(a['feasibility']['blocking'])}. {note.strip()}"
            idea.decided_by, idea.decided_at = actor.label, utcnow()
            audit.record(db, actor, "idea.parked", "idea", idea.id, {"blocking": a["feasibility"]["blocking"]})
            return idea
        if a["evidence"]["problems"]:
            raise IdeaError("evidence is insufficient: " + "; ".join(a["evidence"]["problems"]))
        if planned_for:
            try:
                d = date.fromisoformat(planned_for)
            except ValueError as e:
                raise IdeaError("planned_for must be an ISO date (YYYY-MM-DD)") from e
            if d < utcnow().date():
                raise IdeaError("planned_for cannot be in the past")
        idea.planned_for = planned_for
        idea.status = "APPROVED"
    else:
        idea.status = "REJECTED"
    idea.decided_by, idea.decided_at, idea.decision_note = actor.label, utcnow(), note.strip()
    audit.record(db, actor, "idea.approved" if approve else "idea.rejected", "idea", idea.id, {"note": note.strip(), "planned_for": planned_for})
    return idea


def start_video(db: Session, actor: Actor, idea_id: int) -> Video:
    """Turn an approved idea into a video: its sources move across and it enters the research stage."""
    actor.require(P.VIDEO_MANAGE)
    idea = get(db, idea_id)
    if idea.status != "APPROVED":
        raise IdeaError("only an approved idea can become a video")
    ch = db.get(Channel, idea.channel_id)
    v = videos.create(db, actor, ch.slug, idea.title)  # type: ignore[union-attr]
    v.metadata_json = {**(v.metadata_json or {}), "idea_id": idea.id}
    idea.video_id, idea.status = v.id, "IN_PRODUCTION"
    notes_svc.promote_to_video(db, actor, idea.id, v.id)
    videos.set_stage(db, actor, v.id, VideoStage.RESEARCH, note=f"from idea #{idea.id}")
    audit.record(db, actor, "idea.started", "idea", idea.id, {"video": v.id})
    return v


def plan(db: Session, channel_slug: str | None = None) -> list[dict[str, Any]]:
    """The content plan: per channel, approved ideas first, then assessed ones by score. Channels are never mixed."""
    q = select(ContentIdea).where(ContentIdea.status.in_(OPEN_STATUSES))
    out = []
    for ch in db.scalars(select(Channel).order_by(Channel.id)):
        if channel_slug and ch.slug != channel_slug:
            continue
        rows = list(db.scalars(q.where(ContentIdea.channel_id == ch.id)))
        rows.sort(key=lambda i: (STATUS_ORDER[i.status], -(i.priority_score or -1), i.id))
        out.append({"channel": ch.slug, "channel_name": ch.name, "items": [serialize(i, rank=n) for n, i in enumerate(rows, 1)]})
    return out


def serialize(i: ContentIdea, *, rank: int | None = None, detail: bool = False) -> dict[str, Any]:
    a = i.assessment or {}
    d: dict[str, Any] = {
        "id": i.id, "channel_id": i.channel_id, "rank": rank, "title": i.title, "concept": i.concept, "audience": i.audience, "status": i.status,
        "origin": i.origin, "priority_score": i.priority_score, "assessed_at": iso(i.assessed_at) if i.assessed_at else None,
        "planned_for": i.planned_for, "video_id": i.video_id, "requires": i.requires, "decision_note": i.decision_note,
        "decided_by": i.decided_by,
        "summary": {"distinctness": a.get("distinctness", {}).get("verdict"), "feasibility": a.get("feasibility", {}).get("verdict"),
                    "sources": a.get("evidence", {}).get("sources"), "demand": a.get("evidence", {}).get("demand", {}).get("state")},
    }
    if detail:
        d.update({"rationale": i.rationale, "audience_relevance": i.audience_relevance, "risks": i.risks, "assessment": a,
                  "created_by": i.created_by, "created_at": iso(i.created_at)})
    return d
