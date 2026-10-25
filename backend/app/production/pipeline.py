"""Production workflow: the task chain, the real handlers behind it, and the verification finalizers.

Each automated step is a queue task assigned to the specialist role that owns it, so it carries that role's
permissions, records the board version in force, produces evidence a manager can re-verify, and is reviewed
before it counts as done. Creative authoring (brief, lyrics/script) stays a *manual* step until a language-model
provider is connected: the pipeline does not invent creative content.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import session_scope
from app.enums import TaskStatus, VideoStage
from app.models.production import Asset, OriginalityReview, QAReport
from app.models.tasks import Task
from app.models.videos import Video
from app.production import assets as assets_svc
from app.production import briefs, imaging, originality, qa, scripts, synth
from app.production import captions as cap
from app.production import render as rd
from app.production.imaging import FONT_BOLD, FONT_REGULAR
from app.security.permissions import Actor
from app.services import audit, org, tasks, videos
from app.storage import relative_to_data, write_json_artifact
from app.worker.runner import BlockedError, HandlerResult, NonRetryableError, TaskContext, handler

DEFAULT_SPEC = {"width": 1920, "height": 1080, "fps": 30, "preset": "medium", "crossfade_s": 0.6, "scene_seed": None, "music_seed": None}
_STAGE_ORDER = [VideoStage.IDEA, VideoStage.RESEARCH, VideoStage.BRIEF, VideoStage.SCRIPT, VideoStage.PRODUCTION, VideoStage.QA,
                VideoStage.PACKAGING, VideoStage.READY]

# which role fixes which kind of QA failure
FIX_ROLE = {
    "decode": "video-editing-specialist", "duration": "video-editing-specialist", "resolution": "video-editing-specialist",
    "codec": "video-editing-specialist", "av_sync": "video-editing-specialist", "black_frames": "video-editing-specialist",
    "opening": "video-editing-specialist", "ending": "video-editing-specialist", "file": "video-editing-specialist",
    "video_stream": "video-editing-specialist",
    "audio_present": "voice-audio-specialist", "audio_level": "voice-audio-specialist", "loudness": "voice-audio-specialist",
    "clipping": "voice-audio-specialist", "silence": "voice-audio-specialist",
    "captions": "subtitle-caption-specialist", "thumbnail": "thumbnail-designer", "metadata": "creative-director",
    "records": "copyright-originality-reviewer",
}


def adir(video_id: int) -> Path:
    d = get_settings().artifacts_dir / "videos" / str(video_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def spec_of(ctx_or_refs: dict[str, Any]) -> dict[str, Any]:
    return {**DEFAULT_SPEC, **(ctx_or_refs.get("spec") or {})}


def _advance_to(db: Session, actor: Actor, video: Video, target: VideoStage) -> None:
    """Move forward one stage at a time (stages cannot be skipped); never moves backwards."""
    cur = VideoStage(video.stage)
    if cur not in _STAGE_ORDER or _STAGE_ORDER.index(cur) >= _STAGE_ORDER.index(target):
        return
    for st in _STAGE_ORDER[_STAGE_ORDER.index(cur) + 1 : _STAGE_ORDER.index(target) + 1]:
        videos.set_stage(db, actor, video.id, st)


# --- starting a production --------------------------------------------------------------------------------------------


def start_production(db: Session, manager: Actor, video_id: int, *, title: str, description: str, tags: list[str] | None = None,
                     spec: dict[str, Any] | None = None) -> dict[str, Task]:
    """Create the task chain. The creative steps (brief, lyrics/script) are manual until an LLM provider exists."""
    video = db.get(Video, video_id)
    if video is None:
        raise ValueError("video not found")
    video.title = title
    video.metadata_json = {**(video.metadata_json or {}), "description": description, "tags": tags or []}
    refs = {"video_id": video_id, "spec": {**DEFAULT_SPEC, **(spec or {})}}
    channel_slug = org_channel(db, video)

    def mk(key: str, label: str, role: str, task_type: str, deps: tuple[Task, ...] = ()) -> Task:
        return tasks.create(db, manager, title=f"{label} — {video.working_title}", task_type=task_type, assigned_agent_slug=role,
                            video_id=video_id, channel_slug=channel_slug, depends_on=[d.id for d in deps], input_refs=refs,
                            idempotency_key=f"prod:{video_id}:{key}")

    t: dict[str, Task] = {}
    t["brief"] = mk("brief", "Write the creative brief", "creative-director", "manual.brief")
    t["script"] = mk("script", "Write the lyrics/script", "script-lyrics-writer", "manual.script", (t["brief"],))
    t["music"] = mk("music", "Compose the soundtrack", "original-music-specialist", "production.generate_music", (t["brief"],))
    t["scenes"] = mk("scenes", "Create the scenes", "image-generation-specialist", "production.generate_scenes", (t["music"],))
    t["captions"] = mk("captions", "Build the captions", "subtitle-caption-specialist", "production.generate_captions", (t["script"], t["music"]))
    t["render"] = mk("render", "Render the video", "video-editing-specialist", "production.render_video", (t["scenes"], t["music"], t["captions"]))
    t["thumbnail"] = mk("thumbnail", "Design the thumbnail", "thumbnail-designer", "production.generate_thumbnail", (t["brief"],))
    t["qa"] = mk("qa", "Quality assurance", "final-qa-specialist", "production.qa_video", (t["render"], t["thumbnail"]))
    t["originality"] = mk("originality", "Originality and rights review", "copyright-originality-reviewer", "production.originality_review",
                          (t["render"], t["thumbnail"], t["script"]))
    audit.record(db, manager, "production.started", "video", video_id, {"tasks": {k: v.id for k, v in t.items()}})
    return t


def org_channel(db: Session, video: Video) -> str:
    from app.models.org import Channel

    return db.get(Channel, video.channel_id).slug  # type: ignore[union-attr]


def _role_actor(db: Session, ctx: TaskContext) -> Actor:
    return org.actor_for_slug(db, ctx.assigned_agent) if ctx.assigned_agent else Actor.system("pipeline")


def _file_evidence(path: Path, note: str) -> dict[str, Any]:
    return {"kind": "file", "ref": relative_to_data(path), "note": note}


# --- handlers ------------------------------------------------------------------------------------------------------------


def plan_bars(structure: list[str], bpm: float, planned_s: float) -> dict[str, int]:
    """Spread bars over the sections so the piece lands near the planned length."""
    base_w = synth.DEFAULT_BARS
    weights = {s: base_w.get(s.split()[0], 4) for s in structure}
    bar_s = 240.0 / bpm
    target = max(round(planned_s / bar_s), len(structure))
    total_w = sum(weights.values())
    return {s: max(1, round(w * target / total_w)) for s, w in weights.items()}


@handler("production.generate_music")
def generate_music(ctx: TaskContext) -> HandlerResult:
    ctx.check_paused()
    spec = spec_of(ctx.input_refs)
    with session_scope() as db:
        brief = _approved_brief(db, ctx.video_id)  # type: ignore[arg-type]
        bc = brief.content
        video_id = ctx.video_id
    mp = bc.get("musical_plan") or {}
    seed = int(spec["music_seed"] if spec["music_seed"] is not None else (video_id or 1) * 7919 + 17)
    structure = [s.lower() for s in mp["song_structure"]]
    comp_spec = {"tempo_bpm": mp["tempo_bpm"], "key": mp["key"], "mood": mp.get("mood", "bright"), "seed": seed, "structure": structure,
                 "instrumentation": mp["instrumentation"], "bars": plan_bars(structure, float(mp["tempo_bpm"]), float(bc["planned_length_seconds"]))}
    comp = synth.compose(comp_spec)
    wav = comp.write_wav(adir(video_id) / "music.wav")  # type: ignore[arg-type]
    warnings = []
    if mp.get("vocal_delivery") == "sung":
        warnings.append("The plan asks for a sung song, but no singing provider is available: this is an instrumental. "
                        "The originality review will flag it; it must not be presented as a sung song.")
    with session_scope() as db:
        actor = _role_actor(db, ctx)
        a = assets_svc.register(
            db, actor, video_id=video_id, kind="music", role="soundtrack", path=wav, provider="local-procedural", model="synth-v1",  # type: ignore[arg-type]
            seed=seed, license_type="owner-generated", commercial_use="yes",
            license_ref="Generated by this software from a numeric seed; contains no third-party recording, sample or melody.",
            params={"vocals": False, "timeline": comp.timeline, "meta": comp.meta, "duration_s": comp.duration_s,
                    "spec": comp_spec}, task_id=ctx.task_id)
        v = db.get(Video, video_id)
        _advance_to(db, Actor.system("pipeline"), v, VideoStage.PRODUCTION)  # type: ignore[arg-type]
        aid = a.id
    return HandlerResult(result={"asset_id": aid, "duration_s": comp.duration_s, "seed": seed, "vocals": False, "warnings": warnings},
                         evidence=[_file_evidence(wav, "generated soundtrack (WAV)")])


def _approved_brief(db: Session, video_id: int):
    """Production may only proceed on an approved brief; until then the task waits (BLOCKED) instead of failing."""
    try:
        return briefs.require_approved(db, video_id)
    except briefs.BriefError as e:
        raise BlockedError("BRIEF_NOT_APPROVED", f"{e}. A manager must approve the brief, then release this task.") from e


def _music(db: Session, video_id: int) -> Asset:
    m = db.scalar(select(Asset).where(Asset.video_id == video_id, Asset.kind == "music").order_by(Asset.id.desc()))
    if m is None:
        raise NonRetryableError("no music asset exists yet")
    return m


@handler("production.generate_scenes")
def generate_scenes(ctx: TaskContext) -> HandlerResult:
    ctx.check_paused()
    spec = spec_of(ctx.input_refs)
    size = (int(spec["width"]), int(spec["height"]))
    with session_scope() as db:
        _approved_brief(db, ctx.video_id)  # type: ignore[arg-type]
        music = _music(db, ctx.video_id)  # type: ignore[arg-type]
        timeline, dur = music.params["timeline"], music.params["duration_s"]
        video = db.get(Video, ctx.video_id)
        title = video.title or video.working_title  # type: ignore[union-attr]
    base = int(spec["scene_seed"] if spec["scene_seed"] is not None else (ctx.video_id or 1) * 104729 + 3)
    made = []
    for i, s in enumerate(timeline):
        ctx.check_paused()
        end = timeline[i + 1]["start_s"] if i + 1 < len(timeline) else dur
        text = title if i == 0 else ("Thanks for listening" if i == len(timeline) - 1 else None)
        path = imaging.make_scene(adir(ctx.video_id) / f"scene-{i}.png", size=size, seed=base + i * 31, title=text,  # type: ignore[arg-type]
                                  motif="confetti" if s["base"] in ("chorus", "intro") else "sun")
        made.append((i, path, round(end - s["start_s"], 3), base + i * 31, s["section"]))
    with session_scope() as db:
        actor = _role_actor(db, ctx)
        ids = []
        for i, path, d, seed, section in made:
            a = assets_svc.register(db, actor, video_id=ctx.video_id, kind="image", role=f"scene-{i}", path=path,  # type: ignore[arg-type]
                                    provider="local-procedural", model="scene-v1", seed=seed, license_type="owner-generated",
                                    commercial_use="yes", license_ref="Generated by this software from a numeric seed.",
                                    params={"dhash": imaging.dhash(path), "duration_s": d, "section": section}, task_id=ctx.task_id)
            ids.append(a.id)
    return HandlerResult(result={"scenes": len(made), "asset_ids": ids},
                         evidence=[_file_evidence(p, f"scene {i}") for i, p, *_ in made])


@handler("production.generate_captions")
def generate_captions(ctx: TaskContext) -> HandlerResult:
    ctx.check_paused()
    spec = spec_of(ctx.input_refs)
    size = (int(spec["width"]), int(spec["height"]))
    with session_scope() as db:
        music = _music(db, ctx.video_id)  # type: ignore[arg-type]
        timeline, dur = music.params["timeline"], music.params["duration_s"]
        sv = scripts.latest(db, ctx.video_id, "lyrics") or scripts.latest(db, ctx.video_id, "script")  # type: ignore[arg-type]
        if sv is None:
            raise NonRetryableError("no lyrics or script has been saved yet")
        text, kind, version, author = sv.text, sv.kind, sv.version, sv.author_label
    try:
        cues = (cap.align_lyrics(cap.parse_lyrics(text), timeline) if kind == "lyrics" else cap.align_text_estimated(text, 0.5, dur - 1.0))
    except cap.CaptionError as e:
        raise NonRetryableError(f"captions cannot be aligned: {e}") from e
    issues = cap.validate_cues(cues, dur)
    if issues:
        raise NonRetryableError("captions are not readable as written: " + "; ".join(i["message"] for i in issues[:4]))
    d = adir(ctx.video_id)  # type: ignore[arg-type]
    ass, srt = cap.write_ass(cues, d / "captions.ass", size), cap.write_srt(cues, d / "captions.srt")
    meta = write_json_artifact(f"videos/{ctx.video_id}", "captions.json", {"cues": cues, "source": f"{kind} v{version}"})
    with session_scope() as db:
        actor = _role_actor(db, ctx)
        lic = f"Derived from the {kind} v{version} (author: {author}); a caption file adds no third-party material."
        for path, role in ((ass, "captions-ass"), (srt, "captions-srt")):
            assets_svc.register(db, actor, video_id=ctx.video_id, kind="caption", role=role, path=path, provider="local-render",  # type: ignore[arg-type]
                                license_type="owner-generated", commercial_use="yes", license_ref=lic,
                                params={"cues": len(cues), "timing": cues[0]["timing"]}, task_id=ctx.task_id)
        for rel, role in ((FONT_BOLD, "font-bold"), (FONT_REGULAR, "font-regular")):
            assets_svc.register(db, actor, video_id=ctx.video_id, kind="font", role=role, path=rel, provider="bundled",  # type: ignore[arg-type]
                                license_type="ofl-1.1", commercial_use="yes", license_ref="resource:fonts/OFL-Inter.txt (SIL Open Font License 1.1)",
                                attribution="Inter by The Inter Project Authors", task_id=ctx.task_id)
    return HandlerResult(result={"cues": len(cues), "timing": cues[0]["timing"], "text_source": f"{kind} v{version}"},
                         evidence=[_file_evidence(ass, "ASS captions"), _file_evidence(srt, "SRT captions"),
                                   {"kind": "file", "ref": meta, "note": "cue list"}])


@handler("production.render_video")
def render_video(ctx: TaskContext) -> HandlerResult:
    ctx.check_paused()
    spec = spec_of(ctx.input_refs)
    with session_scope() as db:
        music = _music(db, ctx.video_id)  # type: ignore[arg-type]
        scenes = sorted((a for a in assets_svc.for_video(db, ctx.video_id) if a.kind == "image" and a.role.startswith("scene-")),  # type: ignore[arg-type]
                        key=lambda a: int(a.role.split("-")[1]))
        ass = next((a for a in assets_svc.for_video(db, ctx.video_id) if a.role == "captions-ass"), None)  # type: ignore[arg-type]
        if not scenes or ass is None:
            raise NonRetryableError("scenes and captions must exist before rendering")
        paths = [(assets_svc.resolve_asset_path(a.path), a.params["duration_s"]) for a in scenes]
        wav, ass_path, dur = assets_svc.resolve_asset_path(music.path), assets_svc.resolve_asset_path(ass.path), music.params["duration_s"]
    scene_list = [rd.Scene(p, d, f"scene-{i}") for i, (p, d) in enumerate(paths)]
    scene_list[-1].duration_s = round(dur - sum(s.duration_s for s in scene_list[:-1]), 3)
    rspec = rd.RenderSpec(scene_list, wav, ass_path, size=(int(spec["width"]), int(spec["height"])), fps=int(spec["fps"]),
                          crossfade_s=float(spec["crossfade_s"]), preset=str(spec["preset"]))
    out = adir(ctx.video_id) / "final.mp4"  # type: ignore[arg-type]
    try:
        res = rd.render(rspec, out)
    except rd.RenderError as e:
        raise NonRetryableError(f"{e} {e.stderr[-300:]}") from e
    with session_scope() as db:
        actor = _role_actor(db, ctx)
        a = assets_svc.register(db, actor, video_id=ctx.video_id, kind="video", role="final", path=out, provider="local-render",  # type: ignore[arg-type]
                                license_type="owner-generated", commercial_use="yes",
                                license_ref="Assembled by this software from the registered, licensed assets of this video.",
                                params={"command": res.command, "elapsed_s": round(res.elapsed_s, 2), "duration_s": res.total_s,
                                        "size": [rspec.size[0], rspec.size[1]]}, task_id=ctx.task_id)
        video = db.get(Video, ctx.video_id)
        video.final_video_path = a.path  # type: ignore[union-attr]
        _advance_to(db, Actor.system("pipeline"), video, VideoStage.QA)  # type: ignore[arg-type]
    return HandlerResult(result={"asset_id": a.id, "duration_s": res.total_s, "render_seconds": round(res.elapsed_s, 1), "bytes": out.stat().st_size},
                         evidence=[_file_evidence(out, "final video (H.264/AAC)")])


@handler("production.generate_thumbnail")
def generate_thumbnail(ctx: TaskContext) -> HandlerResult:
    ctx.check_paused()
    spec = spec_of(ctx.input_refs)
    with session_scope() as db:
        _approved_brief(db, ctx.video_id)  # type: ignore[arg-type]
        video = db.get(Video, ctx.video_id)
        title = video.title or video.working_title  # type: ignore[union-attr]
    seed = int(spec["scene_seed"] if spec["scene_seed"] is not None else (ctx.video_id or 1) * 104729 + 3) + 977
    path = imaging.make_thumbnail(adir(ctx.video_id) / "thumbnail.png", title=title, seed=seed)  # type: ignore[arg-type]
    with session_scope() as db:
        actor = _role_actor(db, ctx)
        a = assets_svc.register(db, actor, video_id=ctx.video_id, kind="thumbnail", role="thumbnail", path=path, provider="local-procedural",  # type: ignore[arg-type]
                                model="thumbnail-v1", seed=seed, license_type="owner-generated", commercial_use="yes",
                                license_ref="Generated by this software from a numeric seed and the video title.",
                                params={"dhash": imaging.dhash(path), "title": title}, task_id=ctx.task_id)
        db.get(Video, ctx.video_id).thumbnail_path = a.path  # type: ignore[union-attr]
    return HandlerResult(result={"asset_id": a.id}, evidence=[_file_evidence(path, "thumbnail 1280x720")])


@handler("production.qa_video")
def qa_video(ctx: TaskContext) -> HandlerResult:
    ctx.check_paused()
    spec = spec_of(ctx.input_refs)
    with session_scope() as db:
        video = db.get(Video, ctx.video_id)
        if not video.final_video_path or not video.thumbnail_path:  # type: ignore[union-attr]
            raise NonRetryableError("the video and thumbnail must exist before QA")
        music = _music(db, ctx.video_id)  # type: ignore[arg-type]
        srt = next((a for a in assets_svc.for_video(db, ctx.video_id) if a.role == "captions-srt"), None)  # type: ignore[arg-type]
        cues = cap.read_srt(assets_svc.resolve_asset_path(srt.path)) if srt else None
        vpath, tpath = assets_svc.resolve_asset_path(video.final_video_path), assets_svc.resolve_asset_path(video.thumbnail_path)  # type: ignore[union-attr]
        problems = assets_svc.license_problems(db, ctx.video_id)  # type: ignore[arg-type]
        meta = video.metadata_json or {}  # type: ignore[union-attr]
        cfg = qa.QAConfig(expected_size=(int(spec["width"]), int(spec["height"])), expected_fps=float(spec["fps"]),
                          expected_duration_s=float(music.params["duration_s"]), title=video.title, description=meta.get("description"),  # type: ignore[union-attr]
                          tags=list(meta.get("tags") or []), require_captions=True)
        vid, version = video.id, (len(list(db.scalars(select(QAReport.id).where(QAReport.video_id == ctx.video_id)))) + 1)  # type: ignore[union-attr]
    checks = qa.run_checks(vpath, cfg, thumbnail=tpath, cues=cues, asset_problems=problems)
    summary = qa.summarize(checks)
    report = {"video_id": vid, "version": version, "summary": summary, "checks": checks, "config": {"size": list(cfg.expected_size)}}
    rel = write_json_artifact(f"videos/{vid}", f"qa-v{version}.json", report)
    with session_scope() as db:
        db.add(QAReport(video_id=vid, version=version, passed=summary["passed"], checks=checks, artifact_path=rel,
                        created_by=_role_actor(db, ctx).label, task_id=ctx.task_id))
    evidence = [{"kind": "file", "ref": rel, "note": "QA report"}]
    if summary["passed"]:  # a failed QA is a legitimate result, but it must never be recorded as a passing test
        ok = write_json_artifact(f"videos/{vid}", f"qa-v{version}-result.json",
                                 {"passed": True, "failed": 0, "passed_count": summary["total"]})
        evidence.append({"kind": "test_result", "ref": ok, "note": "all QA checks passed"})
    return HandlerResult(result={"passed": summary["passed"], "failed_checks": summary["failed"], "warned": summary["warned"],
                                 "qa_version": version}, evidence=evidence)


@handler("production.originality_review")
def originality_review(ctx: TaskContext) -> HandlerResult:
    ctx.check_paused()
    with session_scope() as db:
        actor = _role_actor(db, ctx)
        rev = originality.run_review(db, actor, ctx.video_id, task_id=ctx.task_id)  # type: ignore[arg-type]
        status, path, rid = rev.status, rev.artifact_path, rev.id
    return HandlerResult(result={"status": status, "review_id": rid},
                         evidence=[{"kind": "file", "ref": path, "note": "originality and rights report"}])


# --- finalizers (run after the manager verified the task) ----------------------------------------------------------------------


def _completed(db: Session, task_id: int, expected_type: str) -> Task:
    t = db.get(Task, task_id)
    if t is None or t.task_type != expected_type:
        raise ValueError(f"task {task_id} is not a {expected_type} task")
    if t.status != TaskStatus.COMPLETED.value:
        raise ValueError(f"task {task_id} is {t.status}: the manager must verify it first")
    return t


def finalize_qa(db: Session, actor: Actor, video_id: int, qa_task_id: int) -> dict[str, Any]:
    """Record the QA verdict. A failure opens correction tasks instead of passing quietly."""
    _completed(db, qa_task_id, "production.qa_video")
    report = db.scalar(select(QAReport).where(QAReport.video_id == video_id, QAReport.task_id == qa_task_id))
    if report is None:
        raise ValueError("this QA task produced no report")
    videos.record_qa(db, actor, video_id, report.passed, qa_task_id=qa_task_id)
    video = db.get(Video, video_id)
    if report.passed:
        _advance_to(db, actor, video, VideoStage.PACKAGING)  # type: ignore[arg-type]
        return {"passed": True, "corrections": []}
    return {"passed": False, "corrections": [t.id for t in create_correction_tasks(db, actor, video_id, report)]}


def create_correction_tasks(db: Session, manager: Actor, video_id: int, report: QAReport) -> list[Task]:
    by_role: dict[str, list[dict[str, Any]]] = {}
    for c in report.checks:
        if c["status"] == "fail":
            by_role.setdefault(FIX_ROLE.get(c["id"], "video-editing-specialist"), []).append(c)
    out = []
    for role, fails in by_role.items():
        out.append(tasks.create(
            db, manager, title=f"Fix QA failures (v{report.version}): " + ", ".join(c["id"] for c in fails), assigned_agent_slug=role,
            video_id=video_id, task_type="manual.correction", priority=20,
            description="\n".join(f"- {c['name']}: {c['detail']} → {c['correction']}" for c in fails),
            input_refs={"video_id": video_id, "qa_report_id": report.id, "checks": [c["id"] for c in fails]},
            idempotency_key=f"qa-fix:{video_id}:{report.version}:{role}"))
    audit.record(db, manager, "qa.corrections_opened", "video", video_id, {"tasks": [t.id for t in out], "version": report.version})
    return out


def finalize_originality(db: Session, video_id: int, review_task_id: int) -> Video:
    """Copy the reviewer's (manager-verified) verdict onto the video. Runs as the reviewer role and is audited as such."""
    _completed(db, review_task_id, "production.originality_review")
    review = db.scalar(select(OriginalityReview).where(OriginalityReview.video_id == video_id, OriginalityReview.task_id == review_task_id))
    if review is None:
        raise ValueError("this task produced no originality review")
    reviewer = org.actor_for_slug(db, "copyright-originality-reviewer")
    return originality.apply_verdict(db, reviewer, video_id, review.id, review_task_id=review_task_id)
