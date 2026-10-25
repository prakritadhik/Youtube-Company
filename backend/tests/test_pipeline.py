"""The whole production chain through the real queue, real handlers and real FFmpeg (small frames for speed)."""

from __future__ import annotations

import subprocess

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import session_scope
from app.enums import TaskStatus as S
from app.enums import VideoStage as V
from app.models.production import Asset, QAReport
from app.models.tasks import Task
from app.models.videos import Video
from app.production import assets, briefs, originality, pipeline, qa
from app.production import scripts as scripts_svc
from app.research import seo
from app.services import control, gate, org, tasks, videos
from app.storage import resolve_data_path
from app.worker.runner import HANDLERS, Runner
from tests.prod_helpers import HAVE_FFMPEG, LYRICS_A, brief_content

pytestmark = pytest.mark.skipif(not HAVE_FFMPEG, reason="FFmpeg/ffprobe not installed")
SPEC = {"width": 320, "height": 180, "preset": "ultrafast", "crossfade_s": 0.4}


def manager_of(db, task) -> str:
    return {"creative-director": "production-manager"}.get(task.assigned_agent.slug, "production-manager")


def review_all_submitted() -> int:
    n = 0
    with session_scope() as db:
        pm = org.actor_for_slug(db, "production-manager")
        for t in db.scalars(select(Task).where(Task.status == S.SUBMITTED.value)).all():
            tasks.review(db, pm, t.id, approve=True, notes="evidence checked")
            n += 1
    return n


def drive(runner: Runner, limit: int = 30) -> None:
    """Run the queue and let the manager verify submissions until nothing moves."""
    for _ in range(limit):
        ran = 0
        while runner.run_once() is not None:
            ran += 1
        if not ran and not review_all_submitted():
            return
        review_all_submitted()


def start(db, agent, *, channel="happy-birthday-songs", brief=None, lyrics=LYRICS_A, approve_brief=True, write_lyrics=True):
    pm = agent("production-manager")
    v = videos.create(db, pm, channel, "Garden Morning Song")
    t = pipeline.start_production(db, pm, v.id, title="Garden Morning Song", description="An original instrumental demo.",
                                  tags=["birthday"], spec=SPEC)
    db.commit()
    with session_scope() as s:
        director = org.actor_for_slug(s, "creative-director")
        tasks.begin(s, director, t["brief"].id)
        b = briefs.create(s, director, v.id, brief or brief_content())
        tasks.submit(s, director, t["brief"].id, result={"brief_id": b.id},
                     evidence_specs=[{"kind": "file", "ref": b.content["_artifact"], "note": "brief"}])
        if approve_brief:
            briefs.review(s, org.actor_for_slug(s, "production-manager"), b.id, approve=True)
    review_all_submitted()  # the brief task must be verified before the script task may start
    if write_lyrics:
        with session_scope() as s:
            w = org.actor_for_slug(s, "script-lyrics-writer")
            tasks.begin(s, w, t["script"].id)
            sv = scripts_svc.add_script(s, w, v.id, kind="lyrics", text=lyrics, author_kind="human", author_label="test fixture")
            tasks.submit(s, w, t["script"].id, result={"version": sv.version}, evidence_specs=[{"kind": "file", "ref": sv.artifact_path, "note": "lyrics"}])
    review_all_submitted()
    return v, {k: x.id for k, x in t.items()}


def finish(v_id, ids):
    with session_scope() as s:
        pm = org.actor_for_slug(s, "production-manager")
        qa_res = pipeline.finalize_qa(s, pm, v_id, ids["qa"])
        out = {"qa": qa_res}
        if qa_res["passed"]:
            pipeline.finalize_originality(s, v_id, ids["originality"])
            out["video"] = s.get(Video, v_id).originality_status
    return out


