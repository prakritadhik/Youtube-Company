"""Replying to comments: draft, approve, send once, read back.

The rules this module enforces (each has a test):

* **Draft-only by default.** A reply is written as a ``ReplyDraft``. Nothing reaches YouTube until the owner approves that exact
  text, or the owner has chosen ``auto_low_risk`` *and* the company is in Controlled autonomy *and* the comment is plain praise
  on a non-medicine channel that the owner listed.
* **Templates only for praise.** A template ("thank you") is the only wording the software writes. A question, complaint, medical,
  safety, copyright or personal-data comment gets no generated reply; a person writes it. The software never gives personal
  medical advice and never answers on the medicine channel beyond a thank-you.
* **The approval is for one text.** The text's hash is stored with the approval; the sender refuses text that no longer matches.
* **One reply per comment.** Before posting, the thread's existing replies are listed; a reply by this channel with the same text
  is adopted instead of posted again. The task is idempotent and a crash after the call started needs the owner to reconcile.
* **Success means a read-back.** The reply counts as sent only when ``comments.list`` returns it under the right parent, written by
  this channel, with the text that was approved.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.community import triage as tri
from app.db import session_scope
from app.enums import ApprovalKind, OperatingMode
from app.models.community import Comment, ReplyDraft
from app.models.org import Channel
from app.models.videos import Video
from app.providers import base as providers
from app.security.permissions import Actor, P
from app.services import approvals, audit, control, tasks
from app.services import settings as settings_svc
from app.storage import write_json_artifact
from app.timeutil import iso, utcnow
from app.youtube import client as yt
from app.youtube import connection as conn_svc
from app.youtube import quota

MAX_REPLY = 1000
OPEN = ("DRAFT", "APPROVED", "SENDING")
URL = re.compile(r"https?://|www\.", re.IGNORECASE)
TEMPLATES = (
    "Thank you so much for watching and for your kind words. It really means a lot to us!",
    "We are so glad you enjoyed it. Thank you for being here!",
    "Thank you for the lovely comment. Your support means the world to us.",
)


class ReplyError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class Blocked(Exception):
    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code, self.reason = code, reason


class Failed(Exception):
    pass


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _norm(text: str) -> str:
    return " ".join(text.split())


def template_for(comment: Comment) -> str:
    return TEMPLATES[comment.id % len(TEMPLATES)]


def _channel_of(db: Session, comment: Comment) -> Channel:
    return db.get(Channel, comment.channel_id)  # type: ignore[return-value]


# --- drafting ---------------------------------------------------------------------------------------------------------------


def create_draft(db: Session, actor: Actor, comment_id: int, text: str | None = None) -> ReplyDraft:
    """Write a draft. With no ``text``, the praise template is used; anything else needs the owner's (or a manager's) own words."""
    if not (actor.is_owner or actor.can(P.VIDEO_MANAGE)):
        raise ReplyError("NOT_ALLOWED", "Only the owner or a manager may draft a reply.")
    c = db.get(Comment, comment_id)
    if c is None:
        raise ReplyError("UNKNOWN_COMMENT", "comment not found")
    if c.is_ours:
        raise ReplyError("OWN_COMMENT", "This comment was written by the channel itself.")
    if c.parent_youtube_id:
        raise ReplyError("NOT_TOP_LEVEL", "YouTube only lets this tool reply to top-level comments.")
    if c.status == "REPLIED":
        raise ReplyError("ALREADY_REPLIED", "This comment already has a reply from the channel.")
    open_draft = db.scalar(select(ReplyDraft).where(ReplyDraft.comment_id == c.id, ReplyDraft.status.in_(OPEN)))
    if open_draft is not None:
        raise ReplyError("DRAFT_EXISTS", f"This comment already has an open draft (#{open_draft.id}). Reject it first to write another.")
    if text is None:
        if c.category not in tri.TEMPLATE_CATEGORIES or c.risk != "low" or c.injection_flags:
            raise ReplyError("NO_TEMPLATE", f"No template reply is safe for a '{c.category}' comment"
                                            + (" that contains instruction-like text" if c.injection_flags else "")
                                            + ". Write the reply in your own words, or leave it.")
        body, method = template_for(c), "template-v1"
    else:
        if not actor.is_owner:
            raise ReplyError("OWNER_ONLY", "Only the owner can supply the wording of a reply.")
        body, method = text.strip(), "owner_written"
        if not body:
            raise ReplyError("EMPTY", "The reply is empty.")
        if len(body) > MAX_REPLY:
            raise ReplyError("TOO_LONG", f"A reply may be at most {MAX_REPLY} characters (this one is {len(body)}).")
    d = ReplyDraft(comment_id=c.id, text=body, text_sha256=_sha(body), method=method, status="DRAFT", risk=c.risk, created_by=actor.label)
    db.add(d)
    c.status = "DRAFTED"
    db.flush()
    audit.record(db, actor, "community.reply_drafted", "reply_draft", d.id, {"comment": c.id, "method": method})
    return d


def discard(db: Session, actor: Actor, draft_id: int) -> ReplyDraft:
    d = db.get(ReplyDraft, draft_id)
    if d is None:
        raise ReplyError("UNKNOWN_DRAFT", "draft not found")
    if not (actor.is_owner or actor.can(P.VIDEO_MANAGE)):
        raise ReplyError("NOT_ALLOWED", "Only the owner or a manager may reject a draft.")
    if d.status not in ("DRAFT", "APPROVED"):
        raise ReplyError("NOT_OPEN", f"A draft that is {d.status.lower()} cannot be rejected.")
    d.status = "REJECTED"
    c = db.get(Comment, d.comment_id)
    if c is not None and c.status == "DRAFTED":
        c.status = "TRIAGED"
    if d.approval_request_id:
        from app.models.approvals import ApprovalRequest

        req = db.get(ApprovalRequest, d.approval_request_id)
        if req is not None and req.status == "PENDING":
            approvals.cancel(db, actor, req.id, "draft rejected")
    audit.record(db, actor, "community.reply_rejected", "reply_draft", d.id, {})
    return d


# --- approval ---------------------------------------------------------------------------------------------------------------


def auto_allowed(db: Session, d: ReplyDraft) -> tuple[bool, str]:
    """May this draft go out without the owner approving it? Only when every condition holds; otherwise the reason is returned."""
    c = db.get(Comment, d.comment_id)
    ch = _channel_of(db, c)  # type: ignore[arg-type]
    autonomy = settings_svc.get(db, "autonomy")
    if autonomy.get("comment_reply_policy") != "auto_low_risk":
        return False, "the reply policy is draft-only"
    if settings_svc.operating_mode(db) is not OperatingMode.CONTROLLED_AUTONOMY:
        return False, "the operating mode is not Controlled autonomy"
    if ch.slug not in autonomy.get("autopublish_channels", []):
        return False, "this channel is not on the autonomy list"
    if ch.kind == "medicine":
        return False, "replies on the medicine channel always need the owner"
    if d.method != "template-v1" or c.category not in tri.TEMPLATE_CATEGORIES or c.risk != "low" or c.injection_flags:  # type: ignore[union-attr]
        return False, "only a template thank-you to a plain praise comment may go out automatically"
    return True, "all conditions for an automatic reply hold"


def submit(db: Session, actor: Actor, draft_id: int) -> dict[str, Any]:
    """Ask for approval (or, where the owner's policy allows, approve by policy). Returns what happened."""
    d = db.get(ReplyDraft, draft_id)
    if d is None:
        raise ReplyError("UNKNOWN_DRAFT", "draft not found")
    if d.status != "DRAFT":
        raise ReplyError("NOT_A_DRAFT", f"This reply is already {d.status.lower()}.")
    c = db.get(Comment, d.comment_id)
    ch = _channel_of(db, c)  # type: ignore[arg-type]
    ok, why = auto_allowed(db, d)
    if ok:
        d.status, d.approved_by = "APPROVED", "policy:auto_low_risk"
        t = _queue(db, actor, d)
        audit.record(db, actor, "community.reply_auto_approved", "reply_draft", d.id, {"policy": "auto_low_risk", "task": t.id})
        return {"draft_id": d.id, "approval": "policy", "task_id": t.id}
    video = db.get(Video, c.video_id)  # type: ignore[union-attr]
    req = approvals.request(
        db, actor, kind=ApprovalKind.COMMENT_REPLY, title=f"Reply to a comment on “{video.title or video.working_title}”" if video else "Reply to a comment",
        summary=f"Comment ({c.category}, risk {c.risk}): {c.text[:300]!r}\n\nProposed reply: {d.text}",  # type: ignore[union-attr]
        why_it_matters="A public reply speaks for the channel and cannot be un-sent by this software.",
        if_approved="This exact text is posted as a reply from the channel, then read back to confirm.",
        if_rejected="Nothing is posted; the draft is discarded.", channel_id=ch.id, subject_type="reply_draft", subject_id=d.id,
        action={"handler": "community.reply", "args": {"draft_id": d.id, "text_sha256": d.text_sha256}})
    d.approval_request_id = req.id
    audit.record(db, actor, "community.reply_submitted", "reply_draft", d.id, {"approval": req.id, "auto_blocked_because": why})
    return {"draft_id": d.id, "approval": "owner", "approval_request_id": req.id, "why_not_automatic": why}


def _queue(db: Session, actor: Actor, d: ReplyDraft):
    c = db.get(Comment, d.comment_id)
    ch = _channel_of(db, c)  # type: ignore[arg-type]
    t = tasks.create(db, actor, title=f"Post approved reply to a comment (draft #{d.id})", task_type="youtube.reply", assigned_agent_slug="community-reply-specialist",
                     video_id=c.video_id, channel_slug=ch.slug, input_refs={"draft_id": d.id}, idempotency_key=f"yt-reply:{d.id}")  # type: ignore[union-attr]
    d.task_id = t.id
    return t


@approvals.register_handler("community.reply")
def _on_approved(db: Session, actor: Actor, req, args: dict[str, Any]) -> dict[str, Any]:
    d = db.get(ReplyDraft, int(args["draft_id"]))
    if d is None or d.status != "DRAFT":
        raise approvals.ApprovalError("the draft is no longer waiting for approval")
    if d.text_sha256 != args.get("text_sha256") or _sha(d.text) != d.text_sha256:
        raise approvals.ApprovalError("the reply text changed after approval was requested; request approval again")
    d.status, d.approved_by = "APPROVED", actor.label
    t = _queue(db, actor, d)
    return {"draft_id": d.id, "task_id": t.id}


@approvals.register_handler("community.reply:rejected")
def _on_rejected(db: Session, actor: Actor, req, args: dict[str, Any]) -> dict[str, Any]:
    d = db.get(ReplyDraft, int(args["draft_id"]))
    if d is not None and d.status == "DRAFT":
        d.status = "REJECTED"
        c = db.get(Comment, d.comment_id)
        if c is not None and c.status == "DRAFTED":
            c.status = "TRIAGED"
    return {"draft_id": args.get("draft_id")}


# --- sending ----------------------------------------------------------------------------------------------------------------


def _find_ours(listing: dict[str, Any], ours: str, text: str) -> dict[str, Any] | None:
    for item in listing.get("items", []):
        s = item.get("snippet") or {}
        if (s.get("authorChannelId") or {}).get("value") == ours and _norm(s.get("textOriginal") or s.get("textDisplay") or "") == _norm(text):
            return item
    return None


def perform(draft_id: int, *, mark_started: Callable[[dict[str, Any]], None], clear_started: Callable[[], None], task_id: int | None = None,
            check_paused: Callable[[], None] = lambda: None) -> dict[str, Any]:
    """Post (or finish posting) one approved reply and prove it arrived. Raises ``Blocked``, ``Failed`` or a plain error (retry)."""
    with session_scope(write=False) as db:
        d = db.get(ReplyDraft, draft_id)
        if d is None:
            raise Failed("The reply draft does not exist.")
        if d.status in ("SENT", "FAILED", "REJECTED"):
            return _result(d)
        if d.status not in ("APPROVED", "SENDING") or not d.approved_by:
            raise Failed(f"This reply is {d.status.lower()}, not approved; nothing was posted.")
        if _sha(d.text) != d.text_sha256:
            raise Failed("The reply text changed after it was approved; nothing was posted.")
        c = db.get(Comment, d.comment_id)
        if c is None or c.status == "REPLIED":
            raise Failed("The comment is gone or already answered; nothing was posted.")
        if d.approved_by.startswith("policy:"):
            ok, why = auto_allowed(db, d)
            if not ok:
                raise Blocked("POLICY_CHANGED", f"The automatic-reply policy no longer allows this ({why}). Approve it yourself or reject it.")
        state, detail = providers.capability_state(db, "youtube", "youtube.comments")
        if state != "ready":
            raise Blocked(f"PROVIDER_{state.upper()}", f"YouTube cannot be used right now: {detail}.")
        try:
            control.assert_external_action_allowed(db, "reply")
            quota.require(db, "comments.insert", "comments.list")
        except control.ExternalActionBlocked as e:
            raise Blocked(e.code, str(e)) from e
        except quota.QuotaExceeded as e:
            raise Blocked(e.code, e.reason) from e
        conn = conn_svc.connection_for(db, c.channel_id)
        if conn is None or conn.status != "CONNECTED":
            raise Blocked("NOT_CONNECTED", "This channel is not connected to YouTube. Reconnect it in Settings, then release the task.")
        if not conn_svc.has_group(conn, "comments"):
            raise Blocked("PERMISSION_MISSING", "Replying needs the comments permission. Allow comments for this channel in Settings, YouTube, then release the task.")
        conn_id, ours, parent, text, video_id, channel_id = conn.id, conn.google_channel_id, c.youtube_comment_id, d.text, c.video_id, c.channel_id
        already = d.youtube_reply_id
    check_paused()
    try:
        api = yt.YouTubeClient(conn_svc.access_token(conn_id), transport=conn_svc.TRANSPORT)
    except conn_svc.YouTubeError as e:
        raise Blocked(e.code, str(e)) from e

    def charge(method: str, ok: bool = True, note: str = "") -> None:
        with session_scope() as db:
            quota.record(db, method, ok=ok, video_id=video_id, task_id=task_id, note=note)

    def fail(e: yt.YouTubeApiError, step: str) -> Exception:
        if e.is_quota:
            clear_started()
            return Blocked("YOUTUBE_QUOTA", "YouTube says the daily quota is used up. The reply keeps its place and can go after the quota resets (midnight Pacific time).")
        if e.is_auth:
            clear_started()
            return Blocked("YOUTUBE_AUTH", "YouTube no longer accepts the stored permission. Reconnect the channel in Settings, then release the task.")
        if e.retryable:
            return RuntimeError(f"{step}: {e.reason}: {e.message}")  # unknown whether it landed: the marker stays, a person reconciles
        clear_started()
        _save(draft_id, status="FAILED", error=f"{step}: {e.reason}: {e.message}")
        return Failed(f"{step} was refused by YouTube: {e.reason}: {e.message}")

    mark_started({"step": "reply", "draft": draft_id})
    _save(draft_id, status="SENDING")
    reply_id = already
    posted_ref = None
    if reply_id is None:
        try:  # never post twice: look for the same reply first
            listing = api.comment_replies(parent)
        except yt.YouTubeApiError as e:
            charge("comments.list", ok=False, note=f"pre-check: {e.reason}")
            raise fail(e, "checking the thread's existing replies") from e
        charge("comments.list", note="pre-check")
        found = _find_ours(listing, ours or "", text)
        if found is not None:
            reply_id = found["id"]
        else:
            try:
                created = api.comment_reply(parent, text)
            except yt.YouTubeApiError as e:
                charge("comments.insert", ok=False, note=e.reason)
                raise fail(e, "posting the reply") from e
            charge("comments.insert", note="reply posted")
            reply_id = created.get("id")
            posted_ref = write_json_artifact(f"youtube/replies/{draft_id}", "insert-response.json",
                                             {"provider": "youtube", "endpoint": "comments.insert", "status_code": 200, "simulated": False, "retrieved_at": iso(utcnow()),
                                              "response": created})
            if not reply_id:
                _save(draft_id, status="FAILED", error="YouTube accepted the reply but returned no id.", response_ref=posted_ref)
                raise Failed("YouTube accepted the reply but returned no id.")
        _save(draft_id, youtube_reply_id=reply_id, response_ref=posted_ref)
    try:
        back = api.comment_get(reply_id)  # type: ignore[arg-type]
    except yt.YouTubeApiError as e:
        charge("comments.list", ok=False, note=f"read-back: {e.reason}")
        raise fail(e, "reading the reply back") from e
    charge("comments.list", note="read-back")
    item = next(iter(back.get("items") or []), None)
    s = (item or {}).get("snippet") or {}
    checks = [
        {"check": "the reply exists when read back", "ok": item is not None},
        {"check": "under the right comment", "ok": s.get("parentId") == parent, "expected": parent, "actual": s.get("parentId")},
        {"check": "written by this channel", "ok": (s.get("authorChannelId") or {}).get("value") == ours, "expected": ours, "actual": (s.get("authorChannelId") or {}).get("value")},
        {"check": "with the approved text", "ok": _norm(s.get("textOriginal") or s.get("textDisplay") or "") == _norm(text)},
    ]
    vref = write_json_artifact(f"youtube/replies/{draft_id}", "verification.json",
                               {"provider": "youtube", "endpoint": "comments.list (read-back)", "status_code": 200, "simulated": False, "retrieved_at": iso(utcnow()),
                                "response": back, "checks": checks})
    if not all(x["ok"] for x in checks):
        _save(draft_id, status="FAILED", response_ref=vref, error="the read-back did not match what was approved: "
              + "; ".join(x["check"] for x in checks if not x["ok"]))
        raise Blocked("YOUTUBE_MISMATCH", f"The reply {reply_id} was read back but does not match what was approved ("
                                          + "; ".join(x["check"] for x in checks if not x["ok"]) + "). A person must look at it in YouTube Studio.")
    with session_scope() as db:
        d = db.get(ReplyDraft, draft_id)
        d.status, d.youtube_reply_id, d.response_ref, d.sent_at, d.error = "SENT", reply_id, vref, utcnow(), None  # type: ignore[union-attr]
        c = db.get(Comment, d.comment_id)  # type: ignore[union-attr]
        c.status = "REPLIED"  # type: ignore[union-attr]
        audit.record(db, Actor.system("youtube"), "community.reply_sent", "reply_draft", draft_id, {"comment": c.id, "youtube_reply_id": reply_id, "channel": channel_id})  # type: ignore[union-attr]
    with session_scope(write=False) as db:
        return _result(db.get(ReplyDraft, draft_id))  # type: ignore[arg-type]


def _save(draft_id: int, **fields: Any) -> None:
    with session_scope() as db:
        d = db.get(ReplyDraft, draft_id)
        for k, v in fields.items():
            setattr(d, k, v)


def _result(d: ReplyDraft) -> dict[str, Any]:
    return {"draft_id": d.id, "status": d.status, "youtube_reply_id": d.youtube_reply_id, "error": d.error, "ref": d.response_ref}


def serialize(d: ReplyDraft) -> dict[str, Any]:
    return {"id": d.id, "comment_id": d.comment_id, "text": d.text, "method": d.method, "status": d.status, "risk": d.risk, "approval_request_id": d.approval_request_id,
            "approved_by": d.approved_by, "task_id": d.task_id, "youtube_reply_id": d.youtube_reply_id, "error": d.error, "created_by": d.created_by,
            "created_at": iso(d.created_at), "sent_at": iso(d.sent_at) if d.sent_at else None}
