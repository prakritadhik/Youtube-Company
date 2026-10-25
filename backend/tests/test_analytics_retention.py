"""Retention analysis: plain arithmetic on the curve YouTube reported, with no claims about why viewers left."""

from __future__ import annotations

from app.analytics import collect, views
from app.analytics.retention import MIN_POINTS, analyse
from app.db import session_scope
from app.models.community import Comment
from tests.analytics_helpers import connect, daily_rows, thread, uploaded
from tests.google_fixtures import google  # noqa: F401


def curve(f, n=100):
    return [(i / n, f(i / n), None) for i in range(1, n + 1)]


def test_a_straight_decline_is_described_with_its_measured_marks():
    a = analyse(curve(lambda r: 1 - 0.7 * r))
    assert a["status"] == "available" and a["points"] == 100
    assert a["at"]["50%"] == 0.65 and a["at"]["25%"] == 0.825 and a["end"] == 0.3
    assert a["steepest_loss"]["loss_points"] == 3.5  # 0.7 * 5% of the video, in points of the audience
    assert any("65% of the audience was still watching 50%" in s for s in a["statements"]) and any("30% reached the end" in s for s in a["statements"])


def test_the_steepest_loss_is_found_where_it_really_is():
    a = analyse(curve(lambda r: 1.0 if r < 0.30 else 0.55 if r > 0.40 else 1.0 - 4.5 * (r - 0.30)))
    w = a["steepest_loss"]
    # the loss is 45 points over 10% of the video, so any 5% stretch inside the slope loses 22.5
    assert 0.30 <= w["from"] <= 0.36 and w["to"] - w["from"] >= 0.05 - 1e-9 and w["loss_points"] == 22.5


def test_a_curve_that_never_falls_has_no_steepest_loss_and_re_watching_is_kept():
    a = analyse(curve(lambda r: 1.0 + 0.2 * r))  # values above 1 are people re-watching
    assert a["steepest_loss"] is None and a["end"] == 1.2


def test_too_few_points_is_unavailable_not_a_guess():
    a = analyse([(i / 5, 1 - i / 10, None) for i in range(1, 6)])
    assert a["status"] == "unavailable" and str(MIN_POINTS) in a["reason"]


def test_a_curve_that_stops_early_has_no_end_figure():
    a = analyse([(i / 100, 1 - i / 200, None) for i in range(1, 81)])
    assert a["end"] is None and not any("reached the end" in s for s in a["statements"])


def test_relative_performance_is_reported_as_youtubes_own_number_with_a_caveat():
    pts = [(r, w, 1.2) for r, w, _ in curve(lambda r: 1 - 0.5 * r)]
    a = analyse(pts)
    assert a["relative_to_similar_videos"] == 1.2 and any("not documented" in s for s in a["statements"]) and "does not say why" in a["caveat"]


def test_the_video_view_carries_the_analysis_or_says_it_is_unavailable(db, owner, agent, google):
    connect(db, owner, google)
    vid = uploaded(db, agent, google)
    assert views.video_performance(db, vid)["retention_analysis"]["status"] == "unavailable"
    db.commit()
    google.analytics_days["vid001"] = daily_rows()
    google.retention["vid001"] = [(i / 100, 1 - 0.6 * i / 100, 1.0) for i in range(1, 101)]
    collect.collect_video(vid)
    db.expire_all()
    a = views.video_performance(db, vid)["retention_analysis"]
    assert a["status"] == "available" and a["at"]["50%"] == 0.7
    db.commit()
    other = uploaded(db, agent, google, yid="vid002", title="Other")
    assert views.video_performance(db, other)["retention_analysis"]["status"] == "unavailable"


def test_the_comment_summary_groups_categories_into_a_plain_feedback_picture(db, owner, agent, google):
    from app.community import collect as comments
    connect(db, owner, google)
    vid = uploaded(db, agent, google)
    google.threads["vid001"] = [thread("a", "Love it"), thread("b", "Great!"), thread("c", "so boring"), thread("d", "can you do one for Mia?"),
                                thread("e", "I want to kill myself"), thread("f", "check out my channel http://x.example"), thread("g", "ok")]
    comments.collect_video(vid)
    db.expire_all()
    s = views.comments_summary(db)
    assert s["sentiment"] == {"positive": 2, "negative": 1, "questions": 1, "needs_a_person": 1, "unclassified": 1}  # spam is left out; the rest is "other"
    assert "rules-v1" in s["method"]
    with session_scope(write=False) as sess:
        assert sess.query(Comment).count() == 7
