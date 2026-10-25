"""Controlled experiments on one channel's own videos.

What an experiment is here: a *registered plan* (hypothesis, the change, one primary metric, how long each video is observed, the confounders worth
naming), videos assigned to two arms by a seeded random draw **before they are published**, outcome figures read from retrieved metrics, and an analysis that
says what the numbers can and cannot support. Then a human decision: adopt, revise, repeat or reject.

Rules enforced here, each with a test:

* assignment is random and seeded, made before publication, recorded with its draw, and never changed;
* a video is in at most one running experiment, so two changes never share a video;
* below the planned number of finished videos per arm **no p-value is computed**, so nobody can act on an early look;
* an experiment that cannot reach the threshold with its sample size is called inconclusive before the numbers are read;
* videos that never produced complete figures are *counted and named* (attrition), not silently dropped;
* adoption needs a supported improvement and the owner; the confidence earned is capped at MEDIUM (one experiment is not proof);
* the experiment lives on the company intelligence board as an entry, so it is verified, acknowledged by workers and expires like any other lesson.
"""

from __future__ import annotations

import random
import secrets
from datetime import date
from statistics import mean, median
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import BoardCategory, BoardStatus, Confidence, EvidenceKind, RiskClass, VideoStage
from app.improvement import measure, stats
from app.models.improvement import Experiment, ExperimentVideo
from app.models.org import Channel
from app.models.videos import Video
from app.security.permissions import Actor, P, PermissionDenied
from app.services import audit, board, org
from app.storage import write_json_artifact
from app.timeutil import iso, utcnow

ARMS = ("control", "treatment")
DECISIONS = ("adopt", "revise", "repeat", "reject")
MIN_GROUP, MAX_GROUP = 5, 50
OPEN = ("DESIGN", "RUNNING")
DEFAULT_CONFOUNDERS = [
    "the day and time of publication",
    "topic, season and holidays",
    "YouTube's own decision to recommend a video, which nobody here controls",
    "how many of the viewers are new to the channel",
    "the order videos are published in (an earlier video can change the audience of a later one)",
]
CONFIDENCE_NOTE = "One experiment is a single result. Confidence is capped at medium until the same result is seen again."
LIMITATIONS = [
    "The unit is the video, so the sample is the number of videos, not the number of views.",
    "Random assignment supports a causal reading only if both arms were made and published alike apart from the change.",
    CONFIDENCE_NOTE,
    "Only the registered primary metric is tested. Looking at other figures afterwards is exploration, not a test.",
]


class ExperimentError(ValueError):
    pass


def _get(db: Session, exp_id: int) -> Experiment:
    e = db.get(Experiment, exp_id)
    if e is None:
        raise ExperimentError("experiment not found")
    return e


def get(db: Session, exp_id: int) -> Experiment:
    return _get(db, exp_id)


def _authority(actor: Actor) -> None:
    """Starting, deciding and cancelling belong to the owner (or the CEO)."""
    if not (actor.is_owner or actor.can(P.BOARD_VERIFY)):
        raise PermissionDenied(actor, P.BOARD_VERIFY)


def describe_metric(spec: dict[str, Any]) -> str:
    agg = {"sum": "total", "mean": "daily average", "latest": "reading"}[spec["aggregate"]]
    return (f"{agg} of {spec['metric']} ({spec['source'].replace('_', ' ')}) over the first {spec['window_days']} days after publication; "
            f"{'higher' if spec['higher_is_better'] else 'lower'} is better")


# --- designing ----------------------------------------------------------------------------------------------------------------


