"""Titles, descriptions, tags, hashtags and chapters: drafting, checking and approving them.

What this module does and does not claim:

* Drafts come from templates over the approved brief and script (``template-v1``). They are starting points for a
  person or a language model, not creative writing.
* Every check is deterministic and says what it looked at. A title is "accurate" only in the sense that its distinctive
  words appear in the video's brief, script or sources; the checks cannot judge meaning.
* Search intent, keyword demand and historical performance are **unavailable** (no search data, no analytics). Nothing
  here is shown to perform better than anything else, and the recommendation text must say so.
* Chapters are offered only from timestamps measured in the finished audio, never estimated ones.
* The platform limits below are taken from YouTube's published help and API documentation as reported to the build
  environment (see docs/PROVIDERS.md); the live API is the final judge and Phase 4 reads back what it actually accepted.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import VideoStage
from app.models.org import Channel
from app.models.production import Asset, CreativeBrief, SourceRecord
from app.models.research import MetadataSet
from app.models.videos import Video
from app.production import scripts
from app.research import ideas, untrusted
from app.security.permissions import Actor, ActorKind, P, PermissionDenied
from app.services import audit, org
from app.timeutil import utcnow

LIMITS = {
    "title_chars": 100, "title_display_chars": 70, "description_bytes": 5000, "tags_chars": 500, "hashtags_max": 15,
    "hashtags_comfortable": 5, "chapter_min_count": 3, "chapter_min_seconds": 10, "chapter_match_tolerance_s": 0.5,
}
LIMITS_BASIS = "YouTube Help / API documentation as reported to the build environment; confirmed against the live API in Phase 4"

SEVERITIES = ("block", "revise", "info")
_GENRE_RAW = {
    "birthday": {"birthday", "bday", "celebration", "party", "music", "kids", "children", "original", "instrumental", "tune", "melody", "wishes"},
    "medicine": {"medicine", "medical", "health", "education", "educational", "explained", "guide", "facts", "know", "safe", "safely", "simply", "work", "works", "use", "used", "body"},
    "story": {"story", "stories", "tale", "ai", "original", "adventure", "short", "episode", "chapter"},
}
GENRE_WORDS = {k: {ideas.stem(w) for w in v} for k, v in _GENRE_RAW.items()}  # compared with stemmed terms
DEFAULT_HASHTAGS = {"birthday": ["#HappyBirthday"], "medicine": ["#HealthEducation"], "story": ["#AIStory"]}
DEFAULT_TAGS = {"birthday": ["happy birthday", "birthday song"], "medicine": ["health education"], "story": ["ai story"]}
DISCLAIMER = "This video is for general education only and is not personal medical advice. Talk to a qualified health professional about your own situation."
STORY_DISCLOSURE = "This story was created with AI assistance."
HYPOTHESIS = ("Hypothesis only: no search or performance data is available (YouTube analytics are not connected), so none of these options "
              "is shown to perform better than another.")

_CLICKBAIT = re.compile(r"you won'?t believe|shocking|gone wrong|this one (weird )?trick|doctors hate|what happens next|blow your mind|can'?t stop (watching|laughing)", re.I)
_URGENCY = re.compile(r"before it'?s (deleted|banned|removed|too late)|last chance|act now|only today|limited time|watch (this )?now before", re.I)
_MED_BLOCK = re.compile(r"guarantee[sd]?|miracle|100\s?%|instant(ly)? (relief|cure|fix|results?)|\bdetox\b|no side effects?|doctors (don'?t|do not) want|secret (remedy|cure)|"
                        r"reverses? (diabetes|cancer|aging)|cures? (any|all|everything)|permanent(ly)? (cure|fix|relief)", re.I)
_MED_REVISE = re.compile(r"\bcures?\b|\bcured\b|\bmiraculous\b|\bwonder (drug|pill)\b", re.I)
_PERFORMANCE = re.compile(r"best[- ]performing|proven (to|winner)|guaranteed (views|growth)|go(es)? viral|will (rank|outperform)|outperforms?|"
                          r"higher (ctr|click)|more clicks|#1 (on|in)|number one (on|in)", re.I)
_VOCAL = re.compile(r"\b(sing|sings|sung|singing|vocals?|vocalist|karaoke|voice acting)\b", re.I)
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_HEX = re.compile(r"^#(?:[0-9a-fA-F]{6})$")


class SeoError(ValueError):
    pass


def finding(code: str, severity: str, field: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "severity": severity, "field": field, "message": message, **extra}


def worst(findings: list[dict[str, Any]]) -> str:
    sev = {f["severity"] for f in findings}
    return "block" if "block" in sev else "revise" if "revise" in sev else "info" if "info" in sev else "none"


def status_for(findings: list[dict[str, Any]]) -> str:
    return "NEEDS_REVISION" if worst(findings) in ("block", "revise") else "CHECKED"


# --- facts about the video ----------------------------------------------------------------------------------------


def _brief(db: Session, video_id: int) -> dict[str, Any]:
    b = db.scalar(select(CreativeBrief).where(CreativeBrief.video_id == video_id).order_by(CreativeBrief.version.desc()))
    return dict(b.content) if b else {}


def corpus(db: Session, video: Video) -> str:
    """Everything the video actually contains or cites: brief, script/lyrics and sources."""
    brief = _brief(db, video.id)
    parts: list[str] = []  # the working title is deliberately left out: a title cannot vouch for itself
    for k in ("video_objective", "target_audience", "viewer_benefit", "main_concept", "tone_and_style", "visual_direction", "audio_direction",
              "hook_and_opening", "thumbnail_concept", "premise", "source_requirements"):
        if brief.get(k):
            parts.append(str(brief[k]))
    for k in ("style_tags", "distinctive_elements"):
        parts.extend(str(x) for x in brief.get(k) or [])
    for kind in ("lyrics", "script", "narration"):
        sv = scripts.latest(db, video.id, kind)
        if sv:
            parts.append(scripts.spoken_text(sv.text))
    for s in db.scalars(select(SourceRecord).where(SourceRecord.video_id == video.id)):
        parts.extend((s.title, s.supports))
    return " ".join(parts)


def _terms(text: str) -> set[str]:
    return ideas.topic_terms(_CAMEL.sub(" ", text), limit=100_000)


def _kind(db: Session, video: Video) -> str:
    return db.get(Channel, video.channel_id).kind  # type: ignore[union-attr]


def _other_titles(db: Session, video: Video) -> list[str]:
    out = []
    for v in db.scalars(select(Video).where(Video.channel_id == video.channel_id, Video.id != video.id, Video.stage != VideoStage.REJECTED.value)):
        if (v.metadata_json or {}).get("demo"):
            continue
        if v.title or v.working_title:
            out.append(v.title or v.working_title)
    return out


# --- chapters from measured timestamps ----------------------------------------------------------------------------


def fmt_ts(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def boundaries(db: Session, video: Video) -> dict[str, Any]:
    """Real section boundaries of the finished audio, or an honest statement that none are available."""
    m = db.scalar(select(Asset).where(Asset.video_id == video.id, Asset.kind == "music").order_by(Asset.id.desc()))
    tl = (m.params or {}).get("timeline") if m else None
    if not tl:
        return {"kind": "unavailable", "sections": [], "duration_s": None,
                "reason": "no measured timeline exists (narrated videos have only estimated timing)"}
    return {"kind": "aligned", "duration_s": float((m.params or {}).get("duration_s") or tl[-1]["end_s"]),
            "sections": [{"name": str(s["section"]).title(), "start_s": float(s["start_s"])} for s in tl]}


def chapters_from(bounds: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Chapters that satisfy YouTube's rules (start at 0:00, at least three, each at least 10 s), or ``([], reason)``.

    A section too short to stand alone is folded into the chapter before it and named with it ("Intro & Verse 1").
    """
    if bounds["kind"] != "aligned":
        return [], bounds.get("reason", "no measured timestamps")
    mn = LIMITS["chapter_min_seconds"]
    chosen: list[dict[str, Any]] = []
    names: list[list[str]] = []
    for s in bounds["sections"]:
        if not chosen or s["start_s"] - chosen[-1]["start_s"] >= mn:
            chosen.append({"start_s": round(s["start_s"], 2), "title": s["name"], "source": "measured section start"})
            names.append([s["name"]])
        else:
            names[-1].append(s["name"])
    chosen[0]["start_s"] = 0.0
    while len(chosen) > 1 and bounds["duration_s"] - chosen[-1]["start_s"] < mn:
        chosen.pop()
        names.pop()
    for c, n in zip(chosen, names, strict=True):
        c["title"] = " & ".join(n[:2])
    if len(chosen) < LIMITS["chapter_min_count"]:
        return [], f"the video is too short for {LIMITS['chapter_min_count']} chapters of {mn}+ seconds"
    return chosen, ""


