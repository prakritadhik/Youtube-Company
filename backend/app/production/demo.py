"""``python -m app.cli demo-production``: run the real pipeline end to end on a clearly-labelled demonstration video.

Why it exists: it lets the owner see (and hear) the platform produce a validated video on *their* computer, with no
credentials, and proves FFmpeg and fonts work there.

Why it cannot be confused with real content:
* the title starts with "DEMO", ``metadata_json.demo`` is true, and the lyrics are labelled as sample text written
  by the build assistant for demonstration;
* the publication gate refuses demo videos permanently (``DEMO_CONTENT``, never waivable);
* demo videos are ignored by the repetition checks, so they cannot make real uploads look like duplicates.

Each step is performed as the role that owns it (so permissions, the audit log and the board acknowledgement are real);
the manager's verification of every task is the real ``tasks.review`` that re-hashes the evidence.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.db import session_scope
from app.enums import TaskStatus
from app.models.tasks import Task
from app.models.videos import Video
from app.production import briefs, originality, pipeline, scripts
from app.production import qa as qa_mod
from app.research import seo, thumbs, workflow
from app.services import gate, org, tasks, videos
from app.storage import resolve_data_path
from app.worker.runner import HANDLERS, Runner

DEMO_LYRICS = """[Verse]
Morning light is on the hill today
Paper hats and lanterns on the way
[Chorus]
Spin around, the garden starts to glow
Candles dancing in a gentle row
[Verse 2]
Bells are ringing over fields of green
Brightest morning ever seen
[Chorus 2]
Spin around, the garden starts to glow
Candles dancing in a gentle row"""

LYRICS_NOTE = "sample lyrics written by the build assistant for this demonstration only"


def demo_brief() -> dict[str, Any]:
    return {
        "video_objective": "Demonstrate the production pipeline end to end.", "target_audience": "The channel owner",
        "viewer_benefit": "See exactly what the platform produces and how it is checked.", "main_concept": "A short animated lyric demo, a pipeline demonstration and never a real release",
        "tone_and_style": "warm and clean", "planned_length_seconds": 40, "visual_direction": "procedural illustrated landscapes",
        "audio_direction": "algorithmic bells, pad, bass and soft drums", "hook_and_opening": "Bells over a sunrise",
        "structure": "intro, verse, chorus, verse 2, chorus 2, outro", "thumbnail_concept": "Large title over soft colour",
        "distinctive_elements": ["lyrics aligned to the generated music bar by bar"],
        "originality_requirements": ["no reused lines", "no third-party audio"], "success_criteria": ["all QA checks pass"],
        "musical_plan": {"tempo_bpm": 104, "key": "G major", "mood": "bright", "instrumentation": ["bells", "pad", "bass", "drums"],
                         "vocal_delivery": "instrumental", "song_structure": ["intro", "verse", "chorus", "verse 2", "chorus 2", "outro"]},
        "style_tags": ["demo", "garden"],
    }


def is_demo(video: Video) -> bool:
    return bool((video.metadata_json or {}).get("demo"))


def _review_submitted() -> int:
    """Each submitted task is verified by the manager above the role that did it (their own department's manager)."""
    n = 0
    with session_scope() as db:
        for t in db.scalars(select(Task).where(Task.status == TaskStatus.SUBMITTED.value)).all():
            boss = org.chain_of_command(db, org.get_agent(db, t.assigned_agent.slug))[0]
            tasks.review(db, org.actor_for_slug(db, boss.slug), t.id, approve=True, notes="evidence re-verified by the manager (demo run)")
            n += 1
    return n


def run(width: int = 1280, height: int = 720, preset: str = "veryfast", progress=print) -> dict[str, Any]:
    spec = {"width": width, "height": height, "preset": preset, "crossfade_s": 0.6}
    with session_scope() as db:
        pm = org.actor_for_slug(db, "production-manager")
        v = videos.create(db, pm, "happy-birthday-songs", "DEMO — Garden Morning (pipeline demonstration)")
        t = pipeline.start_production(db, pm, v.id, title="DEMO — Garden Morning (pipeline demonstration)",
                                      description="Pipeline demonstration: an algorithmic instrumental with sample lyrics. Not a real release and never publishable.",
                                      tags=[], spec=spec)
        v.metadata_json = {**v.metadata_json, "demo": True}
        t["metadata"] = workflow.start_packaging(db, org.actor_for_slug(db, "research-manager"), v.id)
        vid, ids = v.id, {k: x.id for k, x in t.items()}
    progress(f"Created demo video #{vid} and its {len(ids)} tasks.")

    with session_scope() as db:
        d = org.actor_for_slug(db, "creative-director")
        tasks.begin(db, d, ids["brief"])
        b = briefs.create(db, d, vid, demo_brief())
        tasks.submit(db, d, ids["brief"], result={"brief_id": b.id}, evidence_specs=[{"kind": "file", "ref": b.content["_artifact"], "note": "brief"}])
        briefs.review(db, org.actor_for_slug(db, "production-manager"), b.id, approve=True, notes="approved for the demonstration")
    _review_submitted()
    with session_scope() as db:
        w = org.actor_for_slug(db, "script-lyrics-writer")
        tasks.begin(db, w, ids["script"])
        sv = scripts.add_script(db, w, vid, kind="lyrics", text=DEMO_LYRICS, author_kind="agent", author_label=f"Claude (build assistant): {LYRICS_NOTE}")
        tasks.submit(db, w, ids["script"], result={"version": sv.version}, evidence_specs=[{"kind": "file", "ref": sv.artifact_path, "note": "lyrics"}])
    _review_submitted()
    with session_scope() as db:  # a thumbnail concept is checked and approved on paper before the image is made
        concept = thumbs.create(db, org.actor_for_slug(db, "thumbnail-designer"), vid, overlay_text="DEMO Garden Morning",
                                composition="Large title over soft colour, nothing else", palette=["#FFFFFF", "#1B3A57"],
                                relation_to_content="The title is the video's title; the picture is the generated garden backdrop")
        thumbs.approve(db, org.actor_for_slug(db, "research-manager"), concept.id, note="clear and accurate for the demonstration")
    progress("Brief approved and lyrics saved. Running the production queue (music → scenes → captions → render → thumbnail → QA → rights)...")

    runner = Runner("demo", HANDLERS)
    for _ in range(40):
        ran = 0
        while (tid := runner.run_once()) is not None:
            ran += 1
            with session_scope(write=False) as db:
                progress(f"  ran: {db.get(Task, tid).title}")
        if not _review_submitted() and not ran:
            break
    with session_scope(write=False) as db:
        incomplete = {k: db.get(Task, i).status for k, i in ids.items() if db.get(Task, i).status != TaskStatus.COMPLETED.value}
    if incomplete:
        raise RuntimeError(f"the pipeline did not finish: {incomplete}")

    with session_scope() as db:
        pm, ceo = org.actor_for_slug(db, "production-manager"), org.actor_for_slug(db, "ceo")
        qa_res = pipeline.finalize_qa(db, pm, vid, ids["qa"])
        out: dict[str, Any] = {"video_id": vid, "qa": qa_res}
        if qa_res["passed"]:
            pipeline.finalize_originality(db, vid, ids["originality"])
            video = db.get(Video, vid)
            out["originality_status"] = video.originality_status
            if video.originality_status == "PASS":
                originality.ceo_inspect(db, ceo, vid)
        ms = seo.latest(db, vid)
        if ms is not None and ms.status == "CHECKED":
            seo.approve(db, org.actor_for_slug(db, "research-manager"), ms.id, note="accurate to the demonstration video")
        out["metadata"] = {"version": ms.version, "status": ms.status, "title": ms.chosen_title, "chapters": len(ms.chapters)} if ms else None
        video = db.get(Video, vid)
        out["stage"] = video.stage
        # Everything has passed; the one thing that must still stop it is that it is a demonstration.
        out["publication_gate_blockers"] = gate.evaluate(db, video, purpose="clear").codes()
        path = resolve_data_path(video.final_video_path)
        info = qa_mod.probe(path)
        out["file"] = str(path)
        out["duration_s"] = round(float(info["format"]["duration"]), 2)
        out["bytes"] = path.stat().st_size
    return out
