"""Manager review of submitted work.

Two kinds of review, kept apart because they give different assurance:

* **Mechanical** (renders, music, captions, QA, originality screening, metadata, idea proposals): the manager re-verifies the evidence
  exactly as ``tasks.review`` always does: files are re-hashed, records re-read. No model is involved. A task whose evidence
  does not verify is returned, never waved through.
* **Creative** (a brief, lyrics or a script written by the AI brain): the manager's decision is made with the brain's help, using the
  software's own check results as stated facts. This is a *second reading by the same kind of model*, not independent assurance.
  It is bounded: after two returns the task is handed to a person, and an unusable review is never turned into an approval.

Only tasks of videos the owner started with the AI company ("conducted"), and the AI's own planning tasks, are reviewed here.
Manual steps and correction tasks always wait for a person.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import func, or_, select

from app.ai import gateway, prompts
from app.db import session_scope
from app.enums import TaskStatus
from app.models.ai import AiGeneration
from app.models.org import Channel
from app.models.production import CreativeBrief, ScriptVersion
from app.models.tasks import Task
from app.models.videos import Video
from app.production import briefs
from app.services import org, tasks

log = logging.getLogger("ai.review")

CREATIVE = {"ai.draft_brief": "brief", "ai.draft_script": "script"}
MECHANICAL_TYPES = {"ai.propose_ideas"}
MECHANICAL_PREFIXES = ("production.", "seo.")
MAX_AI_RETURNS = 2  # after this many returns a person decides
MAX_UNUSABLE_REVIEWS = 4  # model answers that could not be used, per task, before a person is asked


def is_conducted(video: Video | None) -> bool:
    return bool(video and (video.metadata_json or {}).get("conducted"))


def _in_scope(db, t: Task) -> bool:
    if t.assigned_agent_id is None:
        return False
    if t.task_type in CREATIVE or t.task_type in MECHANICAL_TYPES:
        return True
    return t.task_type.startswith(MECHANICAL_PREFIXES) and is_conducted(db.get(Video, t.video_id) if t.video_id else None)


def _manager_slug(db, t: Task) -> str:
    agent = org.get_agent(db, t.assigned_agent.slug)
    return org.chain_of_command(db, agent)[0].slug


def pending(db) -> list[Task]:
    return [t for t in db.scalars(select(Task).where(Task.status == TaskStatus.SUBMITTED.value).order_by(Task.id)) if _in_scope(db, t)]


# --- mechanical ------------------------------------------------------------------------------------------------------------


def review_mechanical(task_id: int) -> str:
    with session_scope() as db:
        t = db.get(Task, task_id)
        if t is None or t.status != TaskStatus.SUBMITTED.value:
            return "skipped"
        actor = org.actor_for_slug(db, _manager_slug(db, t))
        try:
            tasks.review(db, actor, task_id, approve=True, notes="Evidence re-verified by the manager (files re-hashed, records re-read).")
            return "approved"
        except tasks.TaskError as e:
            tasks.review(db, actor, task_id, approve=False, notes=f"The evidence did not verify on re-check: {e}")
            return "returned"


# --- creative --------------------------------------------------------------------------------------------------------------


def _checks(kind: str, ch_kind: str) -> list[str]:
    if kind == "brief":
        return ["Every required field is present and valid (checked when the brief was saved).",
                "The musical plan uses only sections, instruments, keys and moods the generator supports.",
                "The main concept is not a duplicate of any earlier video on the channel (word-overlap screen)."]
    out = ["The text matches the approved brief's musical plan and fits on screen as captions (tested against the planned timeline)."]
    if ch_kind == "birthday":
        out += ["Section headers and line counts match the plan exactly.", "No well-known birthday-song line and not the traditional refrain (screen)."]
    if ch_kind == "medicine":
        out += ["Every sentence ends with a source marker that names a recorded source or [framing] (checked)."]
    return out


def _failed_reviews(db, task_id: int) -> int:
    """Review calls that gave nothing usable. A call that failed because the brain was unavailable is not the draft's fault and is not counted."""
    return db.scalar(select(func.count()).select_from(AiGeneration).where(
        AiGeneration.task_id == task_id, AiGeneration.purpose.like("review.%"), AiGeneration.status != "OK",
        or_(AiGeneration.error_code.is_(None), AiGeneration.error_code.notin_(gateway.PARKING_CODES)))) or 0