def chapters_text(chapters: list[dict[str, Any]]) -> str:
    return "\n".join(f"{fmt_ts(c['start_s'])} {c['title']}" for c in chapters)


def check_chapters(chapters: list[dict[str, Any]], bounds: dict[str, Any]) -> list[dict[str, Any]]:
    if not chapters:
        return [finding("CHAPTERS_NONE", "info", "chapters", "No chapters. They are optional.")]
    out: list[dict[str, Any]] = []
    if bounds["kind"] != "aligned":
        return [finding("CHAPTERS_TIMING_ESTIMATED", "block", "chapters",
                        "Chapters need measured timestamps. This video has no measured timeline, so chapter times would be guesses.")]
    starts = [float(c["start_s"]) for c in chapters]
    if abs(starts[0]) > 1e-6:
        out.append(finding("CHAPTERS_START", "block", "chapters", "The first chapter must start at 0:00."))
    if len(chapters) < LIMITS["chapter_min_count"]:
        out.append(finding("CHAPTERS_TOO_FEW", "block", "chapters", f"YouTube needs at least {LIMITS['chapter_min_count']} chapters."))
    ends = starts[1:] + [bounds["duration_s"]]
    for c, s, e in zip(chapters, starts, ends, strict=True):
        if e - s < LIMITS["chapter_min_seconds"] - 1e-6:
            out.append(finding("CHAPTERS_TOO_SHORT", "block", "chapters", f"Chapter '{c['title']}' is only {e - s:.1f}s; the minimum is {LIMITS['chapter_min_seconds']}s."))
        if s > 0 and not any(abs(s - b["start_s"]) <= LIMITS["chapter_match_tolerance_s"] for b in bounds["sections"]):
            out.append(finding("CHAPTERS_NOT_MEASURED", "block", "chapters",
                               f"Chapter '{c['title']}' at {fmt_ts(s)} does not match a measured section start."))
    if starts != sorted(starts) or len(set(starts)) != len(starts):
        out.append(finding("CHAPTERS_ORDER", "block", "chapters", "Chapter times must be strictly increasing."))
    return out