def create(db: Session, actor: Actor, *, channel_slug: str, title: str, hypothesis: str, change: str, metric: dict[str, Any], min_per_group: int = 5,
           confounders: list[str] | None = None, parent_id: int | None = None) -> Experiment:
    actor.require(P.BOARD_PROPOSE)
    for name, val in (("title", title), ("hypothesis", hypothesis), ("change", change)):
        if not val or not val.strip():
            raise ExperimentError(f"an experiment needs a {name}")
    if isinstance(min_per_group, bool) or not isinstance(min_per_group, int) or not MIN_GROUP <= min_per_group <= MAX_GROUP:
        raise ExperimentError(f"at least {MIN_GROUP} videos per arm are needed for the result to be able to mean anything (and no more than {MAX_GROUP} is planned for)")
    try:
        spec = measure.check_spec(metric)
    except measure.MeasureError as e:
        raise ExperimentError(str(e)) from e
    ch = db.scalar(select(Channel).where(Channel.slug == channel_slug))
    if ch is None:
        raise ExperimentError("unknown channel")
    extra = [c.strip() for c in (confounders or []) if c and c.strip()]
    exp = Experiment(public_id=f"TMP-{secrets.token_hex(6)}", channel_id=ch.id, title=title.strip(), hypothesis=hypothesis.strip(), change=change.strip(), metric=spec,
                     min_per_group=min_per_group, confounders=[*DEFAULT_CONFOUNDERS, *extra], seed=secrets.randbelow(2**31), parent_id=parent_id, created_by=actor.label)
    db.add(exp)
    db.flush()
    exp.public_id = f"EXP-{exp.id:06d}"
    audit.record(db, actor, "experiment.created", "experiment", exp.public_id, {"channel": channel_slug, "metric": spec, "min_per_group": min_per_group})
    return exp


def _draw(exp: Experiment, counts: dict[str, int], video_id: int) -> tuple[str, dict[str, Any]]:
    """Permuted blocks of two: within every pair of videos one gets each arm, and which one is a seeded coin flip."""
    rng = random.Random(f"{exp.seed}:{video_id}")
    flip = rng.random()
    if counts["control"] == counts["treatment"]:
        arm, basis = ("control" if flip < 0.5 else "treatment"), "seeded coin flip (the arms had equal counts)"
    else:
        arm, basis = min(ARMS, key=lambda a: counts[a]), "balancing: the other arm had one more video than this one"
    return arm, {"method": "permuted blocks of two, seeded", "seed": exp.seed, "key": f"{exp.seed}:{video_id}", "flip": round(flip, 6), "counts_before": dict(counts), "basis": basis}


def enroll(db: Session, actor: Actor, exp_id: int, video_id: int) -> ExperimentVideo:
    actor.require(P.VIDEO_MANAGE)
    exp = _get(db, exp_id)
    if exp.status not in OPEN:
        raise ExperimentError(f"this experiment is {exp.status.lower()}; videos can no longer be added")
    v = db.get(Video, video_id)
    if v is None or v.channel_id != exp.channel_id:
        raise ExperimentError("the video must belong to the experiment's channel")
    if v.stage in (VideoStage.SCHEDULED.value, VideoStage.PUBLISHED.value, VideoStage.REJECTED.value) or v.youtube_video_id or v.published_at:
        raise ExperimentError("arms are assigned before publication, so a video that is already on YouTube (or rejected) cannot be added")
    busy = db.scalar(select(Experiment.public_id).join(ExperimentVideo, ExperimentVideo.experiment_id == Experiment.id)
                     .where(ExperimentVideo.video_id == video_id, Experiment.status.in_(OPEN)))
    if busy:
        raise ExperimentError(f"this video is already in {busy}; a video takes part in one experiment at a time so two changes never share it")
    counts = {a: 0 for a in ARMS}
    for ev in db.scalars(select(ExperimentVideo).where(ExperimentVideo.experiment_id == exp.id)):
        counts[ev.arm] += 1
    arm, draw = _draw(exp, counts, video_id)
    row = ExperimentVideo(experiment_id=exp.id, video_id=video_id, arm=arm, assigned_by=actor.label, draw=draw)
    db.add(row)
    db.flush()
    audit.record(db, actor, "experiment.video_assigned", "experiment", exp.public_id, {"video": video_id, "arm": arm})
    return row


# --- measuring ----------------------------------------------------------------------------------------------------------------


def rows(db: Session, exp: Experiment) -> list[ExperimentVideo]:
    return list(db.scalars(select(ExperimentVideo).where(ExperimentVideo.experiment_id == exp.id).order_by(ExperimentVideo.id)))


def measurements(db: Session, exp: Experiment, *, today: date | None = None) -> list[dict[str, Any]]:
    out = []
    for r in rows(db, exp):
        v = db.get(Video, r.video_id)
        out.append({"arm": r.arm, **measure.measure_video(db, v, exp.metric, today=today)})  # type: ignore[arg-type]
    return out


