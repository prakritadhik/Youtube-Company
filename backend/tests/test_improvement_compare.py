"""Comparing videos on one channel: the same measurement for every video, with the reasons for the ones that cannot be measured, and no verdicts."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.improvement import compare, measure
from app.improvement import experiments as ex
from app.services import videos
from tests.improvement_helpers import BDAY, PUBLISHED, STORY, TODAY, add_week, publish, spec
from tests.prod_helpers import make_video

_n = [0]


def video(db, agent, per_day=None, *, channel=BDAY, published=True, when=PUBLISHED, days=7):
    _n[0] += 1
    v = make_video(db, agent, channel, f"Compare video {_n[0]} {'qwertyuiop'[_n[0] % 10]}{_n[0]}")
    if published:
        publish(db, v.id, when)
        if per_day is not None:
            add_week(db, v.id, per_day, first=when.date(), days=days)
    return v.id


def test_videos_are_ranked_against_the_channels_own_median_with_reasons_for_those_left_out(db, owner, agent):
    a, b, c, d, e = (video(db, agent, x) for x in (10, 20, 30, 40, 50))
    young = video(db, agent, 5, when=PUBLISHED + timedelta(days=15))  # published 5 days before "today": its window has not finished
    draft = video(db, agent, published=False)
    out = compare.compare(db, BDAY, spec(), today=TODAY)
    by = {t["video_id"]: t for t in out["videos"]}
    assert out["summary"]["n"] == 5 and out["summary"]["of"] == 6
    assert out["summary"]["median"] == 210.0 and (out["summary"]["min"], out["summary"]["max"]) == (70.0, 350.0)
    assert [by[v]["rank"] for v in (e, d, c, b, a)] == [1, 2, 3, 4, 5]  # higher is better by default
    assert by[e]["versus_median_percent"] == 66.7 and by[a]["versus_median_percent"] == -66.7
    assert by[young]["status"] == "incomplete" and "revises recent days" in by[young]["reason"] and "rank" not in by[young]
    assert draft not in by  # only published or scheduled videos are compared
    assert out["caveats"][0].startswith("This compares videos; it is not an experiment")
    assert "note" not in out["summary"]


def test_lower_is_better_reverses_the_ranking_and_few_videos_get_a_caution(db, owner, agent):
    a, b = video(db, agent, 10), video(db, agent, 20)
    out = compare.compare(db, BDAY, spec(higher_is_better=False), today=TODAY)
    by = {t["video_id"]: t for t in out["videos"]}
    assert by[a]["rank"] == 1 and by[b]["rank"] == 2
    assert "too few to say what is normal" in out["summary"]["note"]


def test_a_missing_day_is_a_reason_not_a_zero_in_the_summary(db, owner, agent):
    full, gap = video(db, agent, 10), video(db, agent, 10, days=6)
    out = compare.compare(db, BDAY, spec(), today=TODAY)
    by = {t["video_id"]: t for t in out["videos"]}
    assert by[full]["status"] == "complete" and by[gap]["status"] == "incomplete" and "never counted as zero" in by[gap]["reason"] and out["summary"]["n"] == 1


def test_channels_are_never_mixed_and_experiment_arms_are_shown(db, owner, agent):
    mine = video(db, agent, 10)
    other = videos.create(db, agent("production-manager"), STORY, "A story elsewhere")
    publish(db, other.id)
    add_week(db, other.id, 99)
    e = ex.create(db, owner, channel_slug=BDAY, title="An experiment", hypothesis="Something changes the first week.", change="Do the thing differently.", metric=spec(), min_per_group=5)
    row = db.get(videos.Video, mine)
    row.stage, row.published_at, row.youtube_video_id = "READY", None, None  # not yet published, so it can still be assigned
    ex.enroll(db, owner, e.id, mine)
    publish(db, mine)
    out = compare.compare(db, BDAY, spec(), today=TODAY)
    assert [t["video_id"] for t in out["videos"]] == [mine] and out["videos"][0]["experiment"] == e.public_id and out["videos"][0]["arm"] in ("control", "treatment")


def test_an_unknown_channel_or_a_bad_metric_is_refused(db, owner):
    with pytest.raises(measure.MeasureError):
        compare.compare(db, "nope", spec())
    with pytest.raises(measure.MeasureError):
        compare.compare(db, BDAY, spec(metric="revenue"))