# --- title assessment ---------------------------------------------------------------------------------------------


def _repeated(text: str, *, min_count: int, min_share: float = 0.0) -> list[str]:
    words = [ideas.stem(w) for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in ideas._STOP and len(w) > 2]
    if not words:
        return []
    out = []
    for w in set(words):
        n = words.count(w)
        if n >= min_count and n / len(words) >= min_share:
            out.append(w)
    return sorted(out)


def assess_title(db: Session, video: Video, title: str) -> dict[str, Any]:
    kind = _kind(db, video)
    f: list[dict[str, Any]] = []
    t = untrusted.clean(title, 400)[0]
    if not t:
        return {"title": t, "findings": [finding("TITLE_EMPTY", "block", "title", "The title is empty.")], "criteria": {}}
    if len(t) > LIMITS["title_chars"]:
        f.append(finding("TITLE_TOO_LONG", "block", "title", f"{len(t)} characters; YouTube allows {LIMITS['title_chars']}."))
    elif len(t) > LIMITS["title_display_chars"]:
        f.append(finding("TITLE_LONG_FOR_DISPLAY", "info", "title", f"{len(t)} characters; the end may be cut off where titles are shortened."))
    if "<" in t or ">" in t:
        f.append(finding("TITLE_ANGLE_BRACKETS", "block", "title", "YouTube rejects titles containing < or >."))
    letters = [c for c in t if c.isalpha()]
    if len(letters) >= 8 and sum(c.isupper() for c in letters) / len(letters) > 0.6:
        f.append(finding("TITLE_SHOUTING", "revise", "title", "Mostly capital letters reads as shouting; use normal capitalisation."))
    if re.search(r"[!?]{2,}", t) or t.count("!") > 1:
        f.append(finding("TITLE_PUNCTUATION", "revise", "title", "Repeated exclamation or question marks."))
    if _URGENCY.search(t):
        f.append(finding("TITLE_FALSE_URGENCY", "revise", "title", "Urgency wording the video cannot back up."))
    if _CLICKBAIT.search(t):
        f.append(finding("TITLE_CLICKBAIT", "revise", "title", "Clickbait phrasing; say plainly what the video contains."))
    if kind == "medicine":
        if _MED_BLOCK.search(t):
            f.append(finding("TITLE_MEDICAL_OVERCLAIM", "block", "title", "A medical claim that promises results or certainty."))
        elif _MED_REVISE.search(t):
            f.append(finding("TITLE_MEDICAL_WORDING", "revise", "title", "Wording such as 'cure' or 'heals' can read as a claim; use careful, descriptive wording."))
    rep = _repeated(t, min_count=3)
    if rep:
        f.append(finding("TITLE_REPEATED_WORDS", "revise", "title", f"The same word repeated three times or more: {', '.join(rep)}."))
    brief = _brief(db, video.id)
    delivery = (brief.get("musical_plan") or {}).get("vocal_delivery")
    if _VOCAL.search(t) and delivery and delivery != "sung":
        f.append(finding("TITLE_PROMISES_VOCALS", "block", "title", "The title promises singing or vocals but the plan is not sung."))
    elif kind == "birthday" and delivery and delivery != "sung" and re.search(r"\bsongs?\b", t, re.I):
        f.append(finding("TITLE_SAYS_SONG", "revise", "title",
                         "The title says 'song' but the audio has no singing; viewers expect vocals from a song. Say 'music' or 'instrumental', or add real vocals."))

    text = corpus(db, video)
    distinctive = _terms(t) - GENRE_WORDS.get(kind, set())
    has_content = len(text.split()) > 12 and (bool(brief) or scripts.latest(db, video.id, "lyrics") or scripts.latest(db, video.id, "script"))
    support: dict[str, Any]
    if not has_content:
        f.append(finding("ACCURACY_UNASSESSED", "revise", "title", "There is no brief or script to compare the title against, so accuracy cannot be checked."))
        support = {"state": "unassessed", "detail": "no brief or script"}
    else:
        missing = sorted(distinctive - _terms(text))
        ratio = len(missing) / len(distinctive) if distinctive else 0.0
        if distinctive and ratio > 0.4:
            f.append(finding("TITLE_NOT_SUPPORTED", "revise", "title", f"Words in the title that appear nowhere in the brief, script or sources: {', '.join(missing)}.",
                             terms=missing))
        support = {"state": "ok" if ratio <= 0.4 else "warn", "unsupported_terms": missing,
                   "detail": f"{len(distinctive) - len(missing)} of {len(distinctive)} distinctive words appear in the video's own material"}
    sims = [(o, ideas.overlap(t, "", o, "")) for o in _other_titles(db, video)]
    sims.sort(key=lambda x: x[1]["title_similarity"], reverse=True)
    near = [o for o, s in sims if s["title_similarity"] >= ideas.DUPLICATE_TITLE_RATIO]
    if near:
        f.append(finding("TITLE_NEAR_DUPLICATE", "revise", "title", f"Almost identical to an existing title on this channel: '{near[0]}'.", other=near[0]))
    criteria = {
        "audience_relevance": {"state": "ok" if _terms(t) & _terms(str(brief.get("target_audience", "")) + " " + str(brief.get("main_concept", ""))) else "warn",
                               "detail": "shares words with the brief's audience or concept" if brief else "no brief to compare with"},
        "clarity": {"state": "ok" if 15 <= len(t) <= LIMITS["title_display_chars"] and not any(x["code"] in ("TITLE_SHOUTING", "TITLE_PUNCTUATION") for x in f) else "warn",
                    "detail": f"{len(t)} characters"},
        "accuracy_and_consistency": support,
        "search_intent": {"state": "unavailable", "detail": "no search data is recorded; only keyword presence in the brief can be checked"},
        "distinctiveness": {"state": "warn" if near else "ok", "detail": f"compared with {len(sims)} existing title(s) on this channel"},
        "readability": {"state": "ok" if len(t) <= LIMITS["title_display_chars"] else "warn", "detail": f"display limit about {LIMITS['title_display_chars']} characters"},
        "historical_performance": {"state": "unavailable", "detail": "YouTube analytics are not connected"},
    }
    return {"title": t, "findings": f, "criteria": criteria}


