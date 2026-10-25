"""Sources, scripts and lyrics. Medicine scripts must cite a recorded source for every factual sentence."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.org import Channel
from app.models.production import ScriptVersion, SourceRecord
from app.models.videos import Video
from app.production.similarity import strip_markers
from app.security.permissions import Actor, P
from app.services import audit
from app.storage import write_bytes_artifact
from app.timeutil import utcnow


class ScriptError(ValueError):
    pass


KINDS = {"script", "lyrics", "narration", "storyboard"}
_MARKER = re.compile(r"\[(S\d+(?:\s*,\s*S?\d+)*|framing)\]\s*$", re.IGNORECASE)
# A sentence runs to its terminal punctuation and keeps the source marker that follows it.
_SENTENCE = re.compile(r"[^.!?]+[.!?]+(?:\s*\[[^\]]*\])?|[^.!?]+$")


def add_source(db: Session, actor: Actor, video_id: int, *, url: str, title: str, publisher: str, retrieved_at: datetime,
               note: str, supports: str) -> SourceRecord:
    actor.require(P.TASK_WORK)
    if db.get(Video, video_id) is None:
        raise ScriptError("video not found")
    if not re.match(r"^https?://[^\s/$.?#].[^\s]*$", url):
        raise ScriptError("a source needs an http(s) URL")
    if retrieved_at > utcnow():
        raise ScriptError("retrieval date cannot be in the future")
    if not supports.strip():
        raise ScriptError("say which claim this source supports")
    s = SourceRecord(video_id=video_id, url=url, title=title, publisher=publisher, retrieved_at=retrieved_at, note=note,
                     supports=supports, created_by=actor.label)
    db.add(s)
    db.flush()
    audit.record(db, actor, "source.added", "source", s.id, {"video": video_id, "url": url})
    return s


def parse_cited(text: str) -> list[dict[str, Any]]:
    """Split into sentences; each must end with ``[S<id>[,S<id>]]`` (a factual claim) or ``[framing]``."""
    out = []
    for line in text.splitlines():
        for found in _SENTENCE.finditer(line):
            sent = found.group(0).strip()
            if not sent:
                continue
            m = _MARKER.search(sent)
            ids: list[int] = []
            framing = False
            if m:
                tag = m.group(1)
                framing = tag.lower() == "framing"
                ids = [] if framing else [int(x.strip().lstrip("Ss")) for x in tag.split(",")]
            out.append({"sentence": strip_markers(sent).strip(), "source_ids": ids, "framing": framing, "marked": bool(m)})
    return out


def validate_cited(db: Session, video_id: int, text: str) -> list[dict[str, Any]]:
    parsed = parse_cited(text)
    if not parsed:
        raise ScriptError("the script is empty")
    valid_ids = set(db.scalars(select(SourceRecord.id).where(SourceRecord.video_id == video_id)))
    problems = []
    for i, p in enumerate(parsed, 1):
        if not p["marked"]:
            problems.append(f"sentence {i} has no source marker: {p['sentence'][:60]!r}")
        elif not p["framing"]:
            bad = [x for x in p["source_ids"] if x not in valid_ids]
            if bad:
                problems.append(f"sentence {i} cites unknown source(s) {bad}")
    if problems:
        raise ScriptError("medicine scripts must cite a recorded source for every factual sentence — " + "; ".join(problems))
    return [{"claim": p["sentence"], "source_ids": p["source_ids"]} for p in parsed if not p["framing"]]


def add_script(db: Session, actor: Actor, video_id: int, *, kind: str, text: str, author_kind: str, author_label: str,
               structure: dict[str, Any] | None = None) -> ScriptVersion:
    actor.require(P.TASK_WORK)
    if kind not in KINDS:
        raise ScriptError(f"unknown kind '{kind}'")
    if author_kind not in ("human", "agent", "provider"):
        raise ScriptError("author_kind must be human, agent or provider")
    if not text.strip():
        raise ScriptError("the text is empty")
    video = db.get(Video, video_id)
    if video is None:
        raise ScriptError("video not found")
    ch = db.get(Channel, video.channel_id)
    claims: list[dict[str, Any]] = []
    if ch.policy.get("source_citations_required") and kind in ("script", "narration"):  # type: ignore[union-attr]
        claims = validate_cited(db, video_id, text)
    version = (db.scalar(select(func.max(ScriptVersion.version)).where(
        ScriptVersion.video_id == video_id, ScriptVersion.kind == kind)) or 0) + 1
    rel = write_bytes_artifact(f"videos/{video_id}", f"{kind}-v{version}.txt", text.encode("utf-8"))
    sv = ScriptVersion(video_id=video_id, kind=kind, version=version, text=text, author_kind=author_kind,
                       author_label=author_label, claims=claims, structure=structure or {}, artifact_path=rel,
                       created_by=actor.label)
    db.add(sv)
    db.flush()
    audit.record(db, actor, "script.saved", "script", sv.id, {"video": video_id, "kind": kind, "version": version,
                                                              "author_kind": author_kind})
    return sv


def latest(db: Session, video_id: int, kind: str) -> ScriptVersion | None:
    return db.scalar(select(ScriptVersion).where(ScriptVersion.video_id == video_id, ScriptVersion.kind == kind)
                     .order_by(ScriptVersion.version.desc()))


def spoken_text(text: str) -> str:
    """The text without source markers, as it would be narrated or shown."""
    cleaned = re.sub(r"[ \t]{2,}", " ", strip_markers(text))
    return re.sub(r"[ \t]+\n", "\n", cleaned).strip()
