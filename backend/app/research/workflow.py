"""Queue handlers for the research and packaging steps.

* ``research.fetch_source``: fetch one public page with the safe fetcher and record it as untrusted research.
* ``seo.prepare_metadata``: draft title, description, tags, hashtags and chapters from the approved brief and script,
  check them, and store the result. The set is *not* approved by this step; a research or publishing manager decides.

Each step runs as the specialist role that owns it and returns evidence the manager can re-verify.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import session_scope
from app.models.org import Channel
from app.models.tasks import Task
from app.models.videos import Video
from app.research import notes, seo
from app.research.fetch import FetchError, SafeFetcher
from app.security.permissions import Actor, P
from app.services import audit, org, tasks
from app.storage import write_json_artifact
from app.worker.runner import HandlerResult, NonRetryableError, TaskContext, handler

fetcher_factory: Callable[[], SafeFetcher] = SafeFetcher  # replaced in tests so no network is touched


def start_packaging(db: Session, manager: Actor, video_id: int) -> Task:
    """Queue the metadata step for a video. It waits for the script and the soundtrack so chapters can use measured timing.

    Created by the research manager (or owner): the SEO specialist belongs to that department, and department
    boundaries apply to task assignment like everything else.
    """
    manager.require(P.TASK_CREATE)
    video = db.get(Video, video_id)
    if video is None:
        raise ValueError("video not found")
    deps = [t.id for t in db.scalars(select(Task).where(Task.video_id == video_id, Task.task_type.in_(("manual.script", "production.generate_music"))))]
    ch = db.get(Channel, video.channel_id)
    t = tasks.create(db, manager, title=f"Prepare the metadata — {video.working_title}", task_type="seo.prepare_metadata",
                     assigned_agent_slug="keyword-seo-specialist", video_id=video_id, channel_slug=ch.slug if ch else None, depends_on=deps,
                     input_refs={"video_id": video_id}, idempotency_key=f"pkg:{video_id}:metadata")
    audit.record(db, manager, "packaging.started", "video", video_id, {"task": t.id})
    return t


def _actor(db, ctx: TaskContext):
    if not ctx.assigned_agent:
        raise NonRetryableError("this task has no assigned role")
    return org.actor_for_slug(db, ctx.assigned_agent)


@handler("research.fetch_source")
def fetch_source(ctx: TaskContext) -> HandlerResult:
    ctx.check_paused()
    refs: dict[str, Any] = ctx.input_refs
    url = str(refs.get("url") or "")
    with session_scope(write=False) as db:
        _actor(db, ctx).require_tool("web.research")
    try:
        page = fetcher_factory().fetch(url)
    except FetchError as e:
        if e.retryable:
            raise RuntimeError(f"{e.code}: {e.message}") from e  # transient: the queue retries with backoff
        raise NonRetryableError(f"{e.code}: {e.message}") from e
    try:
        with session_scope() as db:
            n = notes.record_fetched(db, _actor(db, ctx), page, kind=str(refs.get("kind", "source")), supports=str(refs.get("supports", "")),
                                     note=str(refs.get("note", "")), channel_slug=ctx.channel_slug, idea_id=refs.get("idea_id"),
                                     video_id=ctx.video_id or refs.get("video_id"))
            note_id, snapshot, flags = n.id, n.snapshot_ref, list(n.injection_flags)
    except notes.ResearchError as e:  # a task that cannot be recorded as asked will never succeed on retry
        raise NonRetryableError(str(e)) from e
    return HandlerResult(
        result={"note_id": note_id, "final_url": page.final_url, "bytes": page.raw_bytes, "truncated": page.truncated, "robots": page.robots,
                "injection_flags": flags},
        evidence=[{"kind": "file", "ref": snapshot, "note": "page snapshot (untrusted external text)"},
                  {"kind": "db_record", "ref": f"research_notes:{note_id}", "note": "research note"}])


@handler("seo.prepare_metadata")
def prepare_metadata(ctx: TaskContext) -> HandlerResult:
    ctx.check_paused()
    if ctx.video_id is None:
        raise NonRetryableError("metadata is prepared for a video")
    with session_scope() as db:
        ms = seo.generate(db, _actor(db, ctx), ctx.video_id, task_id=ctx.task_id)
        data = seo.serialize(ms)
        set_id, version = ms.id, ms.version
    ref = write_json_artifact(f"videos/{ctx.video_id}", f"metadata-v{version}.json", data)
    return HandlerResult(
        result={"metadata_set_id": set_id, "version": version, "status": data["status"], "finding_counts": data["finding_counts"],
                "chapters": len(data["chapters"]), "generated_by": data["generated_by"],
                "next": "A research or publishing manager must check and approve it; nothing is applied to the video until then."},
        evidence=[{"kind": "file", "ref": ref, "note": "metadata set with check results"},
                  {"kind": "db_record", "ref": f"metadata_sets:{set_id}", "note": "metadata set"}])