# --- whole-set checks ---------------------------------------------------------------------------------------------


def _hashtag_ok(h: str) -> bool:
    return bool(re.fullmatch(r"#[A-Za-z0-9_]{2,40}", h))


def check_content(db: Session, video: Video, *, chosen_title: str, description: str, tags: list[str], hashtags: list[str],
                  chapters: list[dict[str, Any]], recommendation: str, pinned_comment: str = "") -> list[dict[str, Any]]:
    kind = _kind(db, video)
    f = list(assess_title(db, video, chosen_title)["findings"])
    d = description or ""
    if not d.strip():
        f.append(finding("DESCRIPTION_EMPTY", "block", "description", "The description is empty."))
    if len(d.encode("utf-8")) > LIMITS["description_bytes"]:
        f.append(finding("DESCRIPTION_TOO_LONG", "block", "description", f"{len(d.encode('utf-8'))} bytes; YouTube allows {LIMITS['description_bytes']}."))
    if "<" in d or ">" in d:
        f.append(finding("DESCRIPTION_ANGLE_BRACKETS", "block", "description", "YouTube rejects descriptions containing < or >."))
    words = re.findall(r"[a-z0-9']+", d.lower())
    rep = _repeated(d, min_count=5, min_share=0.1) if len(words) >= 40 else []
    if rep:
        f.append(finding("DESCRIPTION_KEYWORD_STUFFING", "revise", "description", f"One word makes up a large share of the description: {', '.join(rep)}."))
    for fld, txt in (("description", d), ("pinned_comment", pinned_comment)):
        if _PERFORMANCE.search(txt) or _URGENCY.search(txt):
            f.append(finding("UNSUPPORTED_CLAIM", "revise", fld, "A performance or urgency claim the company cannot support."))
    if kind == "medicine":
        if _MED_BLOCK.search(d + " " + " ".join(tags)):
            f.append(finding("MEDICAL_OVERCLAIM", "block", "description", "A medical claim that promises results or certainty."))
        if DISCLAIMER.split(".")[0].lower() not in d.lower():
            f.append(finding("MEDICAL_DISCLAIMER_MISSING", "block", "description", "A medicine video's description must say it is general education, not personal medical advice."))
        srcs = list(db.scalars(select(SourceRecord).where(SourceRecord.video_id == video.id)))
        listed = [s for s in srcs if s.url in d]
        if srcs and not listed:
            f.append(finding("SOURCES_NOT_LISTED", "block", "description", "The description does not list any of the video's sources."))
        elif len(listed) < len(srcs):
            f.append(finding("SOURCES_PARTLY_LISTED", "revise", "description", f"{len(srcs) - len(listed)} of {len(srcs)} sources are not listed."))
        if not srcs:
            f.append(finding("NO_SOURCES", "block", "description", "A medicine video needs recorded sources before its description can be approved."))
    if kind == "story" and STORY_DISCLOSURE.lower() not in d.lower():
        f.append(finding("AI_DISCLOSURE_MISSING", "revise", "description", "Say in the description that the story was created with AI assistance."))
    elif kind != "story" and db.scalar(select(Asset.id).where(Asset.video_id == video.id, Asset.kind == "narration", Asset.provider.notin_(("local-procedural", "local-render", "bundled")))) \
            and "ai-generated" not in d.lower() and "ai generated" not in d.lower():
        f.append(finding("AI_DISCLOSURE_MISSING", "revise", "description", "The narration is AI-generated; say so in the description."))

    text_terms = _terms(corpus(db, video) + " " + chosen_title + " " + d) | GENRE_WORDS.get(kind, set())
    if tags:
        norm = [t.strip().lower() for t in tags]
        if len(set(norm)) != len(norm):
            f.append(finding("TAGS_DUPLICATE", "revise", "tags", "Duplicate tags."))
        total = sum(len(t) + (2 if " " in t else 0) for t in tags) + max(0, len(tags) - 1)
        if total > LIMITS["tags_chars"]:
            f.append(finding("TAGS_TOO_LONG", "block", "tags", f"Tags total {total} characters; YouTube allows {LIMITS['tags_chars']}."))
        if any("<" in t or ">" in t for t in tags):
            f.append(finding("TAGS_ANGLE_BRACKETS", "block", "tags", "Tags cannot contain < or >."))
        if len(tags) >= 5:
            shared = {w for w in set().union(*(_terms(t) for t in tags)) if sum(w in _terms(t) for t in tags) / len(tags) > 0.6}
            if shared:
                f.append(finding("TAGS_REPETITIVE", "revise", "tags", f"Most tags repeat the same word ({', '.join(sorted(shared))}); that reads as keyword stuffing."))
        unsupported = [t for t in tags if _terms(t) and not _terms(t) <= text_terms]
        if unsupported:
            f.append(finding("TAGS_UNSUPPORTED", "revise", "tags", f"Tags the video's content does not support: {', '.join(unsupported)}.", tags=unsupported))
    if hashtags:
        bad = [h for h in hashtags if not _hashtag_ok(h)]
        if bad:
            f.append(finding("HASHTAG_FORMAT", "block", "hashtags", f"Invalid hashtag(s): {', '.join(bad)}. Use # then letters, digits or underscores, no spaces."))
        if len(hashtags) > LIMITS["hashtags_max"]:
            f.append(finding("HASHTAGS_TOO_MANY", "block", "hashtags", f"{len(hashtags)} hashtags; YouTube ignores all of them above {LIMITS['hashtags_max']}."))
        elif len(hashtags) > LIMITS["hashtags_comfortable"]:
            f.append(finding("HASHTAGS_MANY", "revise", "hashtags", f"{len(hashtags)} hashtags; a few relevant ones read better than many."))
        irrelevant = [h for h in hashtags if _hashtag_ok(h) and not _terms(h[1:]) <= text_terms]
        if irrelevant:
            f.append(finding("HASHTAGS_IRRELEVANT", "revise", "hashtags", f"Hashtags unrelated to this video: {', '.join(irrelevant)}.", hashtags=irrelevant))
    f.extend(check_chapters(chapters, boundaries(db, video)))
    if chapters and chapters_text(chapters) not in d:
        f.append(finding("CHAPTERS_NOT_IN_DESCRIPTION", "revise", "description", "Chapters only work when their timestamps are in the description."))
    if _PERFORMANCE.search(recommendation or ""):
        f.append(finding("RECOMMENDATION_CLAIMS_PERFORMANCE", "block", "recommendation", "A recommendation cannot claim an option performs better without data."))
    if "hypothesis" not in (recommendation or "").lower():
        f.append(finding("RECOMMENDATION_NOT_LABELLED", "revise", "recommendation", "Label the recommendation as a hypothesis; no performance data exists."))
    return f


