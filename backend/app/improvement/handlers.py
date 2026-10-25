"""Task handlers for the improvement department."""

from __future__ import annotations

from app.db import session_scope
from app.improvement import lessons
from app.services import org
from app.storage import write_json_artifact
from app.worker.runner import HandlerResult, NonRetryableError, TaskContext, handler


@handler("improvement.mine_lessons")
def mine_lessons(ctx: TaskContext) -> HandlerResult:
    """Examine completed workflows that have not been examined yet and propose lessons from what was recorded.

    Runs as the Continuous Improvement Scientist. Proposals are OBSERVED entries; nothing becomes an instruction here.
    """
    ctx.check_paused()
    if not ctx.assigned_agent:
        raise NonRetryableError("this task has no assigned role")
    with session_scope() as db:
        actor = org.actor_for_slug(db, ctx.assigned_agent)
        vid = ctx.input_refs.get("video_id")
        out = lessons.mine_all(db, actor) if vid is None else {"videos": [lessons.mine_video(db, actor, int(vid))]}
        if vid is not None:
            out = {"videos_examined": 1, "lessons": out["videos"][0]["lessons"], "videos": out["videos"]}
    ref = write_json_artifact("improvement", f"mine-lessons-{ctx.task_id}.json", out)
    entries = sorted({x["lesson"] for x in out["lessons"]})
    evidence = [{"kind": "file", "ref": ref, "note": "what was examined and what was proposed"}]
    evidence += [{"kind": "db_record", "ref": f"board_entries:{_entry_id(e)}", "note": f"proposed lesson {e}"} for e in entries if _entry_id(e)]
    return HandlerResult(result={"videos_examined": out["videos_examined"], "lessons": entries,
                                 "proposed": sum(1 for x in out["lessons"] if x["action"] == "proposed"),
                                 "evidence_added": sum(1 for x in out["lessons"] if x["action"] == "evidence_added")}, evidence=evidence)


def _entry_id(public_id: str) -> int | None:
    from app.models.board import BoardEntry

    with session_scope(write=False) as db:
        return db.query(BoardEntry.id).filter(BoardEntry.public_id == public_id).scalar()
