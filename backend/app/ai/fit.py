"""Will this text fit the music it is meant for? Computed from the plan alone, with the same rules the caption and QA stages apply later.

Lyrics are checked against the *planned* timeline (the bars the music generator will be given, and the same arithmetic it
uses), so a draft that passes here also aligns into readable captions after the music has been composed.
"""

from __future__ import annotations

import math
import re
from typing import Any

from app.production import captions as cap
from app.production.pipeline import plan_bars

SECTION_NAME = re.compile(r"^(intro|verse|chorus|bridge|outro)( \d+)?$")
LYRIC_BASES = ("verse", "chorus", "bridge")
TAIL_S = 1.2  # the music's ring-out after the last bar


def structure_problems(structure: list[str], channel_kind: str) -> list[str]:
    problems = []
    if len(set(structure)) != len(structure):
        problems.append("musical_plan.song_structure must not repeat a section name (number repeats: 'verse', 'verse 2')")
    for s in structure:
        if not SECTION_NAME.match(s):
            problems.append(f"musical_plan.song_structure entry {s!r} must be intro, verse, chorus, bridge or outro, optionally followed by a number")
    if channel_kind == "birthday" and not any(s.split()[0] in LYRIC_BASES for s in structure):
        problems.append("musical_plan.song_structure needs at least one verse, chorus or bridge to carry lyrics")
    return problems


def planned_timeline(structure: list[str], bpm: float, planned_s: float) -> tuple[list[dict[str, Any]], float]:
    bars = plan_bars(structure, bpm, planned_s)
    bar_s = 240.0 / bpm
    t, out = 0.0, []
    for name in structure:
        out.append({"section": name, "base": name.split()[0], "bars": bars[name], "start_s": t, "end_s": t + bars[name] * bar_s})
        t += bars[name] * bar_s
    return out, t + TAIL_S


def lyric_plan(structure: list[str], bpm: float, planned_s: float) -> list[dict[str, Any]]:
    """For each lyric section: the header to use, how many lines, and the longest line that stays readable on screen."""
    timeline, _ = planned_timeline(structure, bpm, planned_s)
    plan = []
    for sec in timeline:
        if sec["base"] not in LYRIC_BASES:
            continue
        usable = (sec["end_s"] - 0.05) - (sec["start_s"] + 0.15)
        n = max(2, min(8, round(usable / 3.0)))
        slot = usable / n
        longest = max(12, min(cap.MAX_LINE_CHARS, int(cap.MAX_CPS * (slot - 0.08))) - 2)
        plan.append({"header": f"[{sec['section'].title()}]", "lines": n, "max_chars": longest, "seconds": round(sec["end_s"] - sec["start_s"], 1)})
    return plan


def lyric_problems(text: str, structure: list[str], bpm: float, planned_s: float) -> list[str]:
    """Exactly what the captions stage would complain about, found now, in plain words."""
    timeline, total = planned_timeline(structure, bpm, planned_s)
    try:
        cues = cap.align_lyrics(cap.parse_lyrics(text), timeline)
    except cap.CaptionError as e:
        return [str(e)]
    return [i["message"] for i in cap.validate_cues(cues, total)]


def script_budget(structure: list[str], bpm: float, planned_s: float) -> dict[str, Any]:
    """How much spoken-style text a script can carry, so it neither runs past the video nor leaves it empty."""
    _, total = planned_timeline(structure, bpm, planned_s)
    window = total - 1.5
    lo = max(3, math.ceil(window / 5.0))  # captions may stay on screen for at most MAX_CUE_S, and sentences are not all the same length
    hi = max(lo, int(window / 2.6))
    return {"seconds": round(window, 1), "min_sentences": lo, "max_sentences": hi, "max_chars_per_sentence": int(cap.MAX_CPS * 2.6)}


def script_problems(text: str, structure: list[str], bpm: float, planned_s: float) -> list[str]:
    _, total = planned_timeline(structure, bpm, planned_s)
    try:
        cues = cap.align_text_estimated(text, 0.5, total - 1.0)
    except cap.CaptionError as e:
        return [str(e)]
    return [i["message"] for i in cap.validate_cues(cues, total)]
