"""Collecting performance data: every figure has a source, every gap has a reason, and nothing missing becomes zero."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from app.analytics import collect
from app.db import session_scope
from app.models.community import Metric, MetricGap, RetentionPoint
from app.models.tasks import Task
from app.models.youtube import QuotaEntry
from app.services import settings as settings_svc
from app.services import tasks as tasks_svc
from app.storage import resolve_data_path
from app.worker.runner import HANDLERS, Runner
from tests.analytics_helpers import CORE, DAYS, connect, daily_rows, uploaded
from tests.google_fixtures import google  # noqa: F401


def metrics(video_id, source=None):
    with session_scope(write=False) as s:
        q = select(Metric).where(Metric.video_id == video_id)
        if source:
            q = q.where(Metric.source == source)
        return [(m.metric, str(m.day), m.value, m.source) for m in s.scalars(q)]


def gaps(video_id):
    with session_scope(write=False) as s:
        return {(g.metric, g.source): g.reason for g in s.scalars(select(MetricGap).where(MetricGap.video_id == video_id))}


@pytest.fixture
def setup(db, owner, agent, google):  # noqa: F811
    connect(db, owner, google)
    vid = uploaded(db, agent, google)
    google.analytics_days["vid001"] = daily_rows()
    google.retention["vid001"] = [(0.01, 1.0, 1.1), (0.5, 0.62, 0.9), (1.0, 0.31, 0.8)]
    return vid


def test_totals_daily_figures_and_retention_are_stored_each_with_its_source(db, setup, google):
    out = collect.collect_video(setup)
    assert out["stats"] and out["analytics_rows"] == 6 and out["retention_points"] == 3  # core (3 days) + reach (3 days)
    got = metrics(setup)
    assert ("views_total", str(collect.pacific_today()), 120.0, "youtube_data") in got
    assert ("likes_total", str(collect.pacific_today()), 9.0, "youtube_data") in got
    for m in (*CORE, "impressions", "impressionsClickThroughRate"):
        assert sorted(d for n, d, _, src in got if n == m and src == "youtube_analytics") == DAYS, m
    assert {src for *_, src in got} == {"youtube_data", "youtube_analytics"}  # never mixed up
    with session_scope(write=False) as s:
        pts = s.scalars(select(RetentionPoint).where(RetentionPoint.video_id == setup).order_by(RetentionPoint.ratio)).all()
        assert [(p.ratio, p.audience_watch_ratio) for p in pts] == [(0.01, 1.0), (0.5, 0.62), (1.0, 0.31)]
    assert gaps(setup) == {}


def test_a_figure_youtube_will_not_give_is_a_gap_with_its_reason_never_a_zero(db, setup, google):
    google.analytics_refused["impressions"] = (400, "badRequest", "impressions are not available for this channel")
    out = collect.collect_video(setup)
    got = metrics(setup)
    assert not [1 for n, *_ in got if n in ("impressions", "impressionsClickThroughRate")]  # no row at all, in particular no zero
    g = gaps(setup)
    assert "not available for this channel" in g[("impressions", "youtube_analytics")] and ("impressionsClickThroughRate", "youtube_analytics") in g
    assert any(x["metric"] == "impressions" for x in out["gaps"])
    assert [1 for n, *_ in got if n == "views"]  # the rest still arrived


def test_a_null_in_a_returned_row_is_skipped_never_stored_as_zero(db, setup, google):
    rows = daily_rows()
    rows[1]["averageViewDuration"] = None  # YouTube reports no average for a day without views
    google.analytics_days["vid001"] = rows
    collect.collect_video(setup)
    got = metrics(setup, "youtube_analytics")
    assert sorted(d for n, d, v, _ in got if n == "averageViewDuration") == [DAYS[0], DAYS[2]]  # the empty day has no row at all
    assert sorted(d for n, d, v, _ in got if n == "views") == DAYS  # the other metrics of that day are unaffected


def test_a_video_with_no_data_yet_gets_gaps_not_zero_rows(db, setup, google):
    google.analytics_days["vid001"] = []
    google.retention["vid001"] = []
    collect.collect_video(setup)
    assert not [1 for n, *_ in metrics(setup, "youtube_analytics")]
    g = gaps(setup)
    assert "no rows" in g[("views", "youtube_analytics")] and "no retention" in g[("audienceWatchRatio", "youtube_analytics")]
    assert [1 for n, *_ in metrics(setup, "youtube_data")]  # the cumulative totals are still real


def test_without_the_analytics_permission_nothing_is_asked_for_and_the_gap_says_how_to_fix_it(db, owner, agent, google):
    connect(db, owner, google, groups=())
    vid = uploaded(db, agent, google)
    google.analytics_days["vid001"] = daily_rows()
    collect.collect_video(vid)
    assert google.calls("youtubeanalytics") == []
    assert "permission not granted" in gaps(vid)[("views", "youtube_analytics")] and "Settings" in gaps(vid)[("views", "youtube_analytics")]
    assert metrics(vid, "youtube_analytics") == [] and metrics(vid, "youtube_data")


def test_a_refused_request_is_reported_in_googles_words(db, setup, google):
    google.fail["analytics.reports"] = [(403, "forbidden", "The caller does not have access to this report")] * 3
    collect.collect_video(setup)
    assert "does not have access" in gaps(setup)[("views", "youtube_analytics")]


def test_collecting_again_updates_in_place_and_clears_the_gap(db, setup, google):
    google.analytics_refused["impressions"] = (400, "badRequest", "not offered")
    collect.collect_video(setup)
    assert ("impressions", "youtube_analytics") in gaps(setup)
    del google.analytics_refused["impressions"]
    google.stats["vid001"]["viewCount"] = "200"
    google.retention["vid001"] = [(0.01, 1.0, 1.0)]
    collect.collect_video(setup)
    got = metrics(setup)
    assert [v for n, d, v, _ in got if n == "views_total"] == [200.0]  # one row for the day, updated
    assert len(got) == len(set((n, d, s) for n, d, _, s in got))  # no duplicates
    assert ("impressions", "youtube_analytics") not in gaps(setup)
    with session_scope(write=False) as s:
        assert s.scalar(select(func.count()).select_from(RetentionPoint).where(RetentionPoint.video_id == setup)) == 1  # replaced, not appended


def test_raw_responses_are_saved_and_referenced(db, setup, google):
    collect.collect_video(setup)
    with session_scope(write=False) as s:
        refs = {m.response_ref for m in s.scalars(select(Metric).where(Metric.video_id == setup))}
    assert refs and None not in refs
    for ref in refs:
        doc = json.loads(resolve_data_path(ref).read_text())
        assert doc["provider"] == "youtube" and doc["status_code"] == 200 and doc["simulated"] is False and "response" in doc


def test_data_api_calls_are_charged_to_the_quota_and_analytics_calls_are_not(db, setup, google):
    collect.collect_video(setup)
    with session_scope(write=False) as s:
        used = [(q.method, q.units) for q in s.scalars(select(QuotaEntry))]
    assert [u for u in used if u[0] != "channels.list"] == [("videos.list", 1)]  # (channels.list was the identity check when connecting)


def test_one_video_failing_never_stops_the_others(db, owner, agent, google):
    connect(db, owner, google)
    a = uploaded(db, agent, google, yid="vidA", title="One")
    b = uploaded(db, agent, google, yid="vidB", title="Two")
    google.analytics_days["vidB"] = daily_rows()
    google.fail["videos.list"] = [(500, "backendError", "boom")]
    out = collect.collect_all()
    assert {v["video_id"] for v in out["videos"]} == {a, b}
    assert "500" in gaps(a)[("views_total", "youtube_data")]
    assert [1 for n, *_ in metrics(b, "youtube_data")] and [1 for n, *_ in metrics(b, "youtube_analytics")]


def test_the_quota_guard_stops_collection_and_the_run_reports_it(db, owner, agent, google):
    connect(db, owner, google)
    uploaded(db, agent, google, yid="vidA", title="One")
    uploaded(db, agent, google, yid="vidB", title="Two")
    settings_svc.set_value(db, owner, "youtube", {"daily_quota_units": 2})  # connecting used 1 unit, so exactly one video fits
    db.commit()
    out = collect.collect_all()
    assert sum(1 for v in out["videos"] if "error" in v) == 1 and "quota" in next(v["error"] for v in out["videos"] if "error" in v).lower()


def test_nothing_is_collected_for_a_video_that_is_not_on_youtube_or_a_channel_that_is_not_connected(db, owner, agent, google):
    from tests.prod_helpers import make_video
    v = make_video(db, agent)
    db.commit()
    with pytest.raises(collect.CollectError):
        collect.collect_video(v.id)
    assert collect.collect_all()["connected_channels"] == 0
    assert "No YouTube channel is connected" in collect.collect_all()["note"]


def test_the_scheduled_task_completes_even_when_nothing_is_connected(db, owner, system, google):
    t = tasks_svc.create(db, system, title="Collect performance data", task_type="analytics.collect", review_required=False, idempotency_key="t-analytics-1")
    db.commit()
    assert Runner("t", HANDLERS).run_once() == t.id
    with session_scope(write=False) as s:
        row = s.get(Task, t.id)
        assert row.status == "COMPLETED" and row.result["connected_channels"] == 0 and "No YouTube channel is connected" in row.result["note"]


def test_the_scheduled_task_reports_what_arrived_and_what_did_not(db, setup, system, google):
    google.analytics_refused["impressions"] = (400, "badRequest", "not offered")
    t = tasks_svc.create(db, system, title="Collect performance data", task_type="analytics.collect", review_required=False, idempotency_key="t-analytics-2")
    db.commit()
    Runner("t", HANDLERS).run_once()
    with session_scope(write=False) as s:
        row = s.get(Task, t.id)
        assert row.status == "COMPLETED" and row.result["videos"] == 1 and row.result["rows_stored"] > 0 and row.result["gaps"] == 2
