"""Text similarity screening. Pure functions, no I/O.

These are *screening aids*: a low score does not prove originality and a high score does not prove copying.
They are tuned to catch the obvious cases the originality rules forbid — verbatim reuse, a few words swapped,
lines reordered — and nothing more. Synonym-level paraphrase and melodic similarity are out of reach here.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_MARKER = re.compile(r"\[(?:S\d+(?:\s*,\s*S?\d+)*|framing)\]", re.IGNORECASE)
_NON_WORD = re.compile(r"[^\w\s']", re.UNICODE)
_SPACE = re.compile(r"\s+")

SHINGLE_N = 4
NEAR_DUP_LINE_RATIO = 0.82
MIN_LINE_WORDS = 3


def strip_markers(text: str) -> str:
    return _MARKER.sub("", text)


def normalize(text: str) -> str:
    t = strip_markers(text).lower().replace("’", "'")
    return _SPACE.sub(" ", _NON_WORD.sub(" ", t)).strip()


def words(text: str) -> list[str]:
    n = normalize(text)
    return n.split() if n else []


def shingles(ws: list[str], n: int = SHINGLE_N) -> set[tuple[str, ...]]:
    if len(ws) < n:
        return {tuple(ws)} if ws else set()
    return {tuple(ws[i : i + n]) for i in range(len(ws) - n + 1)}


def containment(a: set, b: set) -> float:
    """Share of ``a`` that also appears in ``b`` (asymmetric: 'how much of A is copied from B')."""
    return len(a & b) / len(a) if a else 0.0


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def longest_common_run(a: list[str], b: list[str]) -> int:
    """Longest run of consecutive identical words (classic DP; inputs are scripts, so small)."""
    if not a or not b:
        return 0
    best = 0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0] * (len(b) + 1)
        for j, y in enumerate(b, 1):
            if x == y:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


def content_lines(text: str) -> list[str]:
    out = []
    for raw in re.split(r"[\n]+|(?<=[.!?])\s+", strip_markers(text)):
        n = normalize(raw)
        if len(n.split()) >= MIN_LINE_WORDS:
            out.append(n)
    return out


def near_duplicate_lines(a_lines: list[str], b_lines: list[str], threshold: float = NEAR_DUP_LINE_RATIO) -> list[dict]:
    """Lines that are the same or nearly the same (a few words changed), regardless of order."""
    hits = []
    for la in a_lines:
        best, best_lb = 0.0, ""
        for lb in b_lines:
            r = SequenceMatcher(None, la, lb).ratio()
            if r > best:
                best, best_lb = r, lb
        if best >= threshold:
            hits.append({"line": la, "matches": best_lb, "ratio": round(best, 3)})
    return hits


def compare(candidate: str, other: str) -> dict:
    wa, wb = words(candidate), words(other)
    sa, sb = shingles(wa), shingles(wb)
    la, lb = content_lines(candidate), content_lines(other)
    dups = near_duplicate_lines(la, lb)
    return {
        "candidate_words": len(wa),
        "other_words": len(wb),
        "containment_in_other": round(containment(sa, sb), 3),  # how much of the candidate is found in the other
        "containment_of_other": round(containment(sb, sa), 3),
        "jaccard": round(jaccard(sa, sb), 3),
        "longest_common_run_words": longest_common_run(wa, wb),
        "near_duplicate_lines": dups,
        "near_duplicate_line_share": round(len(dups) / len(la), 3) if la else 0.0,
    }


# --- structural fingerprint for songs/stories -----------------------------------------------------------


def structure_signature(brief: dict) -> dict:
    """What makes two productions structurally 'the same shape' (used for formulaic-repetition checks)."""
    mp = brief.get("musical_plan") or {}
    bpm = mp.get("tempo_bpm")
    return {
        "sections": [s.lower() for s in (mp.get("song_structure") or brief.get("story_structure") or [])],
        "tempo_bucket": None if bpm is None else int(round(float(bpm) / 8.0)),
        "key": (mp.get("key") or "").lower() or None,
        "instrumentation": sorted(i.lower() for i in (mp.get("instrumentation") or [])),
        "vocal_delivery": (mp.get("vocal_delivery") or "").lower() or None,
        "style_tags": sorted(t.lower() for t in (brief.get("style_tags") or [])),
    }


def same_shape(a: dict, b: dict) -> bool:
    keys = ("sections", "tempo_bucket", "key", "instrumentation")
    return all(a.get(k) == b.get(k) for k in keys) and bool(a.get("sections") or a.get("instrumentation"))


# --- well-known lines (a short screening list, not a database of protected works) -----------------------------

KNOWN_LINE_NOTES = {
    "good morning to all": "Opening line of the 1893 song that the traditional birthday song descends from.",
    "for he's a jolly good fellow": "Traditional celebratory song.",
    "many happy returns of the day": "Common greeting; harmless alone.",
}


def traditional_birthday_pattern(lyrics: str) -> dict:
    """Detect the four-line traditional refrain. Using it wholesale needs a human rights decision."""
    n = normalize(lyrics)
    to_you = len(re.findall(r"happy birthday to you", n))
    dear = len(re.findall(r"happy birthday dear", n))  # followed by a name
    full_pattern = to_you >= 3 and dear >= 1
    return {"happy_birthday_to_you_lines": to_you, "happy_birthday_dear_lines": dear, "full_traditional_refrain": full_pattern}


def known_lines_found(text: str) -> list[dict]:
    n = normalize(text)
    return [{"line": k, "note": v} for k, v in KNOWN_LINE_NOTES.items() if k in n]
