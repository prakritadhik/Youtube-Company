from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.db import session_scope
from app.enums import VideoStage
from app.models.videos import Video
from app.services import org
from app.worker.runner import HANDLERS, Runner
from app.youtube.quota import PACIFIC
from tests.api_fixtures import api_env, client, fast_hashing, make_client, owner_client, viewer_client  # noqa: F401
from tests.improvement_helpers import BDAY, PUBLISHED, add_qa, add_week, publish
from tests.prod_helpers import make_video

PLAN = {"channel": BDAY, "title": "Bright thumbnails", "hypothesis": "Brighter thumbnails get more first-week views.", "change": "Use the bright palette on the thumbnail.",
        "metric": {"metric": "views", "aggregate": "sum", "window_days": 7}, "min_per_group": 5}


def new_videos(n):
    with session_scope() as s:
        pm = lambda slug: org.actor_for_slug(s, slug)  # noqa: E731
        return [make_video(s, pm, BDAY, f"API video {i} {'plmoknijbu'[i]}{i}").id for i in range(n)]


def finished_with_a_corrected_failure():
    from app.services import videos
    from tests.test_gate_videos import make_ready_video
    with session_scope() as s:
        agent = lambda slug: org.actor_for_slug(s, slug)  # noqa: E731
        v = make_ready_video(s, agent, title="Garden Song")
        videos.set_stage(s, agent("production-manager"), v.id, VideoStage.READY)
        add_qa(s, v.id, 1, failing=("caption_sync",))
        add_qa(s, v.id, 2, passing=("duration", "loudness", "caption_sync"))
        return v.id


def test_viewers_read_experiments_and_comparisons_but_change_nothing(owner_client, viewer_client):
    assert viewer_client.get("/api/experiments").status_code == 200
    assert viewer_client.get(f"/api/compare?channel={BDAY}").status_code == 200
    assert viewer_client.get("/api/improvement/overview").status_code == 403  # raw lessons are the owner's working view
    for url, body in (("/api/experiments", PLAN), ("/api/experiments/1/enroll", {"video_id": 1}), ("/api/experiments/1/start", {}), ("/api/experiments/1/analyse", {}),
                      ("/api/experiments/1/decide", {"decision": "reject", "note": "x"}), ("/api/experiments/1/cancel", {"reason": "x"}),
                      ("/api/board/LSN-000001/activate", {}), ("/api/improvement/mine", {})):
        assert viewer_client.post(url, json=body).status_code == 403, url


def test_a_lesson_goes_from_the_mining_task_through_the_board_to_a_later_task_via_the_api(owner_client):
    finished_with_a_corrected_failure()
    t = owner_client.post("/api/improvement/mine").json()
    assert Runner("t", HANDLERS).run_once() == t["task_id"]
    ov = owner_client.get("/api/improvement/overview").json()
    lesson = ov["lessons"][0]
    assert ov["lessons_by_status"] == {"OBSERVED": 1} and lesson["status"] == "OBSERVED" and lesson["channel"] == BDAY and ov["unexamined_workflows"] == 0
    bad = owner_client.post(f"/api/board/{lesson['public_id']}/activate", json={})
    assert bad.status_code == 409 and "not allowed" in bad.json()["detail"]  # an observation is not an instruction
    assert owner_client.post(f"/api/board/{lesson['public_id']}/investigate", json={}).status_code == 409  # a reason is required
    assert owner_client.post(f"/api/board/{lesson['public_id']}/investigate", json={"reason": "reading the QA records"}).status_code == 200
    v = owner_client.post(f"/api/board/{lesson['public_id']}/verify", json={}).json()
    assert v["entry"]["status"] == "VERIFIED" and v["performed_by"].endswith("ceo")
    a = owner_client.post(f"/api/board/{lesson['public_id']}/activate", json={}).json()
    assert a["entry"]["status"] == "ACTIVE_INSTRUCTION"
    # it is on the owner-facing board now
    board = owner_client.get("/api/board").json()
    assert lesson["public_id"] in [e["public_id"] for e in board["sections"][BDAY]["ACTIVE_INSTRUCTION"]]
    assert owner_client.post(f"/api/board/{lesson['public_id']}/nonsense", json={}).status_code == 409


