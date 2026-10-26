"""Queue handlers that let roles write with the AI brain.

Each handler runs as the role that owns the step, asks the gateway, applies the same deterministic rules a person's work faces
(brief validation, lyric fit and originality screen, source citation), saves the result through the ordinary services, and returns
evidence the manager can re-verify, including the record of the model call itself.

Nothing here approves anything: a draft is a DRAFT until the manager's review (``app.ai.review``) accepts it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.ai import fit, gateway, prompts
from app.db import session_scope
from app.models.org import Channel
from app.models.production import CreativeBrief
from app.models.videos import Video
from app.production import briefs, scripts, synth
from app.production import captions as cap
from app.production import originality as orig
from app.production import similarity as sim
from app.research import ideas
from app.services import audit, org
from app.worker.runner import BlockedError, HandlerResult, NonRetryableError, TaskContext, handler

AUTHOR = "AI: Claude Code on this computer"


def _role(ctx: TaskContext) -> str:
    if not ctx.assigned_agent:
        raise NonRetryableError("this task has no assigned role")
    return ctx.assigned_agent


def _label(model: str | None) -> str:
    return (f"{AUTHOR} ({model})" if model else AUTHOR)[:120]


def _gen_evidence(ids: list[int]) -> list[dict[str, Any]]:
    return [{"kind": "db_record", "ref": f"ai_generations:{i}", "note": "the recorded call to the AI brain (prompt, answer and checks in its file)"} for i in ids]


# --- brief -----------------------------------------------------------------------------------------------------------------


def brief_problems(content: dict[str, Any], kind: str, *, video_id: int, channel_id: int) -> list[str]:
    problems = briefs.validate(content, kind)
    mp = content.get("musical_plan") or {}
    structure = [str(s).lower() for s in mp.get("song_structure") or []]
    problems += fit.structure_problems(structure, kind)
    try:
        synth.parse_key(str(mp.get("key", "")))
    except (synth.SynthError, IndexError):
        problems.append("musical_plan.key must be a note name with major or minor, for example 'G major'")
    with session_scope(write=False) as db:
        video = db.get(Video, video_id)
        for v in db.scalars(select(Video).where(Video.channel_id == channel_id, Video.id != video_id)):
            if (v.metadata_json or {}).get("demo"):
                continue
            b = db.scalar(select(CreativeBrief).where(CreativeBrief.video_id == v.id).order_by(CreativeBrief.version.desc()))
            if b is None:
                continue
            o = ideas.overlap(video.working_title if video else "", str(content.get("main_concept", "")), v.title or v.working_title, str(b.content.get("main_concept", "")))
            if o["verdict"] == "duplicate":
                problems.append(f"the main concept duplicates the channel's earlier video {(v.title or v.working_title)!r}; choose a clearly different concept")
                break
    return problems


@handler("ai.draft_brief")
def draft_brief(ctx: TaskContext) -> HandlerResult:
    ctx.check_paused()
    if ctx.video_id is None:
        raise NonRetryableError("a brief is written for a video")
    role = _role(ctx)
    with session_scope(write=False) as db:
        video = db.get(Video, ctx.video_id)
        ch = db.get(Channel, video.channel_id)  # type: ignore[union-attr]
        prompt, schema, pctx = prompts.brief_prompt(db, video, ch, task_id=ctx.task_id)  # type: ignore[arg-type]
        kind, channel_id = ch.kind, ch.id  # type: ignore[union-attr]
        if kind == "medicine" and not pctx["source_ids"]:
            raise BlockedError("NEEDS_SOURCES", "a medicine brief can only be written from recorded sources, and none are recorded for this video")
    ans = gateway.ask(role=role, purpose="brief.draft", prompt=prompt, schema=schema, task_id=ctx.task_id, video_id=ctx.video_id, channel_slug=ctx.channel_slug,
                      validators=[lambda d: brief_problems(d, kind, video_id=ctx.video_id, channel_id=channel_id)], beat=ctx.heartbeat)  # type: ignore[arg-type]
    content = {k: v for k, v in ans.data.items() if k in schema["properties"]}
    content["_provenance"] = {"written_by": AUTHOR, "model": ans.model, "generation_ids": ans.generation_ids, "lessons_in_force": pctx["lesson_ids"]}
    with session_scope() as db:
        actor = org.actor_for_slug(db, role)
        b = briefs.create(db, actor, ctx.video_id, content)  # type: ignore[arg-type]
        bid, version, art = b.id, b.version, b.content["_artifact"]
    return HandlerResult(result={"brief_id": bid, "version": version, "generation_ids": ans.generation_ids, "model": ans.model, "calls": ans.attempts},
                         evidence=[{"kind": "file", "ref": art, "note": "creative brief"}, {"kind": "db_record", "ref": f"creative_briefs:{bid}", "note": "creative brief (draft)"},
                                   *_gen_evidence(ans.generation_ids)])


# --- lyrics / script -------------------------------------------------------------------------------------------------------


def repeat_problems(text: str, *, kind: str, video_id: int, channel_id: int) -> list[str]:
    """Too close to the channel's own earlier writing, judged by the same thresholds the originality review will apply later."""
    out: list[str] = []
    with session_scope(write=False) as db:
        for v in db.scalars(select(Video).where(Video.channel_id == channel_id, Video.id != video_id)):
            if (v.metadata_json or {}).get("demo"):
                continue
            other = scripts.latest(db, v.id, kind)
            if other is None:
                continue
            c = sim.compare(text, other.text)
            if (c["containment_in_other"] >= orig.REVISE_CONTAINMENT or c["longest_common_run_words"] >= orig.REVISE_LONG_RUN
                    or len(c["near_duplicate_lines"]) >= orig.REVISE_DUP_LINES):
                out.append(f"too close to the channel's earlier video {(v.title or v.working_title)!r} ({c['containment_in_other']:.0%} of its phrases appear there; "
                           f"longest shared run {c['longest_common_run_words']} words); write something clearly different")
    return out