def baseline(db: Session, exp: Experiment, *, today: date | None = None) -> dict[str, Any]:
    """The same figure for the channel's earlier public videos, measured the same way, so the experiment's numbers have something to be read against."""
    enrolled = {r.video_id for r in rows(db, exp)}
    vids = db.scalars(select(Video).where(Video.channel_id == exp.channel_id, Video.stage == VideoStage.PUBLISHED.value, Video.published_at.is_not(None)).order_by(Video.published_at.desc()))
    got = []
    for v in vids:
        if v.id in enrolled:
            continue
        m = measure.measure_video(db, v, exp.metric, today=today)
        if m["status"] == "complete":
            got.append({"video_id": v.id, "title": m["title"], "value": m["value"]})
        if len(got) >= 10:
            break
    if not got:
        return {"status": "unavailable", "reason": "no earlier public video of this channel has complete figures for this metric, so there is no baseline to read the result against"}
    vals = [g["value"] for g in got]
    return {"status": "available", "n": len(got), "median": round(median(vals), 4), "mean": round(mean(vals), 4), "videos": got,
            "note": "the channel's most recent public videos with complete figures, measured the same way"}


# --- running ------------------------------------------------------------------------------------------------------------------


def start(db: Session, requester: Actor, exp_id: int) -> Experiment:
    """Register the plan on the board and begin. Runs the board's real steps (propose, investigate, CEO verification, approval for testing)."""
    _authority(requester)
    exp = _get(db, exp_id)
    if exp.status != "DESIGN":
        raise ExperimentError(f"this experiment is {exp.status.lower()}")
    exp.baseline = baseline(db, exp)
    ch = db.get(Channel, exp.channel_id)
    base_text = (f"median {exp.baseline['median']} over the last {exp.baseline['n']} public videos" if exp.baseline["status"] == "available"
                 else f"unavailable: {exp.baseline['reason']}")
    plan = {"hypothesis": exp.hypothesis, "success_metric": describe_metric(exp.metric), "baseline": base_text,
            "observation_period": f"{exp.metric['window_days']} days from each video's publication; analysed once at least {exp.min_per_group} videos in each arm have complete figures"}
    proposer, manager, ceo = (org.actor_for_slug(db, s) for s in ("experimentation-specialist", "analytics-manager", "ceo"))
    entry = board.propose(
        db, proposer, title=f"{exp.public_id}: {exp.title}", category=BoardCategory.EXPERIMENT, channel_slug=ch.slug, confidence=Confidence.LOW, risk_class=RiskClass.ROUTINE,  # type: ignore[union-attr]
        description=(f"This is a test, not a rule. Hypothesis: {exp.hypothesis} Change under test: {exp.change} Apply the change ONLY to videos assigned to the treatment arm "
                     f"(shown on each video's page); make control videos exactly as usual. Measured by: {describe_metric(exp.metric)}."),
        test_plan=plan, evidence_items=[{"kind": "db_record", "ref": f"experiments:{exp.id}", "note": "the registered plan"}])
    board.transition(db, manager, entry.id, BoardStatus.UNDER_INVESTIGATION, "the plan is registered; checking that it is complete")
    board.transition(db, ceo, entry.id, BoardStatus.VERIFIED)
    board.transition(db, ceo, entry.id, BoardStatus.APPROVED_FOR_TESTING, "")
    exp.board_entry_id, exp.status, exp.started_at = entry.id, "RUNNING", utcnow()
    audit.record(db, requester, "experiment.started", "experiment", exp.public_id, {"board_entry": entry.public_id})
    return exp