def test_an_experiment_runs_from_design_to_an_adopted_instruction_through_the_api(owner_client):
    e = owner_client.post("/api/experiments", json=PLAN).json()
    assert e["status"] == "DESIGN" and e["public_id"].startswith("EXP-")
    assert owner_client.post("/api/experiments", json={**PLAN, "min_per_group": 3}).status_code == 409
    ids = new_videos(10)
    arms = {}
    for vid in ids:
        r = owner_client.post(f"/api/experiments/{e['id']}/enroll", json={"video_id": vid})
        assert r.status_code == 200, r.text
        arms[vid] = r.json()["arm"]
    assert sorted(arms.values()).count("treatment") == 5
    assert owner_client.post(f"/api/experiments/{e['id']}/enroll", json={"video_id": ids[0]}).status_code == 409
    mine = owner_client.get(f"/api/videos/{ids[0]}/experiments").json()["experiments"][0]
    assert mine["arm"] == arms[ids[0]] and mine["experiment"] == e["public_id"]

    started = owner_client.post(f"/api/experiments/{e['id']}/start").json()
    assert started["status"] == "RUNNING" and started["board_status"] == "APPROVED_FOR_TESTING"
    early = owner_client.post(f"/api/experiments/{e['id']}/analyse").json()
    assert early["analysis"]["conclusion"] == "insufficient_data" and early["analysis"]["p_value"] is None
    assert owner_client.post(f"/api/experiments/{e['id']}/decide", json={"decision": "adopt", "note": "I like it"}).status_code == 409

    went_public = datetime.now(UTC) - timedelta(days=30)  # the API uses the real clock, so the first week sits a month back
    with session_scope() as s:  # ten videos go public and have their first week measured
        for vid in ids:
            publish(s, vid, went_public)
            add_week(s, vid, 200 if arms[vid] == "treatment" else 100, first=went_public.astimezone(PACIFIC).date())
    done = owner_client.post(f"/api/experiments/{e['id']}/analyse").json()
    assert done["analysis"]["conclusion"] == "supported_improvement" and done["analysis"]["confidence"] == "medium"
    detail = owner_client.get(f"/api/experiments/{e['id']}").json()
    assert len(detail["videos"]) == 10 and len(detail["assignments"]) == 10 and detail["seed"] and detail["assignments"][0]["draw"]["method"].startswith("permuted blocks")
    out = owner_client.post(f"/api/experiments/{e['id']}/decide", json={"decision": "adopt", "note": "clear gap over ten videos"}).json()
    assert out["experiment"]["status"] == "CONCLUDED" and out["experiment"]["board_status"] == "ACTIVE_INSTRUCTION"
    assert owner_client.post(f"/api/experiments/{e['id']}/decide", json={"decision": "reject", "note": "again"}).status_code == 409  # decided once


def test_the_comparison_endpoint_validates_and_never_invents(owner_client):
    assert owner_client.get(f"/api/compare?channel={BDAY}&metric=revenue").status_code == 422
    assert owner_client.get("/api/compare?channel=nope").status_code == 422
    body = owner_client.get(f"/api/compare?channel={BDAY}&metric=views&window_days=7").json()
    assert body["videos"] == [] and body["summary"]["n"] == 0 and body["caveats"]
    ids = new_videos(1)
    with session_scope() as s:
        publish(s, ids[0], PUBLISHED)
        s.get(Video, ids[0])
    row = owner_client.get(f"/api/compare?channel={BDAY}").json()["videos"][0]
    assert row["status"] == "incomplete" and row["value"] is None and "no figure for 7 of 7 days" in row["reason"]


