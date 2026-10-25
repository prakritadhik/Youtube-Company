"""Queue handler for ``youtube.upload``. All the logic is in ``upload.perform``; this maps its outcomes to task states."""

from __future__ import annotations

from app.worker.runner import BlockedError, HandlerResult, NonRetryableError, TaskContext, handler
from app.youtube import upload


@handler("youtube.upload")
def upload_to_youtube(ctx: TaskContext) -> HandlerResult:
    ctx.check_paused()
    uid = int(ctx.input_refs["upload_id"])
    try:
        out = upload.perform(uid, mark_started=ctx.mark_external_action_started, clear_started=ctx.clear_external_action_started,
                             heartbeat=ctx.heartbeat, task_id=ctx.task_id, check_paused=ctx.check_paused)
    except upload.Blocked as e:
        raise BlockedError(e.code, e.reason) from e
    except upload.Failed as e:
        raise NonRetryableError(str(e)) from e
    status = out["status"]
    ref = out["verification"].get("ref")
    if status == "FAILED":
        raise NonRetryableError(out.get("error") or "the upload failed")
    if status == "MISMATCH":
        failed = [f"{c['check']} (asked for {c['expected']!r}, YouTube says {c['actual']!r})" for c in out["verification"].get("checks", []) if not c["ok"]]
        raise BlockedError("YOUTUBE_MISMATCH", f"YouTube video {out['youtube_video_id']} does not match what was asked for: " + "; ".join(failed)
                           + ". A person must look at it in YouTube Studio.")
    if status == "PRIVACY_LOCKED":
        raise BlockedError("PRIVACY_LOCKED", f"The video was uploaded as {out['youtube_video_id']} but YouTube made it private although "
                                             f"'{out['privacy_requested']}' was requested. It is NOT published. YouTube is reported to do this for API projects that have not "
                                             "passed its compliance audit (not confirmed by this build); see docs/YOUTUBE.md.")
    evidence = [{"kind": "db_record", "ref": f"youtube_uploads:{uid}", "note": "upload record"}]
    if ref:
        evidence.insert(0, {"kind": "api_response", "ref": ref, "note": "YouTube's read-back of the uploaded video (videos.list)"})
    if out.get("insert_ref"):
        evidence.insert(0, {"kind": "api_response", "ref": out["insert_ref"], "note": "YouTube's final answer to the upload (videos.insert)"})
    return HandlerResult(result={k: out[k] for k in ("upload_id", "status", "youtube_video_id", "privacy_requested", "publish_at", "thumbnail")}, evidence=evidence)
