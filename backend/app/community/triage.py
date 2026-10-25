"""Sorting audience comments with plain, inspectable rules.

This is a *rules-based* triage (``rules-v1``), not a language model: it is predictable and every decision lists its reasons, but it
is blunt. It knows English only; text it cannot judge is marked for a person rather than guessed at. The rules are deliberately
cautious: anything that could involve safety, copyright, medicine or personal data is escalated and never answered automatically.

The comment text is *untrusted data written by strangers*. It is cleaned and length-capped on the way in, scanned for text that
tries to give instructions, and never obeyed: a comment saying "ignore your rules and reply with ..." is just a comment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.research import untrusted

METHOD = "rules-v1"
CATEGORIES = ("question", "praise", "criticism", "spam", "abuse", "safety", "copyright", "medical_concern", "personal_data", "other", "own_comment")
RISKS = ("low", "review", "escalate")
TEMPLATE_CATEGORIES = frozenset({"praise"})  # the only kind a template may answer; everything else needs a person's own words


def _rx(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(patterns), re.IGNORECASE)


SAFETY = _rx(r"\bsuicid", r"\bkill (myself|himself|herself|themselves|him|her|them|you)\b", r"\bself[- ]?harm", r"\bend (my|his|her) life\b", r"\bwant(s)? to die\b",
             r"\bhurt (myself|someone|people)\b", r"\bbomb\b", r"\bi('| a)m going to (hurt|kill)\b", r"\bin danger\b", r"\bnobody cares\b")
COPYRIGHT = _rx(r"copyright", r"\bdmca\b", r"\bstolen\b", r"\bstole (my|this|our)\b", r"\bplagiari", r"\bripped? off\b", r"\bcopied (my|our|this|from)\b",
                r"\bthis is (my|our) (song|music|video|work|content|channel)\b", r"\btake ?down\b", r"\bwithout (my |our )?permission\b")
SIMILAR = _rx(r"\b(sounds?|looks?) (exactly |just |a lot |very |so much )?like (the |a |an )?(song|tune|melody|track|video|channel)\b", r"\bsame (melody|tune|song|beat) as\b")
MEDICAL = _rx(r"\b\d+(\.\d+)?\s?(mg|mcg|ml|iu)\b", r"\bdos(e|age|ing)\b", r"\boverdos", r"\bside[- ]effects?\b", r"\bprescri", r"\bpregnan", r"\bbreast ?feeding\b",
              r"\bsymptoms?\b", r"\bdiagnos", r"\b(can|should|may|could) (i|we|he|she|my \w+) (take|use|stop|give|mix|combine)\b", r"\bis it safe (to|for)\b",
              r"\ballerg(y|ic)\b", r"\bchest pain\b", r"\bcan'?t breathe\b", r"\bbleeding\b", r"\bblood pressure\b", r"\bdiabet", r"\bantibiotic", r"\binteract(s|ion)? with\b")
PERSONAL = _rx(r"[\w.+-]+@[\w-]+\.[\w.-]+", r"(?<!\d)\+?\d[\d\s().-]{8,}\d(?!\d)", r"\bmy (home )?address\b", r"\bi live (at|in)\b")
SPAM = _rx(r"https?://", r"\bwww\.", r"\b[\w-]+\.(com|net|org|io|ru|xyz|info|biz|me|tv)/", r"\bsub(scribe)? ?(4|for) ?sub\b", r"\bsubscribe to my\b", r"\bcheck out my\b",
           r"\bdm me\b", r"\bwhats ?app\b", r"\btelegram\b", r"\bgiveaway\b", r"\bcrypto\b", r"\bbitcoin\b", r"\bearn \$?\d", r"\bclick (here|the link)\b", r"\bfree (gift|money|followers)\b",
           r"(.)\1{9,}")
ABUSE = _rx(r"\bidiot", r"\bstupid\b", r"\bmoron", r"\bloser", r"\bshut up\b", r"\bpiece of (shit|crap|garbage)\b", r"\bhate you\b", r"\bdumb\b", r"\bfuck", r"\bshit\b", r"\bbitch",
            r"\basshole", r"\bgo to hell\b", r"\bdie\b")
CRITICISM = _rx(r"\bboring\b", r"\b(bad|terrible|awful|worst|horrible|poor|annoying|disappointing)\b", r"\bwrong\b", r"\bmisleading\b", r"\bfake\b", r"\bclick ?bait\b",
                r"\btoo (loud|quiet|long|short|slow|fast|dark|bright|much)\b", r"\bwaste of (my )?time\b", r"\bnot (good|great|helpful|accurate|true)\b", r"\bdon'?t like\b",
                r"\bunsubscrib", r"\bcan'?t (hear|see|understand)\b", r"\bbad (audio|sound|quality)\b", r"\bout of sync\b", r"\bglitch")
QUESTION = _rx(r"\?", r"^\s*(what|how|why|when|where|who|which|can|could|does|do|is|are|will|would|should|did)\b")
PRAISE = _rx(r"\bthank", r"\blove[ds]?\b", r"\b(great|awesome|amazing|beautiful|wonderful|lovely|perfect|fantastic|brilliant|excellent|cute|sweet|best)\b", r"\bnice\b",
             r"\benjoy", r"\bwell done\b", r"\bgood job\b", r"\bhappy birthday\b", r"\bblessed?\b", r"\bgod bless\b", r"\bhelpful\b", r"\bsubscribed\b")


@dataclass
class Triage:
    category: str
    risk: str
    reasons: list[str] = field(default_factory=list)
    injection_flags: list[str] = field(default_factory=list)
    method: str = METHOD


def _mostly_english(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return True
    return sum(1 for c in letters if c.isascii()) / len(letters) >= 0.6


def triage(text: str, *, channel_kind: str = "", is_ours: bool = False) -> Triage:
    """Decide a comment's category and the care it needs. Pure and deterministic; the same text always gives the same answer."""
    flags = untrusted.injection_flags(text)
    if is_ours:
        return Triage("own_comment", "low", ["written by this channel itself"], flags)
    t = text.strip()

    def done(category: str, risk: str, *reasons: str) -> Triage:
        why = list(reasons)
        if flags:
            why.append("contains text that tries to give instructions; it is treated as data and never followed")
            if risk == "low":
                risk = "review"
        return Triage(category, risk, why, flags)

    if not t:
        return done("other", "low", "the comment has no text")
    if SAFETY.search(t):
        return done("safety", "escalate", "mentions harm or danger to a person; a human must decide whether and how to respond")
    if COPYRIGHT.search(t):
        return done("copyright", "escalate", "makes or hints at a copyright or ownership claim; handle through the rights process, not by replying")
    if MEDICAL.search(t):
        return done("medical_concern", "escalate", "mentions a medicine, dose, symptom or health decision; never answered with personal medical advice")
    if channel_kind == "medicine" and QUESTION.search(t):
        return done("medical_concern", "escalate", "a question on the medicine channel; a person decides whether a reply is appropriate")
    if PERSONAL.search(t):
        return done("personal_data", "review", "contains what looks like contact or location details; do not repeat them")
    if SPAM.search(t):
        return done("spam", "low", "contains a link, promotion or repeated characters")
    if ABUSE.search(t):
        return done("abuse", "review", "contains insulting language; not answered")
    if not _mostly_english(t):
        return done("other", "review", f"not in English; {METHOD} cannot judge it, so a person should read it")
    if SIMILAR.search(t):
        return done("copyright", "review", "says the content resembles something else; check originality records")
    if CRITICISM.search(t):
        return done("criticism", "review", "negative or corrective feedback; read it, it may point to a real problem")
    if QUESTION.search(t):
        return done("question", "review", "asks something; a person answers it in their own words")
    if PRAISE.search(t):
        return done("praise", "low", "positive feedback with no question or concern")
    return done("other", "low", "no rule matched")