def test_full_production_from_brief_to_the_publishing_queue(db, agent):
    v, ids = start(db, agent)
    vid = v.id
    runner = Runner("e2e", HANDLERS)
    drive(runner)
    with session_scope(write=False) as s:
        statuses = {k: s.get(Task, i).status for k, i in ids.items()}
    assert set(statuses.values()) == {S.COMPLETED.value}, statuses

    res = finish(vid, ids)
    assert res["qa"] == {"passed": True, "corrections": []} and res["video"] == "PASS"

    with session_scope() as s:
        ceo = org.actor_for_slug(s, "ceo")
        video = s.get(Video, vid)
        # the file really exists, decodes, and is what QA measured
        final = resolve_data_path(video.final_video_path)
        info = qa.probe(final)
        assert float(info["format"]["duration"]) > 5 and {x["codec_type"] for x in info["streams"]} == {"video", "audio"}
        # QA ran every check, none failed
        report = s.scalar(select(QAReport).where(QAReport.video_id == vid))
        assert report.passed and {"decode", "loudness", "black_frames", "captions", "records"} <= {c["id"] for c in report.checks}
        assert all(c["status"] != "fail" for c in report.checks)
        # every asset has origin, licence and an unchanged file
        rows = assets.for_video(s, vid)
        assert {a.kind for a in rows} == {"music", "image", "caption", "font", "video", "thumbnail"}
        assert assets.license_problems(s, vid) == [] and all(a.commercial_use == "yes" and a.license_ref for a in rows)
        assert {a.provider for a in rows} <= {"local-procedural", "local-render", "bundled"}
        # the board version in force was recorded by every specialist task
        assert all(s.get(Task, i).board_version_used is not None for i in ids.values())
        # the title and description are not publishable until the research department has checked and approved them
        with pytest.raises(gate.GateBlocked) as e:
            videos.set_stage(s, org.actor_for_slug(s, "production-manager"), vid, V.READY)
        assert set(e.value.result.codes()) == {"CEO_RIGHTS_NOT_VERIFIED", "METADATA_NOT_APPROVED"}
        ms = seo.generate(s, org.actor_for_slug(s, "keyword-seo-specialist"), vid)
        assert ms.status == "CHECKED" and ms.generated_by == "template-v1"
        seo.approve(s, org.actor_for_slug(s, "research-manager"), ms.id, note="accurate to the finished video")
        # nothing gets in without the CEO's own inspection
        with pytest.raises(gate.GateBlocked) as e:
            videos.set_stage(s, org.actor_for_slug(s, "production-manager"), vid, V.READY)
        assert e.value.result.codes() == ["CEO_RIGHTS_NOT_VERIFIED"]
        originality.ceo_inspect(s, ceo, vid)
        videos.set_stage(s, org.actor_for_slug(s, "production-manager"), vid, V.READY)
        assert s.get(Video, vid).stage == V.READY.value
        # ...but the company is still in DEVELOPMENT mode, so it cannot actually go out
        assert "MODE_DEVELOPMENT" in gate.evaluate(s, s.get(Video, vid), purpose="publish").codes()


def test_the_soundtrack_is_honestly_labelled_as_an_instrumental(db, agent):
    v, ids = start(db, agent)
    drive(Runner("e2e", HANDLERS))
    with session_scope(write=False) as s:
        music = s.scalar(select(Asset).where(Asset.video_id == v.id, Asset.kind == "music"))
        assert music.params["vocals"] is False and music.params["meta"]["kind"] == "algorithmic instrumental"
        assert "no vocals" in music.params["meta"]["limitations"] and music.provider == "local-procedural" and music.seed