def analyse(db: Session, actor: Actor, exp_id: int, *, today: date | None = None) -> dict[str, Any]:
    actor.require(P.BOARD_PROPOSE)
    exp = _get(db, exp_id)
    if exp.status != "RUNNING":
        raise ExperimentError("only a running experiment can be analysed")
    table = measurements(db, exp, today=today)
    groups = {a: [m["value"] for m in table if m["arm"] == a and m["status"] == "complete"] for a in ARMS}
    result = stats.compare_groups(groups["control"], groups["treatment"], seed=exp.seed, higher_is_better=exp.metric["higher_is_better"], min_per_group=exp.min_per_group)
    looks = ((exp.analysis or {}).get("looks", 0) + 1) if result["conclusion"] != "insufficient_data" else (exp.analysis or {}).get("looks", 0)
    enrolled = {a: sum(1 for m in table if m["arm"] == a) for a in ARMS}
    attrition = {a: {"enrolled": enrolled[a], "complete": len(groups[a])} for a in ARMS}
    limits = [*LIMITATIONS]
    if enrolled["control"] != enrolled["treatment"] or any(attrition[a]["complete"] < attrition[a]["enrolled"] for a in ARMS):
        limits.append("Some enrolled videos have no complete figures and are left out. If one arm lost more videos than the other, the comparison can be biased; the table names each one.")
    if looks > 1:
        limits.append(f"This is look number {looks} at the same experiment. Repeated looks raise the chance of a false positive.")
    analysis = {**result, "looks": looks, "analysed_at": iso(utcnow()), "metric": exp.metric, "metric_description": describe_metric(exp.metric), "baseline": exp.baseline,
                "attrition": attrition, "videos": table, "confounders": exp.confounders, "limitations": limits,
                "confidence": "medium" if result["conclusion"] in ("supported_improvement", "supported_harm") else "low"}
    exp.analysis, exp.analysed_at = analysis, utcnow()
    if exp.board_entry_id:
        ref = write_json_artifact(f"experiments/{exp.public_id}", f"analysis-{utcnow().strftime('%Y%m%dT%H%M%S%f')}.json", {"experiment": exp.public_id, **analysis})
        specialist = org.actor_for_slug(db, "experimentation-specialist")
        board.add_evidence(db, specialist, exp.board_entry_id, kind=EvidenceKind.FILE, ref=ref, note=f"analysis: {result['conclusion']}")
        board.add_evidence(db, specialist, exp.board_entry_id, kind=EvidenceKind.DB_RECORD, ref=f"experiments:{exp.id}", note="the experiment record with this analysis")
        analysis["artifact"] = ref
    audit.record(db, actor, "experiment.analysed", "experiment", exp.public_id, {"conclusion": result["conclusion"], "looks": looks})
    return analysis


def decide(db: Session, requester: Actor, exp_id: int, decision: str, note: str) -> dict[str, Any]:
    _authority(requester)
    if not requester.is_owner:
        raise PermissionDenied(requester, "experiment:decide (the owner decides)")
    exp = _get(db, exp_id)
    if exp.status != "RUNNING":
        raise ExperimentError("only a running experiment can be decided")
    if decision not in DECISIONS:
        raise ExperimentError(f"the decision must be one of {', '.join(DECISIONS)}")
    if not note.strip():
        raise ExperimentError("say why; the reason is kept with the result")
    an = exp.analysis
    if an is None:
        raise ExperimentError("analyse the experiment first, so the decision rests on a recorded result")
    if decision == "adopt" and an["conclusion"] != "supported_improvement":
        raise ExperimentError(f"the analysis says '{an['conclusion'].replace('_', ' ')}', which does not support adopting the change; choose repeat, revise or reject")
    entry = db.get(board.BoardEntry, exp.board_entry_id)  # type: ignore[arg-type]
    ceo = org.actor_for_slug(db, "ceo")
    summary = f"{exp.public_id}: {an['summary']} Decision ({decision}): {note.strip()}"
    child = None
    if decision == "adopt":
        board.record_result(db, ceo, entry.id, confidence=Confidence.MEDIUM, result_text=f"{an['summary']} {CONFIDENCE_NOTE}")  # type: ignore[union-attr]
        board.transition(db, requester, entry.id, BoardStatus.ACTIVE_INSTRUCTION, summary)  # type: ignore[union-attr]
    elif decision == "reject":
        board.transition(db, requester, entry.id, BoardStatus.REJECTED, summary)  # type: ignore[union-attr]
    else:
        board.transition(db, requester, entry.id, BoardStatus.EXPIRED, summary)  # type: ignore[union-attr]
        if decision == "repeat":
            ch = db.get(Channel, exp.channel_id)
            child = create(db, org.actor_for_slug(db, "experimentation-specialist"), channel_slug=ch.slug, title=exp.title, hypothesis=exp.hypothesis, change=exp.change,  # type: ignore[union-attr]
                           metric=exp.metric, min_per_group=exp.min_per_group, confounders=exp.confounders[len(DEFAULT_CONFOUNDERS):], parent_id=exp.id)
    exp.decision, exp.decision_note, exp.decided_by, exp.decided_at = decision, note.strip(), requester.label, utcnow()
    exp.status, exp.concluded_at = "CONCLUDED", utcnow()
    audit.record(db, requester, "experiment.decided", "experiment", exp.public_id, {"decision": decision, "conclusion": an["conclusion"], "repeat": child.public_id if child else None})
    return {"experiment": serialize(db, exp), "repeat": serialize(db, child) if child else None}