# --- drafting -----------------------------------------------------------------------------------------------------


def _sentence(text: str) -> str:
    t = untrusted.clean(text, 400)[0].strip().rstrip(".!?")
    return (t[0].upper() + t[1:] + ".") if t else ""


def title_drafts(db: Session, video: Video) -> list[str]:
    kind, base = _kind(db, video), (video.working_title or "").strip()
    brief = _brief(db, video.id)
    if not base:
        return []
    drafts = [base]
    if kind == "birthday":
        sung = (brief.get("musical_plan") or {}).get("vocal_delivery") == "sung"
        stem = base if sung else re.sub(r"\bsongs?\b", "Music", base, flags=re.I)  # an instrumental is not sold as a song
        noun = "Song" if sung else "Instrumental"
        drafts += [stem, f"{stem} | Original Birthday {noun}"]
        tone = str(brief.get("tone_and_style", "")).replace(",", " ").split()
        if tone:
            drafts += [f"{stem} – A {tone[0].capitalize()} Original Birthday {noun}"]
    elif kind == "medicine":
        drafts += [f"{base}: What to Know", f"{base} Explained Simply"]
    elif kind == "story":
        drafts += [f"{base} | An Original AI Story", f"{base} – A Short Original Story"]
    out, seen = [], set()
    for d in drafts:
        if d.lower() not in seen and len(d) <= LIMITS["title_chars"]:
            seen.add(d.lower())
            out.append(d)
    return out