def text_problems(text: str, *, ch_kind: str, brief: dict[str, Any], video_id: int, channel_id: int) -> list[str]:
    mp = brief.get("musical_plan") or {}
    structure = [str(s).lower() for s in mp.get("song_structure") or []]
    bpm, planned = float(mp.get("tempo_bpm", 100)), float(brief.get("planned_length_seconds", 60))
    problems: list[str] = []
    if ch_kind == "birthday":
        plan = fit.lyric_plan(structure, bpm, planned)
        secs = cap.parse_lyrics(text)
        got, want = [f"[{s['name']}]".lower() for s in secs], [p["header"].lower() for p in plan]
        if got != want:
            problems.append("the section headers must be exactly these, in this order, each on its own line, with nothing before the first: " + ", ".join(p["header"] for p in plan))
        else:
            for sec, p in zip(secs, plan, strict=True):
                if len(sec["lines"]) != p["lines"]:
                    problems.append(f"{p['header']} needs exactly {p['lines']} lines (has {len(sec['lines'])})")
                for line in sec["lines"]:
                    if len(line) > p["max_chars"]:
                        problems.append(f"{p['header']} line {line!r} is {len(line)} characters; the limit is {p['max_chars']}")
            problems += fit.lyric_problems(text, structure, bpm, planned)
        problems += prompts.known_line_problems(text)
    else:
        problems += fit.script_problems(text, structure, bpm, planned)
        if ch_kind == "medicine":
            with session_scope(write=False) as db:
                try:
                    scripts.validate_cited(db, video_id, text)
                except scripts.ScriptError as e:
                    problems.append(str(e))
    problems += repeat_problems(text, kind="lyrics" if ch_kind == "birthday" else "script", video_id=video_id, channel_id=channel_id)
    return problems


