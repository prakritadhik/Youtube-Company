"""Thumbnail concepts: assessed on paper before any image is made.

The checks are the ones a computer can do honestly: text length and wording, whether the words are supported by the
video's own material, measured colour contrast, resemblance to recent concepts in the same channel, and (for medicine)
claim wording. "Will it get clicks" is not among them; there is no data to answer it.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.org import Channel
from app.models.research import ThumbnailConcept
from app.models.videos import Video
from app.research import ideas, seo, untrusted
from app.security.permissions import Actor, PermissionDenied
from app.services import audit
from app.timeutil import utcnow

MAX_WORDS, MAX_CHARS = 5, 32
MIN_CONTRAST = 4.5  # WCAG AA for normal text; thumbnails are small, so the stricter ordinary-text figure is used
RECENT = 5
_BEFORE_AFTER = re.compile(r"before\s*(and|/|&|-)?\s*after|miracle|transformation", re.I)


class ThumbError(ValueError):
    pass


def _rgb(hex_: str) -> tuple[int, int, int]:
    h = hex_.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def luminance(rgb: tuple[int, int, int]) -> float:
    def ch(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: str, b: str) -> float:
    la, lb = luminance(_rgb(a)), luminance(_rgb(b))
    hi, lo = max(la, lb), min(la, lb)
    return round((hi + 0.05) / (lo + 0.05), 2)


def _distance(a: str, b: str) -> float:
    return sum((x - y) ** 2 for x, y in zip(_rgb(a), _rgb(b), strict=True)) ** 0.5


def check_concept(db: Session, video: Video, *, overlay_text: str, composition: str, palette: list[str], relation_to_content: str,
                  exclude_id: int | None = None) -> list[dict[str, Any]]:
    kind = db.get(Channel, video.channel_id).kind  # type: ignore[union-attr]
    f: list[dict[str, Any]] = []
    fnd = seo.finding
    if not composition.strip():
        f.append(fnd("COMPOSITION_MISSING", "revise", "composition", "Describe what the thumbnail shows."))
    if not relation_to_content.strip():
        f.append(fnd("RELATION_MISSING", "revise", "relation_to_content", "Say how the thumbnail reflects what is actually in the video."))
    words = overlay_text.split()
    if len(words) > MAX_WORDS or len(overlay_text) > MAX_CHARS:
        f.append(fnd("OVERLAY_TOO_LONG", "revise", "overlay_text", f"{len(words)} words / {len(overlay_text)} characters; keep it to {MAX_WORDS} words and {MAX_CHARS} characters so it reads when small."))
    letters = [c for c in overlay_text if c.isalpha()]
    if len(letters) >= 6 and sum(c.isupper() for c in letters) / len(letters) > 0.8 and overlay_text.count("!") > 0:
        f.append(fnd("OVERLAY_SHOUTING", "revise", "overlay_text", "Capitals and exclamation marks together read as shouting."))
    if overlay_text and (seo._CLICKBAIT.search(overlay_text) or seo._URGENCY.search(overlay_text)):
        f.append(fnd("OVERLAY_CLICKBAIT", "revise", "overlay_text", "Clickbait or urgency wording the video cannot back up."))
    if overlay_text:
        text = seo.corpus(db, video) + " " + (video.title or video.working_title)
        distinct = seo._terms(overlay_text) - seo.GENRE_WORDS.get(kind, set())
        missing = sorted(distinct - seo._terms(text))
        if distinct and len(missing) / len(distinct) > 0.4:
            f.append(fnd("OVERLAY_NOT_SUPPORTED", "revise", "overlay_text", f"Words on the thumbnail that appear nowhere in the video's material: {', '.join(missing)}."))
    if kind == "medicine":
        if seo._MED_BLOCK.search(overlay_text) or seo._MED_BLOCK.search(composition):
            f.append(fnd("THUMBNAIL_MEDICAL_OVERCLAIM", "block", "overlay_text", "A medical claim that promises results or certainty."))
        elif seo._MED_REVISE.search(overlay_text):
            f.append(fnd("THUMBNAIL_MEDICAL_WORDING", "revise", "overlay_text", "Wording such as 'cure' can read as a claim."))
        if _BEFORE_AFTER.search(composition) or _BEFORE_AFTER.search(overlay_text):
            f.append(fnd("THUMBNAIL_BEFORE_AFTER", "revise", "composition", "Before-and-after or miracle imagery implies results the video cannot promise."))
    bad = [c for c in palette if not re.fullmatch(r"#[0-9a-fA-F]{6}", c or "")]
    if bad:
        f.append(fnd("PALETTE_INVALID", "block", "palette", f"Colours must be #RRGGBB: {', '.join(map(str, bad))}."))
    elif len(palette) >= 2:
        ratio = contrast_ratio(palette[0], palette[1])
        if ratio < MIN_CONTRAST:
            f.append(fnd("LOW_CONTRAST", "revise", "palette", f"Text colour against background has a contrast ratio of {ratio}:1; at least {MIN_CONTRAST}:1 is needed to read at small size.",
                         ratio=ratio))
    elif overlay_text:
        f.append(fnd("CONTRAST_UNASSESSED", "info", "palette", "Give a text colour and a background colour so contrast can be measured."))
    if len(palette) > 5:
        f.append(fnd("PALETTE_BUSY", "info", "palette", "More than five colours is hard to keep clear at small size."))
    if not bad and palette:
        recent = list(db.scalars(select(ThumbnailConcept).join(Video, Video.id == ThumbnailConcept.video_id).where(
            Video.channel_id == video.channel_id, Video.id != video.id, ThumbnailConcept.status.in_(("APPROVED", "CHECKED")),
        ).order_by(ThumbnailConcept.id.desc()).limit(RECENT)))
        for r in recent:
            if r.id == exclude_id or not r.palette or not all(re.fullmatch(r"#[0-9a-fA-F]{6}", c) for c in r.palette[:2]):
                continue
            same_palette = all(_distance(a, b) < 60 for a, b in zip(palette[:2], r.palette[:2], strict=False))
            sim = ideas.overlap("", composition, "", r.composition)["topic_overlap"]
            if same_palette and sim >= 0.6:
                f.append(fnd("SIMILAR_TO_RECENT", "revise", "composition",
                             f"Same colours and a very similar layout to a recent thumbnail concept (video #{r.video_id}); vary the look.", other_video=r.video_id))
                break
    return f


def get(db: Session, concept_id: int) -> ThumbnailConcept:
    c = db.get(ThumbnailConcept, concept_id)
    if c is None:
        raise ThumbError("thumbnail concept not found")
    return c


def latest(db: Session, video_id: int) -> ThumbnailConcept | None:
    return db.scalar(select(ThumbnailConcept).where(ThumbnailConcept.video_id == video_id).order_by(ThumbnailConcept.version.desc()))


def approved(db: Session, video_id: int) -> ThumbnailConcept | None:
    return db.scalar(select(ThumbnailConcept).where(ThumbnailConcept.video_id == video_id, ThumbnailConcept.status == "APPROVED")
                     .order_by(ThumbnailConcept.version.desc()))


def create(db: Session, actor: Actor, video_id: int, *, overlay_text: str, composition: str, palette: list[str], relation_to_content: str) -> ThumbnailConcept:
    if not (actor.is_owner or actor.can("task:work")):
        raise PermissionDenied(actor, "task:work")
    video = db.get(Video, video_id)
    if video is None:
        raise ThumbError("video not found")
    prev = latest(db, video_id)
    c = ThumbnailConcept(video_id=video_id, version=(prev.version + 1) if prev else 1, overlay_text=untrusted.clean(overlay_text, 80)[0],
                         composition=untrusted.clean(composition, 1500)[0], palette=[p.strip() for p in palette],
                         relation_to_content=untrusted.clean(relation_to_content, 1000)[0], created_by=actor.label)
    db.add(c)
    db.flush()
    for old in db.scalars(select(ThumbnailConcept).where(ThumbnailConcept.video_id == video_id, ThumbnailConcept.id != c.id,
                                                         ThumbnailConcept.status.in_(("DRAFT", "CHECKED", "NEEDS_REVISION")))):
        old.status = "SUPERSEDED"
    audit.record(db, actor, "thumbnail_concept.created", "thumbnail_concept", c.id, {"video": video_id, "version": c.version})
    return check(db, actor, c.id)


def check(db: Session, actor: Actor, concept_id: int) -> ThumbnailConcept:
    c = get(db, concept_id)
    if c.status in ("APPROVED", "SUPERSEDED"):
        raise ThumbError(f"a {c.status.lower()} concept is not re-checked; create a new version")
    video = db.get(Video, c.video_id)
    c.findings = check_concept(db, video, overlay_text=c.overlay_text, composition=c.composition, palette=c.palette,  # type: ignore[arg-type]
                               relation_to_content=c.relation_to_content, exclude_id=c.id)
    c.status, c.checked_at = seo.status_for(c.findings), utcnow()
    audit.record(db, actor, "thumbnail_concept.checked", "thumbnail_concept", c.id, {"status": c.status})
    return c


def approve(db: Session, actor: Actor, concept_id: int, *, note: str, waive: dict[str, str] | None = None) -> ThumbnailConcept:
    if not seo._can_approve(db, actor):
        raise PermissionDenied(actor, "thumbnail:approve (research or publishing manager, COO, CEO or owner)")
    c = get(db, concept_id)
    if c.status not in ("CHECKED", "NEEDS_REVISION"):
        raise ThumbError(f"a {c.status.lower()} concept cannot be approved")
    if c.created_by == actor.label and not actor.is_owner:
        raise ThumbError("the author of a concept cannot approve it")
    if not note.strip():
        raise ThumbError("give a reason; it is kept with the approval")
    c = check(db, actor, concept_id)
    blocking = [x for x in c.findings if x["severity"] == "block"]
    if blocking:
        raise ThumbError("blocking findings cannot be waived: " + "; ".join(x["message"] for x in blocking))
    waive = {k: v.strip() for k, v in (waive or {}).items() if v and v.strip()}
    open_ = [x for x in c.findings if x["severity"] == "revise" and x["code"] not in waive]
    if open_:
        raise ThumbError("unresolved findings: " + "; ".join(f"{x['code']} ({x['message']})" for x in open_))
    for prev in db.scalars(select(ThumbnailConcept).where(ThumbnailConcept.video_id == c.video_id, ThumbnailConcept.status == "APPROVED")):
        prev.status = "SUPERSEDED"
    c.status, c.approved_by, c.approved_at = "APPROVED", actor.label, utcnow()
    c.findings = [{**x, "waived": waive[x["code"]]} if x["code"] in waive else x for x in c.findings]
    audit.record(db, actor, "thumbnail_concept.approved", "thumbnail_concept", c.id, {"video": c.video_id, "note": note.strip(), "waived": waive})
    return c


def serialize(c: ThumbnailConcept) -> dict[str, Any]:
    return {"id": c.id, "video_id": c.video_id, "version": c.version, "status": c.status, "overlay_text": c.overlay_text, "composition": c.composition,
            "palette": c.palette, "relation_to_content": c.relation_to_content, "findings": c.findings, "created_by": c.created_by,
            "approved_by": c.approved_by, "approved_at": c.approved_at.isoformat() if c.approved_at else None,
            "note": "A concept on paper. It does not predict clicks; no performance data exists."}
