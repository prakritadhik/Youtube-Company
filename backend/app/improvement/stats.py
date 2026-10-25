"""Comparing two groups of videos honestly.

A *permutation test* answers one narrow question: if the arm labels had been handed out at random, how often would a difference at least this large appear
by chance? It needs no assumption about the shape of the data, which matters because video figures are lumpy. With few videos it is exact (every possible
relabelling is counted); with more it uses a seeded random sample of relabellings, so the same data always gives the same answer.

What it cannot do, and what the wording of every result therefore avoids:

* it cannot make a *causal* claim unless the arms were assigned at random, before publication, and treated alike apart from the change;
* it cannot rescue a small sample: with k videos per arm the smallest p-value that can ever be reached is printed, and an experiment that cannot reach the
  threshold is called inconclusive before anyone looks at the data;
* it is a single pre-registered comparison. Looking at it repeatedly, or at many metrics, raises the chance of a false positive, and the result says so.
"""

from __future__ import annotations

import math
import random
from itertools import combinations
from statistics import mean, median, stdev
from typing import Any

ALPHA = 0.05
MAX_EXACT = 20_000  # largest number of relabellings counted exactly
DRAWS = 20_000


def summarize(values: list[float]) -> dict[str, Any]:
    n = len(values)
    if n == 0:
        return {"n": 0}
    return {"n": n, "mean": round(mean(values), 4), "median": round(median(values), 4), "sd": round(stdev(values), 4) if n > 1 else None,
            "min": min(values), "max": max(values)}


def permutation_test(control: list[float], treatment: list[float], *, seed: int) -> dict[str, Any]:
    """Two-sided test on the difference of means. Exact when the number of relabellings is small, otherwise a seeded sample."""
    n_c, n_t = len(control), len(treatment)
    pooled = [*control, *treatment]
    n = len(pooled)
    observed = abs(mean(treatment) - mean(control))
    total = math.comb(n, n_t)
    eps = 1e-12 * max(1.0, observed)
    if total <= MAX_EXACT:
        hits = 0
        biggest = 0.0
        top = 0
        s_all = sum(pooled)
        for idx in combinations(range(n), n_t):
            s_t = sum(pooled[i] for i in idx)
            d = abs(s_t / n_t - (s_all - s_t) / n_c)
            if d >= observed - eps:
                hits += 1
            if d > biggest + eps:
                biggest, top = d, 1
            elif abs(d - biggest) <= eps:
                top += 1
        return {"p_value": hits / total, "method": "exact permutation test (every possible relabelling counted)", "relabellings": total,
                "smallest_possible_p": top / total}
    rng = random.Random(seed)
    hits = 0
    for _ in range(DRAWS):
        rng.shuffle(pooled)
        d = abs(mean(pooled[:n_t]) - mean(pooled[n_t:]))
        if d >= observed - eps:
            hits += 1
    return {"p_value": (hits + 1) / (DRAWS + 1), "method": f"permutation test on {DRAWS:,} random relabellings (seeded: the same data gives the same answer)",
            "relabellings": total, "smallest_possible_p": 1 / (DRAWS + 1)}


def _cohens_d(a: list[float], b: list[float]) -> float | None:
    if len(a) < 2 or len(b) < 2:
        return None
    pooled = math.sqrt(((len(a) - 1) * stdev(a) ** 2 + (len(b) - 1) * stdev(b) ** 2) / (len(a) + len(b) - 2))
    return None if pooled == 0 else round((mean(b) - mean(a)) / pooled, 3)


def compare_groups(control: list[float], treatment: list[float], *, seed: int, higher_is_better: bool, min_per_group: int) -> dict[str, Any]:
    """The whole analysis. Below the planned sample size no p-value is computed at all, so nobody can act on an early peek."""
    c, t = summarize(control), summarize(treatment)
    base: dict[str, Any] = {"control": c, "treatment": t, "alpha": ALPHA, "min_per_group": min_per_group}
    if len(control) < min_per_group or len(treatment) < min_per_group:
        return {**base, "conclusion": "insufficient_data", "p_value": None, "suggested_decision": "repeat",
                "summary": (f"Not enough finished videos yet: {len(control)} control and {len(treatment)} treatment with complete figures, "
                            f"and the plan needs at least {min_per_group} in each. No test was run, so there is nothing to read into the numbers so far.")}
    test = permutation_test(control, treatment, seed=seed)
    diff = round(mean(treatment) - mean(control), 4)
    rel = round(100 * diff / abs(mean(control)), 1) if mean(control) else None
    better = (diff > 0) == higher_is_better and diff != 0
    p = test["p_value"]
    out = {**base, **test, "difference": diff, "relative_difference_percent": rel, "effect_size_d": _cohens_d(control, treatment)}
    if test["smallest_possible_p"] >= ALPHA:
        out.update(conclusion="cannot_reach_threshold", suggested_decision="repeat",
                   summary=(f"With {len(control)} and {len(treatment)} videos the smallest p-value that is possible is {test['smallest_possible_p']:.3f}, which cannot reach {ALPHA}. "
                            "This experiment cannot settle the question however the numbers fall; run it with more videos."))
    elif p < ALPHA:
        direction = "better" if better else "worse"
        out.update(conclusion="supported_improvement" if better else "supported_harm", suggested_decision="adopt" if better else "reject",
                   summary=(f"The treatment videos were {direction}: the means differ by {diff:+g}" + (f" ({rel:+g}%)" if rel is not None else "")
                            + f". A difference this large or larger arose in {100 * p:.1f}% of random relabellings (p = {p:.3f}, {test['method']})."))
    else:
        out.update(conclusion="no_clear_difference", suggested_decision="repeat",
                   summary=(f"No clear difference: the means differ by {diff:+g}" + (f" ({rel:+g}%)" if rel is not None else "")
                            + f", and a difference this large arose in {100 * p:.1f}% of random relabellings (p = {p:.3f}). "
                            "That does not show the change does nothing; it shows this experiment could not tell."))
    return out
