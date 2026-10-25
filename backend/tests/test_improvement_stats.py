"""The statistics behind experiments: exact where it can be, seeded where it cannot, and honest about what a small sample can show."""

from __future__ import annotations

from itertools import combinations
from statistics import mean

from app.improvement import stats


def brute_force_p(control, treatment):
    pooled = control + treatment
    obs = abs(mean(treatment) - mean(control))
    total = hits = 0
    for idx in combinations(range(len(pooled)), len(treatment)):
        t = [pooled[i] for i in idx]
        c = [pooled[i] for i in range(len(pooled)) if i not in idx]
        total += 1
        hits += abs(mean(t) - mean(c)) >= obs - 1e-9
    return hits / total


def test_the_exact_test_matches_a_brute_force_count():
    c, t = [3, 5, 4, 6, 2, 5], [8, 9, 7, 6, 10, 7]
    r = stats.permutation_test(c, t, seed=1)
    assert abs(r["p_value"] - brute_force_p(c, t)) < 1e-12 and r["relabellings"] == 924 and "exact" in r["method"]


def test_five_against_five_can_reach_significance_but_three_against_three_never_can():
    five = stats.compare_groups([1, 2, 3, 2, 1], [9, 8, 9, 10, 8], seed=1, higher_is_better=True, min_per_group=5)
    assert five["conclusion"] == "supported_improvement" and abs(five["p_value"] - 2 / 252) < 1e-12 and five["suggested_decision"] == "adopt"
    three = stats.compare_groups([1, 2, 3], [9, 8, 9], seed=1, higher_is_better=True, min_per_group=3)
    assert three["conclusion"] == "cannot_reach_threshold" and three["smallest_possible_p"] == 0.1 and "cannot settle the question" in three["summary"]


def test_below_the_planned_sample_no_test_is_run_at_all():
    r = stats.compare_groups([1, 2, 3, 4], [50, 60, 70, 80], seed=1, higher_is_better=True, min_per_group=5)
    assert r["conclusion"] == "insufficient_data" and r["p_value"] is None and "No test was run" in r["summary"] and r["suggested_decision"] == "repeat"
    assert "difference" not in r  # the numbers are not interpreted before the plan says they can be


def test_no_difference_is_not_reported_as_proof_of_no_effect():
    r = stats.compare_groups([5, 6, 5, 7, 6, 5], [6, 5, 7, 5, 6, 6], seed=1, higher_is_better=True, min_per_group=5)
    assert r["conclusion"] == "no_clear_difference" and r["p_value"] > 0.05 and "does not show the change does nothing" in r["summary"] and r["suggested_decision"] == "repeat"


def test_direction_matters_a_supported_drop_is_harm_when_higher_is_better_and_improvement_when_lower_is():
    c, t = [10, 11, 12, 11, 10], [2, 3, 2, 3, 2]
    assert stats.compare_groups(c, t, seed=1, higher_is_better=True, min_per_group=5)["conclusion"] == "supported_harm"
    assert stats.compare_groups(c, t, seed=1, higher_is_better=True, min_per_group=5)["suggested_decision"] == "reject"
    assert stats.compare_groups(c, t, seed=1, higher_is_better=False, min_per_group=5)["conclusion"] == "supported_improvement"


def test_large_samples_use_a_seeded_sample_that_gives_the_same_answer_every_time():
    c, t = [float(i % 7) for i in range(25)], [float(i % 7) + 0.4 for i in range(25)]
    a, b = stats.permutation_test(c, t, seed=42), stats.permutation_test(c, t, seed=42)
    assert a == b and "seeded" in a["method"] and a["relabellings"] > stats.MAX_EXACT


def test_identical_groups_give_p_one_and_effect_size_is_reported_with_the_difference():
    r = stats.compare_groups([4, 4, 4, 4, 4], [4, 4, 4, 4, 4], seed=1, higher_is_better=True, min_per_group=5)
    assert r["p_value"] == 1.0 and r["difference"] == 0 and r["effect_size_d"] is None
    s = stats.compare_groups([1, 2, 3, 2, 1], [9, 8, 9, 10, 8], seed=1, higher_is_better=True, min_per_group=5)
    assert s["difference"] == 7.0 and s["relative_difference_percent"] == 388.9 and s["effect_size_d"] > 5


def test_summary_of_values():
    assert stats.summarize([]) == {"n": 0}
    s = stats.summarize([2, 4, 6])
    assert (s["n"], s["mean"], s["median"], s["min"], s["max"]) == (3, 4, 4, 2, 6)