def description_draft(db: Session, video: Video, chapters: list[dict[str, Any]]) -> str:
    kind, brief = _kind(db, video), _brief(db, video.id)
    lines: list[str] = []
    opening = " ".join(x for x in (_sentence(brief.get("main_concept", "")), _sentence(brief.get("viewer_benefit", ""))) if x)
    lines.append(opening or _sentence(video.working_title))
    if kind == "story" and brief.get("premise"):
        lines.append(_sentence(brief["premise"]))
    if chapters:
        lines += ["", "Chapters", chapters_text(chapters)]
    if kind == "medicine":
        srcs = list(db.scalars(select(SourceRecord).where(SourceRecord.video_id == video.id).order_by(SourceRecord.id)))
        if srcs:
            lines += ["", "Sources"] + [f"- {s.title or s.publisher or 'Source'}: {s.url}" for s in srcs]
        lines += ["", DISCLAIMER]
    if kind == "story":
        lines += ["", STORY_DISCLOSURE]
    credits = [a.attribution for a in db.scalars(select(Asset).where(Asset.video_id == video.id)) if a.attribution and a.license_type not in ("ofl-1.1", "owner-generated")]
    if credits:
        lines += ["", "Credits"] + [f"- {c}" for c in sorted(set(credits))]
    cta = {"birthday": "If you enjoyed this, you can subscribe for more original birthday music.",
           "medicine": "You can subscribe for more careful, source-based health explanations.",
           "story": "You can subscribe for more original stories."}[kind]
    lines += ["", cta]
    return "\n".join(lines).strip()


def tag_drafts(db: Session, video: Video) -> list[str]:
    kind, brief = _kind(db, video), _brief(db, video.id)
    tags = list(DEFAULT_TAGS[kind]) + [str(t) for t in brief.get("style_tags") or []][:4]
    out, seen = [], set()
    for t in tags:
        t = untrusted.clean(t, 60)[0].lower()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def hashtag_drafts(db: Session, video: Video) -> list[str]:
    kind, brief = _kind(db, video), _brief(db, video.id)
    tags = list(DEFAULT_HASHTAGS[kind])
    for t in brief.get("style_tags") or []:
        word = re.sub(r"[^A-Za-z0-9]", "", str(t).title())
        if len(word) >= 3:
            tags.append("#" + word)
    return tags[: LIMITS["hashtags_comfortable"]]


