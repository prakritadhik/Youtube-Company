"""Fixtures for the AI brain tests: a fake ``claude`` program, a configured company, and canned model answers."""

from __future__ import annotations

import json
from typing import Any

from app.ai import fit
from app.config import reset_settings_cache
from app.research import ideas
from app.services import settings as settings_svc
from tests.fake_claude import FakeClaude

STRUCTURE = ["intro", "verse", "chorus", "verse 2", "chorus 2", "outro"]
SPEC = {"width": 320, "height": 180, "preset": "ultrafast", "crossfade_s": 0.4}


def connect_brain(db, owner, tmp_path, monkeypatch, *, limit: int = 100, attest: bool = True) -> FakeClaude:
    """Point the company at a fake ``claude`` program, attest the plan terms and give the brain a daily allowance."""
    fake = FakeClaude(tmp_path / "bin")
    monkeypatch.setenv("YTC_CLAUDE_CODE_BIN", str(fake.path))
    reset_settings_cache()
    if attest:
        settings_svc.set_value(db, owner, "provider_attestations", {"claude-code": {"api_access_confirmed": True, "commercial_use_confirmed": True}})
    settings_svc.set_value(db, owner, "budget", {"daily_model_requests": limit, "daily_provider_requests": limit})
    db.commit()
    return fake


def brief_json(**over: Any) -> dict[str, Any]:
    b: dict[str, Any] = {
        "video_objective": "Give a child a birthday song that feels like a morning in a garden.",
        "target_audience": "Parents and children aged 3 to 8", "viewer_benefit": "A gentle song to sing along to at a party.",
        "main_concept": "A garden wakes up as the birthday child arrives, told through bells and soft drums.",
        "tone_and_style": "warm, playful, unhurried", "planned_length_seconds": 40,
        "visual_direction": "bright illustrated hills with paper lanterns", "audio_direction": "bells, pad, soft bass and brushed drums",
        "hook_and_opening": "Bells over a sunrise", "structure": "intro, two verses, two choruses, outro",
        "thumbnail_concept": "A big friendly title over a sunrise hill",
        "distinctive_elements": ["lyrics follow one morning from first light to the party"],
        "originality_requirements": ["no reused lines", "own melody and rhythm"], "success_criteria": ["viewers stay to the second chorus", "no QA failures"],
        "style_tags": ["garden", "sunrise"],
        "musical_plan": {"tempo_bpm": 104, "key": "G major", "mood": "bright", "instrumentation": ["bells", "pad", "bass", "drums"],
                         "vocal_delivery": "instrumental", "song_structure": list(STRUCTURE)},
    }
    mp = over.pop("musical_plan", None)
    b.update(over)
    if mp:
        b["musical_plan"] = {**b["musical_plan"], **mp}
    return b


def lyrics_json(brief: dict[str, Any] | None = None, *, tag: str = "a") -> dict[str, Any]:
    """Lyrics laid out exactly as the plan asks (headers, line counts, line lengths), with words unique to ``tag``."""
    mp = (brief or brief_json())["musical_plan"]
    plan = fit.lyric_plan([s.lower() for s in mp["song_structure"]], float(mp["tempo_bpm"]), float((brief or brief_json())["planned_length_seconds"]))
    words = ["lantern", "meadow", "ribbon", "sparrow", "pebble", "orchard", "willow", "ember", "thistle", "harbour", "velvet", "copper"]
    out, k = [], 0
    for p in plan:
        out.append(p["header"])
        for _ in range(p["lines"]):
            out.append(f"{words[k % len(words)]} {tag}{k} drifts by the {words[(k + 5) % len(words)]}"[: p["max_chars"]])
            k += 1
    return {"text": "\n".join(out), "note": "one morning, line by line"}


def review_json(decision: str = "approve", notes: str = "Specific, original and faithful to the brief.") -> dict[str, Any]:
    return {"decision": decision, "notes": notes, "checks": [{"check": "specificity", "ok": decision == "approve"}]}


def say(fake: FakeClaude, *answers: dict[str, Any] | str, **extra: Any) -> None:
    """Queue the model's answers, in the order the company will ask."""
    fake.set(replies=[a if isinstance(a, str) else json.dumps(a) for a in answers], **extra)


def approved_idea(db, owner, channel: str = "happy-birthday-songs", title: str = "Garden Morning Birthday Song"):
    idea = ideas.propose(db, owner, channel, title=title, concept="A birthday song that follows one morning in a garden, from first light to the party.",
                         audience="Parents and children aged 3 to 8", rationale="Gentle, seasonal and easy to sing.",
                         requires=["instrumental_music", "illustrated_scenes", "video_render", "llm_drafting"])
    ideas.assess(db, owner, idea.id)
    ideas.decide(db, owner, idea.id, approve=True, note="Fits the channel.")
    db.commit()
    return idea


def run_company(runner, rounds: int = 40) -> None:
    """Let the company work until nothing moves: run the queue, then the managers' review and the conductor, and repeat."""
    from app.ai import autopilot

    for _ in range(rounds):
        ran = 0
        while runner.run_once() is not None:
            ran += 1
        out = autopilot.step(runner.worker_id)
        if not (ran or out["reviews"] or out["conducted"] or out["resumed"]):
            return