def review_creative(task_id: int, *, beat: Callable[[], None] | None = None) -> str:
    with session_scope(write=False) as db:
        t = db.get(Task, task_id)
        if t is None or t.status != TaskStatus.SUBMITTED.value:
            return "skipped"
        kind = CREATIVE[t.task_type]
        boss = _manager_slug(db, t)
        video = db.get(Video, t.video_id)
        ch = db.get(Channel, video.channel_id)  # type: ignore[union-attr]
        brief_content: dict[str, Any] | None = None
        text: str | None = None
        if kind == "brief":
            b = db.get(CreativeBrief, (t.result or {}).get("brief_id"))
            if b is None or b.status != "DRAFT":
                return "skipped"
            brief_content = dict(b.content)
        else:
            sv = db.get(ScriptVersion, (t.result or {}).get("script_version_id"))
            ab = briefs.latest(db, t.video_id, approved_only=True)  # type: ignore[arg-type]
            if sv is None or ab is None:
                return "skipped"
            text, brief_content = sv.text, dict(ab.content)
        returns, video_id, ch_kind, channel_slug = t.revision_count, t.video_id, ch.kind, ch.slug  # type: ignore[union-attr]
        if _failed_reviews(db, task_id) >= MAX_UNUSABLE_REVIEWS:
            unusable = True
        else:
            unusable = False
        prompt = prompts.review_prompt(kind, ch, brief=brief_content, text=text, checks=_checks(kind, ch_kind), revision=returns + 1,  # type: ignore[arg-type]
                                       sung=prompts.sung_available(db))
    if unusable:
        with session_scope() as db:
            tasks.escalate(db, org.actor_for_slug(db, boss), task_id, "AI_REVIEW_FAILED: the manager's AI review gave no usable decision several times; a person needs to look at this draft.")
        return "escalated"
    try:
        ans = gateway.ask(role=boss, purpose=f"review.{kind}", prompt=prompt, schema=prompts.REVIEW_SCHEMA, task_id=task_id, video_id=video_id,
                          channel_slug=channel_slug, beat=beat)
    except gateway.BrainNotReady:
        return "waiting"
    except gateway.BrainOutputError:
        return "unusable"  # recorded as an INVALID generation; counted toward escalation on the next pass
    except Exception:  # noqa: BLE001 - a transient failure of the program: recorded by the gateway, tried again later
        log.exception("AI review of task %s failed", task_id)
        return "failed"
    decision, notes = ans.data["decision"], ans.data["notes"].strip()
    with session_scope() as db:
        t = db.get(Task, task_id)
        if t is None or t.status != TaskStatus.SUBMITTED.value:
            return "skipped"
        actor = org.actor_for_slug(db, boss)
        who = f"AI-assisted review (generation {ans.generation_ids[-1]})"
        if decision == "approve":
            try:
                tasks.review(db, actor, task_id, approve=True, notes=f"{who}: {notes}")
            except tasks.TaskError as e:
                tasks.review(db, actor, task_id, approve=False, notes=f"The evidence did not verify on re-check: {e}")
                return "returned"
            if kind == "brief":
                briefs.review(db, actor, (t.result or {})["brief_id"], approve=True, notes=notes)
            return "approved"
        if t.revision_count >= MAX_AI_RETURNS:
            tasks.escalate(db, actor, task_id, f"AI_REVIEW_FAILED: returned {t.revision_count} times already and still not acceptable; a person decides. Latest notes: {notes}")
            if kind == "brief":
                briefs.review(db, actor, (t.result or {})["brief_id"], approve=False, notes=notes)
            return "escalated"
        tasks.review(db, actor, task_id, approve=False, notes=f"{who}: {notes}")
        if kind == "brief":
            briefs.review(db, actor, (t.result or {})["brief_id"], approve=False, notes=notes)
        return "returned"


def sweep(*, beat: Callable[[], None] | None = None, max_model_reviews: int = 1) -> list[dict[str, Any]]:
    """Review everything in scope that is waiting. Mechanical reviews are quick and all run; model-assisted ones are slow, so only a few per pass."""
    with session_scope(write=False) as db:
        todo = [(t.id, t.task_type) for t in pending(db)]
    out, model_calls = [], 0
    for tid, ttype in todo:
        try:
            if ttype in CREATIVE:
                if model_calls >= max_model_reviews:
                    continue
                result = review_creative(tid, beat=beat)
                model_calls += result not in ("skipped", "waiting")
            else:
                result = review_mechanical(tid)
        except Exception:  # noqa: BLE001 - one task's trouble must not stop the rest
            log.exception("review of task %s failed", tid)
            result = "error"
        if result != "skipped":
            out.append({"task": tid, "type": ttype, "result": result})
    return out