# --- persistence --------------------------------------------------------------------------------------------------


def _can_author(actor: Actor) -> bool:
    return actor.is_owner or actor.can(P.TASK_WORK)


def get(db: Session, set_id: int) -> MetadataSet:
    ms = db.get(MetadataSet, set_id)
    if ms is None:
        raise SeoError("metadata set not found")
    return ms


def latest(db: Session, video_id: int) -> MetadataSet | None:
    return db.scalar(select(MetadataSet).where(MetadataSet.video_id == video_id).order_by(MetadataSet.version.desc()))


def approved(db: Session, video_id: int) -> MetadataSet | None:
    return db.scalar(select(MetadataSet).where(MetadataSet.video_id == video_id, MetadataSet.status == "APPROVED").order_by(MetadataSet.version.desc()))


def create_set(db: Session, actor: Actor, video_id: int, *, chosen_title: str, description: str, tags: list[str] | None = None,
               hashtags: list[str] | None = None, chapters: list[dict[str, Any]] | None = None, playlists: list[str] | None = None,
               pinned_comment: str = "", call_to_action: str = "", recommendation: str = HYPOTHESIS,
               title_candidates: list[str] | None = None, generated_by: str = "manual", task_id: int | None = None) -> MetadataSet:
    if not _can_author(actor):
        raise PermissionDenied(actor, "task:work")
    video = db.get(Video, video_id)
    if video is None:
        raise SeoError("video not found")
    if video.stage in (VideoStage.SCHEDULED.value, VideoStage.PUBLISHED.value):
        raise SeoError("metadata cannot change after the video is scheduled or published")
    version = (latest(db, video_id).version + 1) if latest(db, video_id) else 1
    cands = []
    for c in dict.fromkeys([chosen_title, *(title_candidates or [])]):
        a = assess_title(db, video, c)
        cands.append({"title": c, "source": generated_by,
                      "findings": a["findings"], "criteria": a["criteria"], "label": "hypothesis"})
    ms = MetadataSet(
        video_id=video_id, version=version, status="DRAFT", title_candidates=cands, chosen_title=untrusted.clean(chosen_title, 200)[0],
        description=description.strip(), tags=[untrusted.clean(t, 100)[0] for t in tags or []], hashtags=[h.strip() for h in hashtags or []],
        chapters=chapters or [], playlists=[untrusted.clean(p, 120)[0] for p in playlists or []], pinned_comment=pinned_comment.strip(),
        call_to_action=call_to_action.strip(), recommendation=recommendation.strip(), generated_by=generated_by, task_id=task_id, created_by=actor.label,
    )
    db.add(ms)
    db.flush()
    for old in db.scalars(select(MetadataSet).where(MetadataSet.video_id == video_id, MetadataSet.id != ms.id,
                                                    MetadataSet.status.in_(("DRAFT", "CHECKED", "NEEDS_REVISION")))):
        old.status = "SUPERSEDED"
    audit.record(db, actor, "metadata.created", "metadata_set", ms.id, {"video": video_id, "version": version, "by": generated_by})
    return check(db, actor, ms.id)


def generate(db: Session, actor: Actor, video_id: int, *, task_id: int | None = None) -> MetadataSet:
    """A template draft from the approved brief and script, with chapters only if the audio timeline is measured."""
    video = db.get(Video, video_id)
    if video is None:
        raise SeoError("video not found")
    drafts = title_drafts(db, video)
    if not drafts:
        raise SeoError("the video needs a working title before metadata can be drafted")
    chapters, _why = chapters_from(boundaries(db, video))
    ranked = sorted(drafts, key=lambda t: (len([x for x in assess_title(db, video, t)["findings"] if x["severity"] in ("block", "revise")]), drafts.index(t)))
    return create_set(db, actor, video_id, chosen_title=ranked[0], title_candidates=drafts, description=description_draft(db, video, chapters),
                      tags=tag_drafts(db, video), hashtags=hashtag_drafts(db, video), chapters=chapters, generated_by="template-v1", task_id=task_id)


def check(db: Session, actor: Actor, set_id: int) -> MetadataSet:
    ms = get(db, set_id)
    if ms.status in ("APPROVED", "SUPERSEDED"):
        raise SeoError(f"a {ms.status.lower()} set is not re-checked; create a new version")
    video = db.get(Video, ms.video_id)
    ms.findings = check_content(db, video, chosen_title=ms.chosen_title, description=ms.description, tags=ms.tags, hashtags=ms.hashtags,  # type: ignore[arg-type]
                                chapters=ms.chapters, recommendation=ms.recommendation, pinned_comment=ms.pinned_comment)
    ms.status, ms.checked_at = status_for(ms.findings), utcnow()
    audit.record(db, actor, "metadata.checked", "metadata_set", ms.id, {"status": ms.status, "findings": len(ms.findings)})
    return ms