def test_production_waits_for_an_approved_brief_and_resumes_when_it_is_approved(db, agent):
    v, ids = start(db, agent, approve_brief=False)
    drive(Runner("e2e", HANDLERS))
    with session_scope(write=False) as s:
        for key in ("music", "thumbnail"):
            t = s.get(Task, ids[key])
            assert t.status == S.BLOCKED.value and "approved creative brief" in t.blocked_reason, (key, t.status)
        assert not s.scalars(select(Asset).where(Asset.video_id == v.id)).all()  # nothing was produced
        brief = briefs.latest(s, v.id)
    with session_scope() as s:  # the manager approves, then releases the parked tasks
        pm = org.actor_for_slug(s, "production-manager")
        briefs.review(s, pm, brief.id, approve=True)
        for key in ("music", "thumbnail"):
            tasks.unblock(s, pm, ids[key], "brief approved")
    drive(Runner("e2e", HANDLERS))
    with session_scope(write=False) as s:
        assert s.get(Task, ids["render"]).status == S.COMPLETED.value


def test_missing_lyrics_stop_the_captions_step(db, agent):
    v, ids = start(db, agent, write_lyrics=False)
    drive(Runner("e2e", HANDLERS))
    with session_scope(write=False) as s:
        assert s.get(Task, ids["captions"]).status in (S.WAITING_FOR_DEPENDENCY.value, S.FAILED.value)
        assert s.get(Task, ids["render"]).status != S.COMPLETED.value
        assert not any(a.kind == "video" for a in assets.for_video(s, v.id))


def test_lyrics_that_do_not_fit_the_music_fail_with_a_clear_reason(db, agent):
    v, ids = start(db, agent, lyrics="[Bridge]\nThis section does not exist in the song structure at all")
    drive(Runner("e2e", HANDLERS))
    with session_scope(write=False) as s:
        t = s.get(Task, ids["captions"])
        assert t.status == S.FAILED.value and "no 'bridge' section" in t.error["message"]


