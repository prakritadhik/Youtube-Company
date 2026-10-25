"""Task handlers for the scheduled collections and for posting an approved reply."""

from __future__ import annotations

from app.analytics import collect
from app.community import collect as comments
from app.community import replies
from app.storage import write_json_artifact
from app.worker.runner import BlockedError, HandlerResult, NonRetryableError, TaskContext, handler


@handler("analytics.collect")
def collect_analytics(ctx: TaskContext) -> HandlerResult:
    """Fetch performance data for every uploaded video on every connected channel. Collecting nothing is a normal, reported outcome."""
    ctx.check_paused()
    out = collect.collect_all()
    ref = write_json_artifact("analytics", f"collect-{ctx.task_id}.json", out)
    videos = out["videos"]
    summary = {"connected_channels": out["connected_channels"], "videos": len(videos), "videos_with_errors": sum(1 for v in videos if "error" in v),
               "rows_stored": sum(v.get("stored_rows", 0) for v in videos), "gaps": sum(len(v.get("gaps", [])) for v in videos), "note": out.get("note")}
    return HandlerResult(result=summary, evidence=[{"kind": "file", "ref": ref, "note": "what was collected and what was unavailable"}])


@handler("community.collect_comments")
def collect_comments(ctx: TaskContext) -> HandlerResult:
    ctx.check_paused()
    out = comments.collect_all()
    ref = write_json_artifact("analytics", f"comments-{ctx.task_id}.json", out)
    videos = out["videos"]
    by: dict[str, int] = {}
    for v in videos:
        for k, n in v.get("by_category", {}).items():
            by[k] = by.get(k, 0) + n
    summary = {"connected_channels": out["connected_channels"], "videos": len(videos), "new_comments": sum(v.get("new", 0) for v in videos), "by_category": by,
               "unavailable": [{"video": v["video_id"], "reason": v.get("gap") or v.get("error")} for v in videos if v.get("gap") or v.get("error")], "note": out.get("note")}
    return HandlerResult(result=summary, evidence=[{"kind": "file", "ref": ref, "note": "what was fetched and triaged"}])


@handler("youtube.reply")
def post_reply(ctx: TaskContext) -> HandlerResult:
    ctx.check_paused()
    did = int(ctx.input_refs["draft_id"])
    try:
        out = replies.perform(did, mark_started=ctx.mark_external_action_started, clear_started=ctx.clear_external_action_started, task_id=ctx.task_id,
                              check_paused=ctx.check_paused)
    except replies.Blocked as e:
        raise BlockedError(e.code, e.reason) from e
    except replies.Failed as e:
        raise NonRetryableError(str(e)) from e
    if out["status"] != "SENT":
        raise NonRetryableError(out.get("error") or "the reply was not sent")
    evidence = [{"kind": "db_record", "ref": f"reply_drafts:{did}", "note": "the approved draft and its outcome"}]
    if out.get("ref"):
        evidence.insert(0, {"kind": "api_response", "ref": out["ref"], "note": "YouTube's read-back of the posted reply"})
    return HandlerResult(result={"draft_id": did, "status": out["status"], "youtube_reply_id": out["youtube_reply_id"]}, evidence=evidence)