def _can_approve(db: Session, actor: Actor) -> bool:
    if actor.is_owner:
        return True
    if actor.kind is not ActorKind.AGENT or not actor.can(P.TASK_REVIEW):
        return False
    dept = getattr(org.get_agent(db, actor.id).department, "slug", None)
    return actor.role in ("coo", "ceo") or dept in ("research-growth", "publishing")


def approve(db: Session, actor: Actor, set_id: int, *, note: str, waive: dict[str, str] | None = None) -> MetadataSet:
    """Approve a checked set and apply it to the video. Only 'revise'-level findings can be waived, each with a reason."""
    if not _can_approve(db, actor):
        raise PermissionDenied(actor, "metadata:approve (research or publishing manager, COO, CEO or owner)")
    ms = get(db, set_id)
    if ms.status not in ("CHECKED", "NEEDS_REVISION"):
        raise SeoError(f"a {ms.status.lower()} set cannot be approved")
    if ms.created_by == actor.label and not actor.is_owner:
        raise SeoError("the author of a metadata set cannot approve it")
    if not note.strip():
        raise SeoError("give a reason; it is kept with the approval")
    ms = check(db, actor, set_id)  # approve what is true now, not what was true when it was last checked
    blocking = [x for x in ms.findings if x["severity"] == "block"]
    if blocking:
        raise SeoError("blocking findings cannot be waived: " + "; ".join(x["message"] for x in blocking))
    waive = {k: v.strip() for k, v in (waive or {}).items() if v and v.strip()}
    open_ = [x for x in ms.findings if x["severity"] == "revise" and x["code"] not in waive]
    if open_:
        raise SeoError("unresolved findings: " + "; ".join(f"{x['code']} ({x['message']})" for x in open_))
    video = db.get(Video, ms.video_id)
    if video.stage in (VideoStage.SCHEDULED.value, VideoStage.PUBLISHED.value):  # type: ignore[union-attr]
        raise SeoError("metadata cannot change after the video is scheduled or published")
    for prev in db.scalars(select(MetadataSet).where(MetadataSet.video_id == ms.video_id, MetadataSet.status == "APPROVED")):
        prev.status = "SUPERSEDED"
    ms.status, ms.approved_by, ms.approved_at = "APPROVED", actor.label, utcnow()
    ms.findings = [{**x, "waived": waive[x["code"]]} if x["code"] in waive else x for x in ms.findings]
    video.title = ms.chosen_title  # type: ignore[union-attr]
    video.metadata_json = {**(video.metadata_json or {}), "description": ms.description, "tags": ms.tags, "hashtags": ms.hashtags, "chapters": ms.chapters,
                           "playlists": ms.playlists, "pinned_comment": ms.pinned_comment, "metadata_set_id": ms.id, "metadata_version": ms.version}
    audit.record(db, actor, "metadata.approved", "metadata_set", ms.id, {"video": ms.video_id, "version": ms.version, "note": note.strip(), "waived": waive})
    return ms


def matches_video(ms: MetadataSet, video: Video) -> bool:
    """True when the video still carries exactly what was approved (nobody edited it afterwards)."""
    md = video.metadata_json or {}
    return video.title == ms.chosen_title and md.get("description") == ms.description and md.get("metadata_set_id") == ms.id


def serialize(ms: MetadataSet, *, detail: bool = True) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": ms.id, "video_id": ms.video_id, "version": ms.version, "status": ms.status, "chosen_title": ms.chosen_title, "generated_by": ms.generated_by,
        "created_by": ms.created_by, "approved_by": ms.approved_by, "approved_at": ms.approved_at.isoformat() if ms.approved_at else None,
        "checked_at": ms.checked_at.isoformat() if ms.checked_at else None, "worst_finding": worst(ms.findings or []),
        "finding_counts": {s: sum(1 for x in ms.findings or [] if x["severity"] == s) for s in SEVERITIES},
    }
    if detail:
        d.update({"title_candidates": ms.title_candidates, "description": ms.description, "tags": ms.tags, "hashtags": ms.hashtags, "chapters": ms.chapters,
                  "playlists": ms.playlists, "pinned_comment": ms.pinned_comment, "call_to_action": ms.call_to_action, "recommendation": ms.recommendation,
                  "findings": ms.findings, "limits": LIMITS, "limits_basis": LIMITS_BASIS})
    return d