def test_failed_qa_opens_correction_tasks_and_blocks_the_video(db, agent):
    v, ids = start(db, agent)
    vid = v.id
    runner = Runner("e2e", HANDLERS)
    # run everything up to (not including) QA, then damage the rendered file
    for _ in range(20):
        with session_scope(write=False) as s:
            if s.get(Task, ids["render"]).status == S.COMPLETED.value and s.get(Task, ids["thumbnail"]).status == S.COMPLETED.value:
                break
        while runner.run_once() is not None:
            with session_scope(write=False) as s:
                if s.get(Task, ids["qa"]).status == S.RUNNING.value:
                    break
        review_all_submitted()
    with session_scope(write=False) as s:
        final = resolve_data_path(s.get(Video, vid).final_video_path)
    black = final.with_name("black.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x180:r=30:d=12", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=12", "-af", "volume=-12dB", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(black)], check=True)
    black.replace(final)  # the file now differs from what was registered, and it is a black screen
    drive(runner)
    res = finish(vid, ids)
    assert res["qa"]["passed"] is False and res["qa"]["corrections"]
    with session_scope() as s:
        v = s.get(Video, vid)
        assert not v.qa_passed and v.stage != V.PACKAGING.value
        fails = {c["id"] for c in s.scalar(select(QAReport).where(QAReport.video_id == vid)).checks if c["status"] == "fail"}
        assert {"black_frames", "opening", "records"} <= fails
        fix = s.scalars(select(Task).where(Task.task_type == "manual.correction", Task.video_id == vid)).all()
        assert {t.assigned_agent.slug for t in fix} >= {"video-editing-specialist", "copyright-originality-reviewer"}
        assert all("→" in t.description for t in fix)
        with pytest.raises(gate.GateBlocked) as e:
            videos.set_stage(s, org.actor_for_slug(s, "production-manager"), vid, V.READY)
        assert {"QA_NOT_PASSED", "ASSET_LICENSE_ISSUES"} <= set(e.value.result.codes())
        # asking again does not duplicate the correction tasks
        again = pipeline.create_correction_tasks(s, org.actor_for_slug(s, "production-manager"), vid, s.scalar(select(QAReport).where(QAReport.video_id == vid)))
        assert {t.id for t in again} == {t.id for t in fix}


def test_a_sung_plan_without_a_singing_provider_cannot_reach_the_queue(db, agent):
    v, ids = start(db, agent, brief=brief_content(musical_plan={"vocal_delivery": "sung"}))
    drive(Runner("e2e", HANDLERS))
    with session_scope(write=False) as s:
        music_task = s.get(Task, ids["music"])
        assert any("no singing provider" in w for w in music_task.result["warnings"])
    res = finish(v.id, ids)
    assert res["qa"]["passed"] is True and res["video"] == "NEEDS_REVISION"
    with session_scope() as s:
        video = s.get(Video, v.id)
        review = s.scalar(select(originality.OriginalityReview).where(originality.OriginalityReview.video_id == v.id))
        assert "VOCALS_MISSING" in {f["code"] for f in review.report["findings"]}
        with pytest.raises(Exception, match="not agree|NEEDS_REVISION|not PASS"):
            originality.ceo_inspect(s, org.actor_for_slug(s, "ceo"), v.id)
        with pytest.raises(gate.GateBlocked):
            videos.set_stage(s, org.actor_for_slug(s, "production-manager"), v.id, V.READY)
        assert video.stage == V.PACKAGING.value


def test_finalizers_refuse_unverified_tasks(db, agent):
    v, ids = start(db, agent)
    with session_scope() as s, pytest.raises(ValueError, match="manager must verify"):
        pipeline.finalize_qa(s, org.actor_for_slug(s, "production-manager"), v.id, ids["qa"])
    with session_scope() as s, pytest.raises(ValueError, match="not a production.originality_review"):
        pipeline.finalize_originality(s, v.id, ids["music"])


def test_pause_stops_production_between_steps_and_resume_continues(db, agent, owner):
    v, ids = start(db, agent)
    with session_scope() as s:
        control.pause(s, org.actor_for_slug(s, "ceo"), "check")
    runner = Runner("e2e", HANDLERS)
    assert runner.run_once() is None
    with session_scope(write=False) as s:
        assert s.get(Task, ids["music"]).status == S.QUEUED.value
    with session_scope() as s:
        from app.security.permissions import Actor

        control.resume(s, Actor.for_user(owner.user_id, "owner", "owner"))
    drive(runner)
    with session_scope(write=False) as s:
        assert s.get(Task, ids["render"]).status == S.COMPLETED.value
        assert get_settings().artifacts_dir.exists()


def test_the_vertical_short_format_renders_too(db, agent):
    pm = agent("production-manager")
    v = videos.create(db, pm, "happy-birthday-songs", "Short")
    t = pipeline.start_production(db, pm, v.id, title="Short", description="vertical", spec={**SPEC, "width": 180, "height": 320})
    db.commit()
    with session_scope() as s:
        d = org.actor_for_slug(s, "creative-director")
        tasks.begin(s, d, t["brief"].id)
        b = briefs.create(s, d, v.id, brief_content(planned_length_seconds=12))
        tasks.submit(s, d, t["brief"].id, result={}, evidence_specs=[{"kind": "file", "ref": b.content["_artifact"]}])
        briefs.review(s, org.actor_for_slug(s, "production-manager"), b.id, approve=True)
    review_all_submitted()
    with session_scope() as s:
        w = org.actor_for_slug(s, "script-lyrics-writer")
        tasks.begin(s, w, t["script"].id)
        sv = scripts_svc.add_script(s, w, v.id, kind="lyrics", text=LYRICS_A, author_kind="human", author_label="fixture")
        tasks.submit(s, w, t["script"].id, result={}, evidence_specs=[{"kind": "file", "ref": sv.artifact_path}])
    review_all_submitted()
    drive(Runner("e2e", HANDLERS))
    with session_scope(write=False) as s:
        info = qa.probe(resolve_data_path(s.get(Video, v.id).final_video_path))
        vs = next(x for x in info["streams"] if x["codec_type"] == "video")
        assert (vs["width"], vs["height"]) == (180, 320)