@handler("ai.draft_script")
def draft_script(ctx: TaskContext) -> HandlerResult:
    ctx.check_paused()
    if ctx.video_id is None:
        raise NonRetryableError("lyrics and scripts are written for a video")
    role = _role(ctx)
    with session_scope(write=False) as db:
        video = db.get(Video, ctx.video_id)
        ch = db.get(Channel, video.channel_id)  # type: ignore[union-attr]
        b = briefs.latest(db, ctx.video_id, approved_only=True)
        if b is None:
            raise BlockedError("NEEDS_BRIEF", "the creative brief has not been approved yet")
        brief = dict(b.content)
        prompt, schema, pctx = prompts.script_prompt(db, video, ch, brief, task_id=ctx.task_id)  # type: ignore[arg-type]
        ch_kind, brief_id, channel_id = ch.kind, b.id, ch.id  # type: ignore[union-attr]
    kind = pctx["kind"]
    ans = gateway.ask(role=role, purpose="script.draft", prompt=prompt, schema=schema, task_id=ctx.task_id, video_id=ctx.video_id, channel_slug=ctx.channel_slug,
                      validators=[lambda d: text_problems(d["text"], ch_kind=ch_kind, brief=brief, video_id=ctx.video_id, channel_id=channel_id)], beat=ctx.heartbeat)  # type: ignore[arg-type]
    text = ans.data["text"].strip()
    with session_scope() as db:
        actor = org.actor_for_slug(db, role)
        sv = scripts.add_script(db, actor, ctx.video_id, kind=kind, text=text, author_kind="agent", author_label=_label(ans.model),  # type: ignore[arg-type]
                                structure={"brief_id": brief_id, "generation_ids": ans.generation_ids, "note": ans.data.get("note", ""), "lessons_in_force": pctx["lesson_ids"]})
        svid, version, art = sv.id, sv.version, sv.artifact_path
    return HandlerResult(result={"script_version_id": svid, "kind": kind, "version": version, "generation_ids": ans.generation_ids, "model": ans.model, "calls": ans.attempts},
                         evidence=[{"kind": "file", "ref": art, "note": f"{kind}"}, {"kind": "db_record", "ref": f"script_versions:{svid}", "note": f"{kind} (draft)"},
                                   *_gen_evidence(ans.generation_ids)])


# --- ideas -----------------------------------------------------------------------------------------------------------------

IDEA_REQUIRES = ["instrumental_music", "illustrated_scenes", "video_render", "llm_drafting"]


@handler("ai.propose_ideas")
def propose_ideas(ctx: TaskContext) -> HandlerResult:
    ctx.check_paused()
    role = _role(ctx)
    count = max(1, min(int(ctx.input_refs.get("count", 3)), 5))
    if not ctx.channel_slug:
        raise NonRetryableError("ideas are proposed for a channel")
    with session_scope(write=False) as db:
        ch = org.get_channel(db, ctx.channel_slug)
        prompt, schema, pctx = prompts.ideas_prompt(db, ch, count)
    ans = gateway.ask(role=role, purpose="ideas.propose", prompt=prompt, schema=schema, task_id=ctx.task_id, channel_slug=ctx.channel_slug, beat=ctx.heartbeat)
    created, skipped = [], []
    with session_scope() as db:
        actor = org.actor_for_slug(db, role)
        for raw in ans.data["ideas"][:count]:
            try:
                idea = ideas.propose(db, actor, ctx.channel_slug, title=raw["title"], concept=raw["concept"], audience=raw["audience"], rationale=raw["rationale"],
                                     audience_relevance=raw.get("audience_relevance"), risks=raw.get("risks") or [], requires=IDEA_REQUIRES)
                ideas.assess(db, actor, idea.id)
                if (idea.assessment or {}).get("distinctness", {}).get("verdict") == "duplicate":
                    idea.status, idea.decision_note, idea.decided_by = "REJECTED", "Screened out automatically: it duplicates existing work in the channel.", "automatic duplicate screen"
                    audit.record(db, actor, "idea.rejected", "idea", idea.id, {"note": idea.decision_note})
                    skipped.append({"title": idea.title, "id": idea.id})
                else:
                    created.append(idea.id)
            except ideas.IdeaError as e:
                skipped.append({"title": str(raw.get("title"))[:80], "reason": str(e)})
    if not created and not skipped:
        raise NonRetryableError("the AI proposed no ideas")
    return HandlerResult(result={"ideas": created, "screened_out": skipped, "generation_ids": ans.generation_ids, "model": ans.model},
                         evidence=[*[{"kind": "db_record", "ref": f"content_ideas:{i}", "note": "proposed idea"} for i in created], *_gen_evidence(ans.generation_ids)])
