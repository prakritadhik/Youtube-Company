"""Reading an audience-retention curve. Pure arithmetic on the points YouTube reported; it describes the curve and never says why viewers left.

``audience_watch_ratio`` is YouTube's measure of how much of the audience is watching at that point (it can exceed 1.0 when people re-watch).
Positions are shares of the video's length, because the app does not store a measured duration for uploaded videos.
"""

from __future__ import annotations

from typing import Any

MIN_POINTS = 10  # a curve with fewer points is too coarse to describe
WINDOW = 0.05
MARKS = (0.1, 0.25, 0.5, 0.75)
CAVEAT = ("This describes the curve YouTube reported. It does not say why viewers left, and a video with few views gives a noisy curve; "
          "compare it with other videos only when each has a similar number of views.")


def _near(points: list[tuple[float, float]], ratio: float, tol: float = 0.02) -> float | None:
    best = min(points, key=lambda p: abs(p[0] - ratio))
    return best[1] if abs(best[0] - ratio) <= tol else None


def analyse(points: list[tuple[float, float, float | None]]) -> dict[str, Any]:
    """``points`` are (position 0..1, audience watch ratio, relative retention or None). Returns measured facts only."""
    pts = sorted((r, w) for r, w, _ in points)
    if len(pts) < MIN_POINTS:
        return {"status": "unavailable", "reason": f"YouTube returned only {len(pts)} retention point(s); at least {MIN_POINTS} are needed to describe a curve."}
    marks = {f"{int(m * 100)}%": _near(pts, m) for m in MARKS}
    end = pts[-1][1] if pts[-1][0] >= 0.98 else None
    # the steepest loss over any 5%-wide stretch
    worst: dict[str, Any] | None = None
    biggest = 0.0
    for i, (r0, w0) in enumerate(pts):
        later = next(((r1, w1) for r1, w1 in pts[i + 1:] if r1 - r0 >= WINDOW - 1e-9), None)
        if later is None:
            break
        loss = w0 - later[1]
        if loss > biggest:
            biggest, worst = loss, {"from": round(r0, 3), "to": round(later[0], 3), "loss_points": round(100 * loss, 1)}
    rel = [x for _, _, x in points if x is not None]
    out: dict[str, Any] = {"status": "available", "points": len(pts), "first_point": {"at": round(pts[0][0], 3), "watching": round(pts[0][1], 3)},
                           "at": {k: (round(v, 3) if v is not None else None) for k, v in marks.items()}, "end": round(end, 3) if end is not None else None,
                           "steepest_loss": worst,
                           "relative_to_similar_videos": round(sum(rel) / len(rel), 2) if rel else None, "caveat": CAVEAT}
    out["statements"] = statements(out)
    return out


def statements(a: dict[str, Any]) -> list[str]:
    s = []
    for k, v in a["at"].items():
        if v is not None:
            s.append(f"{round(100 * v)}% of the audience was still watching {k} of the way through.")
    if a["end"] is not None:
        s.append(f"{round(100 * a['end'])}% reached the end.")
    w = a["steepest_loss"]
    if w:
        s.append(f"The steepest loss was between {round(100 * w['from'])}% and {round(100 * w['to'])}% of the video: {w['loss_points']} points of the audience.")
    if a["relative_to_similar_videos"] is not None:
        s.append(f"YouTube rates retention at about {a['relative_to_similar_videos']}× the typical for similar videos (above 1 is better); how it makes that comparison is not documented here.")
    return s
