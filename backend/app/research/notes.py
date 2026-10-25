"""The research register: sources, topic facts, trends, competitor observations and audience questions.

Rules that keep it honest:

* a source needs a URL, a retrieval date and the claim it supports; dates cannot be in the future;
* any number (views, subscribers, search volume, ranking ...) must carry its own source and date, otherwise it is
  refused. The company never estimates these itself;
* ``method`` says how the text got here: ``fetched`` (by the safe fetcher, with a stored snapshot and hash),
  ``owner_provided`` (typed by the owner) or ``manual_entry`` (recorded by a person or agent from a source they
  consulted). A note cannot claim a method it did not use;
* all text is untrusted data: cleaned, capped, scanned and quoted, never followed.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.org import Channel
from app.models.production import SourceRecord
from app.models.research import ContentIdea, ResearchNote
from app.models.videos import Video
from app.production import scripts
from app.research import untrusted
from app.research.fetch import Fetched
from app.security.permissions import Actor, P
from app.services import audit
from app.storage import write_json_artifact
from app.timeutil import utcnow

KINDS = ("source", "topic_info", "trend", "competitor", "audience_question")
NEEDS_URL = ("source", "topic_info", "trend", "competitor")
NEEDS_SUPPORTS = ("source", "topic_info")
METHODS = ("owner_provided", "manual_entry")  # 'fetched' is only created by record_fetched

# A *heuristic* on the web address, offered to help a reviewer. A person still judges the source.
_AUTHORITATIVE_HOSTS = ("who.int", "nih.gov", "cdc.gov", "fda.gov", "ema.europa.eu", "nhs.uk", "nice.org.uk", "cochranelibrary.com",
                        "medlineplus.gov", "ncbi.nlm.nih.gov")
_AUTHORITATIVE_SUFFIX = (".gov", ".edu", ".mil", ".int")
_AUTHORITATIVE_PATTERN = re.compile(r"\.(gov|gob|go|nic|edu|ac)\.[a-z]{2}$|\.gov\.[a-z]{2}$")
_REPUTABLE_HOSTS = ("mayoclinic.org", "clevelandclinic.org", "nature.com", "thelancet.com", "bmj.com", "jamanetwork.com", "nejm.org",
                    "sciencedirect.com", "springer.com", "wiley.com", "britannica.com", "who.int")


class ResearchError(ValueError):
    pass


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".")


def classify_source(url: str) -> str:
    """``authoritative`` / ``reputable`` / ``general`` / ``unrated`` from the host alone. A heuristic, never a verdict."""
    host = host_of(url)
    if not host:
        return "unrated"
    if any(host == h or host.endswith("." + h) for h in _AUTHORITATIVE_HOSTS) or host.endswith(_AUTHORITATIVE_SUFFIX) \
            or _AUTHORITATIVE_PATTERN.search(host):
        return "authoritative"
    if any(host == h or host.endswith("." + h) for h in _REPUTABLE_HOSTS):
        return "reputable"
    return "general"


def _check_figures(figures: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out = []
    for i, f in enumerate(figures or [], 1):
        if not isinstance(f, dict):
            raise ResearchError(f"figure {i} must be an object")
        label, value, as_of, source = (str(f.get(k, "")).strip() if f.get(k) is not None else "" for k in ("label", "value", "as_of", "source"))
        if not label or not value:
            raise ResearchError(f"figure {i} needs a label and a value")
        if not as_of or not source:
            raise ResearchError(
                f"figure '{label}' needs the date it applies to and where it came from; the company does not estimate "
                "views, subscribers, rankings or search volume itself")
        out.append({"label": label[:100], "value": value[:100], "as_of": as_of[:40], "source": source[:300]})
    return out


def _resolve_scope(db: Session, channel_slug: str | None, idea_id: int | None, video_id: int | None) -> tuple[int | None, int | None, int | None]:
    channel_id = None
    if channel_slug:
        ch = db.scalar(select(Channel).where(Channel.slug == channel_slug))
        if ch is None:
            raise ResearchError(f"unknown channel '{channel_slug}'")
        channel_id = ch.id
    if idea_id is not None:
        idea = db.get(ContentIdea, idea_id)
        if idea is None:
            raise ResearchError("idea not found")
        if channel_id is not None and idea.channel_id != channel_id:
            raise ResearchError("that idea belongs to a different channel")
        channel_id = idea.channel_id
    if video_id is not None:
        v = db.get(Video, video_id)
        if v is None:
            raise ResearchError("video not found")
        if channel_id is not None and v.channel_id != channel_id:
            raise ResearchError("that video belongs to a different channel")
        channel_id = v.channel_id
    return channel_id, idea_id, video_id


def _common(db: Session, actor: Actor, *, kind: str, url: str, title: str, publisher: str, retrieved_at: datetime, method: str, text: str,
            supports: str, note: str, figures: list[dict[str, Any]] | None, channel_slug: str | None, idea_id: int | None,
            video_id: int | None, content_sha256: str | None = None, snapshot_ref: str | None = None) -> ResearchNote:
    if kind not in KINDS:
        raise ResearchError(f"kind must be one of {', '.join(KINDS)}")
    if retrieved_at > utcnow():
        raise ResearchError("the retrieval date cannot be in the future")
    if kind in NEEDS_URL and not re.match(r"^https?://[^\s/$.?#][^\s]*$", url or ""):
        raise ResearchError(f"a {kind.replace('_', ' ')} needs an http(s) URL")
    if kind in NEEDS_SUPPORTS and not supports.strip():
        raise ResearchError("say which claim this source supports")
    if kind == "audience_question" and not (text or "").strip():
        raise ResearchError("record the question itself")
    channel_id, idea_id, video_id = _resolve_scope(db, channel_slug, idea_id, video_id)
    if channel_id is None and idea_id is None and video_id is None:
        raise ResearchError("a note belongs to a channel, an idea or a video")
    figs = _check_figures(figures)
    excerpt, _cut = untrusted.clean(text)
    flags = untrusted.injection_flags(" ".join((text or "", title or "", note or "", supports or "")))
    n = ResearchNote(
        channel_id=channel_id, idea_id=idea_id, video_id=video_id, kind=kind, url=url or "", title=untrusted.clean(title, 300)[0],
        publisher=untrusted.clean(publisher, 200)[0], retrieved_at=retrieved_at, method=method, excerpt=excerpt,
        content_sha256=content_sha256, snapshot_ref=snapshot_ref, supports=untrusted.clean(supports, 1000)[0],
        note=untrusted.clean(note, 1500)[0], figures=figs, source_class=classify_source(url) if url else "unrated",
        injection_flags=flags, untrusted=True, created_by=actor.label,
    )
    db.add(n)
    db.flush()
    audit.record(db, actor, "research.note_added", "research_note", n.id,
                 {"kind": kind, "method": method, "url": url, "flags": flags, "idea": idea_id, "video": video_id})
    return n


def add_note(db: Session, actor: Actor, *, kind: str, retrieved_at: datetime, method: str = "manual_entry", url: str = "", title: str = "",
             publisher: str = "", text: str = "", supports: str = "", note: str = "", figures: list[dict[str, Any]] | None = None,
             channel_slug: str | None = None, idea_id: int | None = None, video_id: int | None = None) -> ResearchNote:
    """Record something a person or agent read. For pages the safe fetcher retrieved use ``record_fetched``."""
    if not actor.is_owner:
        actor.require(P.TASK_WORK)
    if method not in METHODS:
        raise ResearchError("method must be 'owner_provided' or 'manual_entry'; 'fetched' is recorded only by the fetcher")
    if method == "owner_provided" and not actor.is_owner:
        raise ResearchError("only the owner can record a note as owner-provided")
    return _common(db, actor, kind=kind, url=url, title=title, publisher=publisher, retrieved_at=retrieved_at, method=method, text=text,
                   supports=supports, note=note, figures=figures, channel_slug=channel_slug, idea_id=idea_id, video_id=video_id)


def record_fetched(db: Session, actor: Actor, fetched: Fetched, *, kind: str = "source", supports: str = "", note: str = "",
                   publisher: str = "", channel_slug: str | None = None, idea_id: int | None = None, video_id: int | None = None) -> ResearchNote:
    """Store a page the safe fetcher retrieved: a hashed snapshot on disk and the cleaned excerpt in the register."""
    actor.require(P.TASK_WORK)
    actor.require_tool("web.research")
    cleaned, _ = untrusted.clean(fetched.text, 200_000)
    n = _common(db, actor, kind=kind, url=fetched.final_url, title=fetched.title, publisher=publisher or host_of(fetched.final_url),
                retrieved_at=fetched.retrieved_at, method="fetched",
                text=cleaned, supports=supports, note=note, figures=None, channel_slug=channel_slug, idea_id=idea_id, video_id=video_id,
                content_sha256=fetched.sha256)
    ref = write_json_artifact(f"research/{n.id}", "snapshot.json", {
        "requested_url": fetched.url, "final_url": fetched.final_url, "status": fetched.status, "content_type": fetched.content_type,
        "retrieved_at": fetched.retrieved_at.isoformat(), "raw_sha256": fetched.sha256, "raw_bytes": fetched.raw_bytes,
        "truncated": fetched.truncated, "robots": fetched.robots, "title": fetched.title, "text": cleaned,
        "note": "Untrusted external text, stored as data only.",
    })
    n.snapshot_ref = ref
    return n


def for_idea(db: Session, idea_id: int) -> list[ResearchNote]:
    return list(db.scalars(select(ResearchNote).where(ResearchNote.idea_id == idea_id).order_by(ResearchNote.id)))


def for_video(db: Session, video_id: int) -> list[ResearchNote]:
    return list(db.scalars(select(ResearchNote).where(ResearchNote.video_id == video_id).order_by(ResearchNote.id)))


def promote_to_video(db: Session, actor: Actor, idea_id: int, video_id: int) -> list[SourceRecord]:
    """Copy an idea's sources into the video's source list, where scripts can cite them as ``[S<id>]``."""
    actor.require(P.VIDEO_MANAGE)
    idea, video = db.get(ContentIdea, idea_id), db.get(Video, video_id)
    if idea is None or video is None:
        raise ResearchError("idea or video not found")
    if idea.channel_id != video.channel_id:
        raise ResearchError("the idea and the video belong to different channels")
    out: list[SourceRecord] = []
    for n in for_idea(db, idea_id):
        n.video_id = n.video_id or video_id
        if n.kind not in NEEDS_SUPPORTS or n.promoted_source_id is not None or not n.url:
            continue
        sr = scripts.add_source(db, Actor.system("research"), video_id, url=n.url, title=n.title or n.url, publisher=n.publisher,
                                retrieved_at=n.retrieved_at, note=f"[{n.source_class}] {n.note}".strip(), supports=n.supports)
        n.promoted_source_id = sr.id
        out.append(sr)
    audit.record(db, actor, "research.sources_promoted", "video", video_id, {"idea": idea_id, "sources": [s.id for s in out]})
    return out


def as_agent_text(note: ResearchNote) -> str:
    """The note as an agent should see it: quoted, labelled data."""
    return untrusted.quote_for_agents(note.excerpt, source=note.url, kind=note.kind)


def serialize(n: ResearchNote) -> dict[str, Any]:
    return {
        "id": n.id, "kind": n.kind, "url": n.url, "title": n.title, "publisher": n.publisher, "method": n.method,
        "retrieved_at": n.retrieved_at.isoformat(), "supports": n.supports, "note": n.note, "excerpt": n.excerpt[:600],
        "source_class": n.source_class, "source_class_basis": "web address heuristic; a person still judges the source",
        "figures": n.figures, "injection_flags": n.injection_flags, "untrusted": True, "has_snapshot": bool(n.snapshot_ref),
        "content_sha256": n.content_sha256, "promoted_source_id": n.promoted_source_id, "created_by": n.created_by,
        "idea_id": n.idea_id, "video_id": n.video_id,
    }
