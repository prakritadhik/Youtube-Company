"""Caption building: lyrics aligned to the generated music's bars, or estimated timing for spoken text."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.production.similarity import strip_markers

_HEADER = re.compile(r"^\s*\[(?P<name>[^\]]+)\]\s*$")
MIN_CUE_S, MAX_CUE_S, MAX_CPS, MAX_LINE_CHARS = 0.8, 7.0, 20.0, 42


class CaptionError(ValueError):
    pass


def parse_lyrics(text: str) -> list[dict[str, Any]]:
    """``[Verse 1]`` / ``[Chorus]`` headers followed by lines. Text without headers is one section."""
    sections: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for raw in strip_markers(text).splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _HEADER.match(line)
        if m:
            name = m.group("name").strip()
            cur = {"name": name, "base": name.lower().split()[0], "lines": []}
            sections.append(cur)
        else:
            if cur is None:
                cur = {"name": "lyrics", "base": "lyrics", "lines": []}
                sections.append(cur)
            cur["lines"].append(line)
    return [s for s in sections if s["lines"]]


def align_lyrics(sections: list[dict[str, Any]], timeline: list[dict[str, Any]], *, lead_in_s: float = 0.15) -> list[dict[str, Any]]:
    """Give every lyric line a time slot inside the matching musical section. Real alignment to known bar times."""
    used: set[int] = set()
    cues: list[dict[str, Any]] = []
    for sec in sections:
        match = next((i for i, t in enumerate(timeline) if i not in used and t["base"] == sec["base"]), None)
        if match is None:
            raise CaptionError(f"the music has no '{sec['base']}' section left for the lyric section '{sec['name']}'")
        used.add(match)
        t0, t1 = timeline[match]["start_s"] + lead_in_s, timeline[match]["end_s"] - 0.05
        n = len(sec["lines"])
        slot = (t1 - t0) / n
        for k, line in enumerate(sec["lines"]):
            cues.append({"start": round(t0 + k * slot, 3), "end": round(t0 + (k + 1) * slot - 0.08, 3), "text": line,
                         "section": sec["name"], "timing": "aligned-to-music-bars"})
    return cues


def align_text_estimated(text: str, start: float, end: float) -> list[dict[str, Any]]:
    """Spread sentences over a time span in proportion to length. Timing is an *estimate* and is labelled as such."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", strip_markers(text)) if s.strip()]
    if not sentences:
        raise CaptionError("no text to caption")
    weights = [max(len(s), 1) for s in sentences]
    total = sum(weights)
    t, cues = start, []
    for s, w in zip(sentences, weights, strict=True):
        d = (end - start) * w / total
        cues.append({"start": round(t, 3), "end": round(t + d - 0.08, 3), "text": s, "section": "narration",
                     "timing": "estimated-from-text-length"})
        t += d
    return cues


def wrap(text: str, width: int = MAX_LINE_CHARS) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        if len(f"{cur} {w}".strip()) <= width or not cur:
            cur = f"{cur} {w}".strip()
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def validate_cues(cues: list[dict[str, Any]], duration_s: float) -> list[dict[str, str]]:
    """Readability rules from the QA stage: length, reading speed, overlap and fit inside the video."""
    issues: list[dict[str, str]] = []
    prev_end = -1.0
    for i, c in enumerate(cues, 1):
        d = c["end"] - c["start"]
        label = f"cue {i} ({c['text'][:24]!r})"
        if d < MIN_CUE_S:
            issues.append({"code": "TOO_SHORT", "message": f"{label} is on screen for only {d:.2f}s"})
        if d > MAX_CUE_S:
            issues.append({"code": "TOO_LONG", "message": f"{label} stays for {d:.2f}s"})
        if d > 0 and len(c["text"]) / d > MAX_CPS:
            issues.append({"code": "TOO_FAST", "message": f"{label} needs {len(c['text']) / d:.0f} characters per second"})
        if len(wrap(c["text"])) > 2:
            issues.append({"code": "TOO_MANY_LINES", "message": f"{label} wraps to more than two lines"})
        if c["start"] < prev_end - 1e-6:
            issues.append({"code": "OVERLAP", "message": f"{label} overlaps the previous cue"})
        if c["end"] > duration_s + 0.05:
            issues.append({"code": "PAST_END", "message": f"{label} ends after the video"})
        prev_end = c["end"]
    return issues


def _ts_srt(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def _ts_ass(t: float) -> str:
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def write_srt(cues: list[dict[str, Any]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    blocks = [f"{i}\n{_ts_srt(c['start'])} --> {_ts_srt(c['end'])}\n" + "\n".join(wrap(c["text"])) for i, c in enumerate(cues, 1)]
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return path


def write_ass(cues: list[dict[str, Any]], path: Path, size: tuple[int, int], font: str = "Inter") -> Path:
    """Readable styling: bold white text, dark outline and shadow, bottom centre, scaled to the frame height."""
    w, h = size
    fs, outline, margin_v = int(h * 0.058), max(2, int(h * 0.004)), int(h * 0.07)
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {w}\nPlayResY: {h}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},{fs},&H00FFFFFF,&H000000FF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,{outline},{max(1, outline // 2)},2,"
        f"{int(w * 0.06)},{int(w * 0.06)},{margin_v},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    for c in cues:
        text = "\\N".join(wrap(c["text"])).replace("{", "(").replace("}", ")")
        lines.append(f"Dialogue: 0,{_ts_ass(c['start'])},{_ts_ass(c['end'])},Default,,0,0,0,,{text}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(head + "\n".join(lines) + "\n", encoding="utf-8")
    return path


def read_srt(path: Path) -> list[dict[str, Any]]:
    cues = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8").strip()):
        lines = block.splitlines()
        if len(lines) < 3:
            continue
        m = re.match(r"(\d+):(\d+):(\d+),(\d+) --> (\d+):(\d+):(\d+),(\d+)", lines[1])
        if not m:
            continue
        a = [int(x) for x in m.groups()]
        cues.append({"start": a[0] * 3600 + a[1] * 60 + a[2] + a[3] / 1000, "end": a[4] * 3600 + a[5] * 60 + a[6] + a[7] / 1000,
                     "text": " ".join(lines[2:])})
    return cues