def cancel(db: Session, requester: Actor, exp_id: int, reason: str) -> Experiment:
    _authority(requester)
    exp = _get(db, exp_id)
    if exp.status not in OPEN:
        raise ExperimentError(f"this experiment is {exp.status.lower()}")
    if not reason.strip():
        raise ExperimentError("say why")
    if exp.board_entry_id:
        board.transition(db, requester, exp.board_entry_id, BoardStatus.EXPIRED, f"{exp.public_id} cancelled: {reason.strip()}")
    exp.status, exp.decision_note, exp.concluded_at = "CANCELLED", reason.strip(), utcnow()
    audit.record(db, requester, "experiment.cancelled", "experiment", exp.public_id, {"reason": reason.strip()})
    return exp


# --- reading ------------------------------------------------------------------------------------------------------------------


def for_video(db: Session, video_id: int) -> list[dict[str, Any]]:
    """What a person making this video must know: which experiment it is in, which arm, and what (not) to do."""
    out = []
    for r in db.scalars(select(ExperimentVideo).where(ExperimentVideo.video_id == video_id)):
        exp = db.get(Experiment, r.experiment_id)
        if exp is None or exp.status not in OPEN:
            continue
        what = (f"Apply this change to this video: {exp.change}" if r.arm == "treatment"
                else "This is a control video: make it exactly as usual and do NOT apply the change.")
        out.append({"experiment": exp.public_id, "title": exp.title, "status": exp.status, "arm": r.arm, "instruction": what})
    return out


def serialize(db: Session, exp: Experiment, *, detail: bool = False) -> dict[str, Any]:
    ch = db.get(Channel, exp.channel_id)
    entry = db.get(board.BoardEntry, exp.board_entry_id) if exp.board_entry_id else None
    vids = rows(db, exp)
    d: dict[str, Any] = {
        "id": exp.id, "public_id": exp.public_id, "channel": ch.slug if ch else None, "title": exp.title, "hypothesis": exp.hypothesis, "change": exp.change, "status": exp.status,
        "metric": exp.metric, "metric_description": describe_metric(exp.metric), "min_per_group": exp.min_per_group, "confounders": exp.confounders, "baseline": exp.baseline,
        "board_entry": entry.public_id if entry else None, "board_status": entry.status if entry else None, "parent_id": exp.parent_id,
        "enrolled": {a: sum(1 for r in vids if r.arm == a) for a in ARMS}, "decision": exp.decision, "decision_note": exp.decision_note, "decided_by": exp.decided_by,
        "decided_at": iso(exp.decided_at) if exp.decided_at else None, "created_by": exp.created_by, "created_at": iso(exp.created_at),
        "started_at": iso(exp.started_at) if exp.started_at else None, "concluded_at": iso(exp.concluded_at) if exp.concluded_at else None,
        "analysis": ({k: v for k, v in exp.analysis.items() if k != "videos"} if exp.analysis else None), "limitations": LIMITATIONS,
        "seed_note": "The arms are drawn from this experiment's seed, so anyone can re-check them." if exp.status != "DESIGN" else None,
    }
    if detail:
        d["videos"] = measurements(db, exp)
        d["assignments"] = [{"video_id": r.video_id, "arm": r.arm, "assigned_by": r.assigned_by, "assigned_at": iso(r.assigned_at), "draw": r.draw} for r in vids]
        d["seed"] = exp.seed
    return d


def list_all(db: Session) -> list[dict[str, Any]]:
    return [serialize(db, e) for e in db.scalars(select(Experiment).order_by(Experiment.id.desc()))]
