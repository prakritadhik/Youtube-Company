"""Copyright & originality screening for a video.

Produces a structured rights report and a status — PASS, NEEDS_REVISION, BLOCKED or HUMAN_REVIEW_REQUIRED —
derived from explicit findings. A numeric similarity figure is *recorded* but never decides a status: only findings
do, and a missing licence or an unresolved concern can never be outvoted by a good score.

These are screening aids. They do not prove a work is free of copyright risk, and the report says so.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import OriginalityStatus as O
from app.enums import VideoStage
from app.models.org import Channel
from app.models.production import Asset, CreativeBrief, OriginalityReview, ScriptVersion, SourceRecord
from app.models.videos import Video
from app.production import assets as assets_svc
from app.production import imaging
from app.production import similarity as sim
from app.security.permissions import Actor, P
from app.services import audit, videos
from app.storage import write_json_artifact
from app.timeutil import iso, utcnow

# Thresholds (documented in the report so a reviewer can see what "similar" meant)
BLOCK_CONTAINMENT = 0.60  # most of this text already appears in an earlier upload on the channel
REVISE_CONTAINMENT = 0.30
REVISE_LONG_RUN = 8  # consecutive identical words shared with an earlier upload
REVISE_DUP_LINES = 3  # near-identical lines (a few words changed) shared with an earlier upload
BLOCK_SOURCE_RUN = 25  # verbatim run copied from a recorded source
REVISE_SOURCE_RUN = 12
THUMB_HAMMING = 8  # of 64 bits
SCENE_HAMMING = 6
FORMULAIC_COUNT = 2  # earlier uploads (of the last five) with the very same structure

TEXT_KIND_BY_CHANNEL = {"birthday": "lyrics", "medicine": "script", "story": "script"}

TOOLS = [
    "4-word shingle containment/Jaccard and longest-common-run (text)",
    "line-level fuzzy matching (catches a few swapped words and reordered lines)",
    "short built-in list of well-known birthday-song lines (not a database of protected works)",
    "asset provenance and licence register with file-hash re-verification",
    "64-bit perceptual hash (thumbnails and scenes)",
    "structural signature comparison (sections, tempo, key, instrumentation)",
]
LIMITATIONS = [
    "Synonym-level paraphrase is not detected by the text checks.",
    "No external lyric, music or footage database was searched; only this channel's own history and the recorded sources were compared.",
    "No audio-fingerprint tool is installed, so melodic or recording similarity to third-party music is NOT checked.",
    "Similarity tools are screening aids, not proof of legal safety. A PASS means no concern was found by these checks.",
]


def _latest_text(db: Session, video_id: int, kind: str) -> ScriptVersion | None:
    return db.scalar(select(ScriptVersion).where(ScriptVersion.video_id == video_id, ScriptVersion.kind == kind)
                     .order_by(ScriptVersion.version.desc()))


def _latest_brief(db: Session, video_id: int) -> CreativeBrief | None:
    return db.scalar(select(CreativeBrief).where(CreativeBrief.video_id == video_id, CreativeBrief.status == "APPROVED")
                     .order_by(CreativeBrief.version.desc())) or db.scalar(
        select(CreativeBrief).where(CreativeBrief.video_id == video_id).order_by(CreativeBrief.version.desc()))


def _exempt_brand_lines(text: str, brand_phrases: list[str]) -> str:
    """Drop lines that are deliberate, recurring brand elements (a channel jingle, a sign-off) before comparing."""
    phrases = [sim.normalize(p) for p in brand_phrases if p.strip()]
    if not phrases:
        return text
    keep = [ln for ln in text.splitlines() if not any(p and p in sim.normalize(ln) for p in phrases)]
    return "\n".join(keep)


def _finding(severity: str, code: str, message: str, **evidence: Any) -> dict[str, Any]:
    return {"severity": severity, "code": code, "message": message, "evidence": evidence}


_ORDER = ("block", "revise", "human", "info")


def resolve_status(findings: list[dict[str, Any]]) -> tuple[str, list[str]]:
    sev = {f["severity"] for f in findings}
    if "block" in sev:
        status = O.BLOCKED
    elif "revise" in sev:
        status = O.NEEDS_REVISION
    elif "human" in sev:
        status = O.HUMAN_REVIEW_REQUIRED
    else:
        status = O.PASS
    reasons = [f["message"] for f in findings if f["severity"] in ("block", "revise", "human")]
    return status.value, reasons


def analyse(db: Session, video: Video) -> dict[str, Any]:
    ch = db.get(Channel, video.channel_id)
    assert ch is not None
    brand_phrases = list((ch.brand or {}).get("brand_phrases", []))
    brief = _latest_brief(db, video.id)
    bc = brief.content if brief else {}
    findings: list[dict[str, Any]] = []
    scores: dict[str, Any] = {}

    others = [o for o in db.scalars(select(Video).where(Video.channel_id == video.channel_id, Video.id != video.id,
                                                        Video.stage != VideoStage.REJECTED.value).order_by(Video.id.desc()))
              if not (o.metadata_json or {}).get("demo")]  # demonstration videos are not real uploads

    # --- text --------------------------------------------------------------------------------------------------
    kind = TEXT_KIND_BY_CHANNEL.get(ch.kind, "script")
    text_row = _latest_text(db, video.id, kind)
    script_info: dict[str, Any] = {"kind": kind, "present": text_row is not None}
    if text_row is None:
        findings.append(_finding("revise", "NO_TEXT", f"No {kind} has been saved for this video, so originality cannot be assessed."))
    else:
        script_info.update({"version": text_row.version, "author_kind": text_row.author_kind, "author": text_row.author_label,
                            "word_count": len(sim.words(text_row.text))})
        cand = _exempt_brand_lines(text_row.text, brand_phrases)
        if kind == "lyrics":
            pat = sim.traditional_birthday_pattern(text_row.text)
            script_info["traditional_refrain_check"] = pat
            if pat["full_traditional_refrain"]:
                findings.append(_finding("human", "TRADITIONAL_REFRAIN",
                                         "The lyrics reproduce the traditional four-line 'Happy Birthday to You' refrain. Its rights status "
                                         "depends on jurisdiction and must not be assumed; a person must decide, or the lyrics must be rewritten.", **pat))
            elif pat["happy_birthday_to_you_lines"] >= 2:
                findings.append(_finding("info", "GREETING_PHRASE", "Uses the familiar greeting 'Happy birthday to you' more than once; the rest must be original."))
            for k in sim.known_lines_found(text_row.text):
                findings.append(_finding("info", "KNOWN_LINE", f"Contains the well-known line '{k['line']}'. {k['note']}"))
        # copied from a recorded source (medicine and story research)
        src_runs = []
        for s in db.scalars(select(SourceRecord).where(SourceRecord.video_id == video.id)):
            blob = f"{s.note} {s.supports} {s.title}"
            run = sim.longest_common_run(sim.words(cand), sim.words(blob))
            src_runs.append({"source_id": s.id, "run_words": run})
            if run >= BLOCK_SOURCE_RUN:
                findings.append(_finding("block", "COPIED_FROM_SOURCE", f"{run} consecutive words are copied from source #{s.id}.", source_id=s.id, run=run))
            elif run >= REVISE_SOURCE_RUN:
                findings.append(_finding("revise", "CLOSE_TO_SOURCE", f"{run} consecutive words match source #{s.id}; rewrite in original words.", source_id=s.id, run=run))
        scores["source_runs"] = src_runs

        # repetition against earlier uploads
        comparisons = []
        for o in others:
            ot = _latest_text(db, o.id, kind)
            if ot is None:
                continue
            c = sim.compare(cand, _exempt_brand_lines(ot.text, brand_phrases))
            c["other_video_id"] = o.id
            comparisons.append(c)
            label = f"video #{o.id}"
            if c["containment_in_other"] >= BLOCK_CONTAINMENT:
                findings.append(_finding("block", "DUPLICATES_EARLIER_UPLOAD",
                                         f"{c['containment_in_other']:.0%} of this {kind} already appears in {label}. Near-duplicate content is not allowed.", other=o.id))
            elif (c["containment_in_other"] >= REVISE_CONTAINMENT or c["longest_common_run_words"] >= REVISE_LONG_RUN
                  or len(c["near_duplicate_lines"]) >= REVISE_DUP_LINES):
                findings.append(_finding("revise", "REPEATS_EARLIER_UPLOAD",
                                         f"Shares substantial wording with {label} ({c['containment_in_other']:.0%} overlap, a {c['longest_common_run_words']}-word run, "
                                         f"{len(c['near_duplicate_lines'])} near-identical line(s)). Make it meaningfully different.", other=o.id))
        scores["text_comparisons"] = comparisons  # informational only

    # --- structure / formula -------------------------------------------------------------------------------------------
    sig = sim.structure_signature(bc)
    same = []
    for o in others[:5]:
        ob = _latest_brief(db, o.id)
        if ob and sim.same_shape(sig, sim.structure_signature(ob.content)):
            same.append(o.id)
    scores["same_shape_as"] = same
    if len(same) >= FORMULAIC_COUNT:
        findings.append(_finding("revise", "FORMULAIC_STRUCTURE", f"Same sections, tempo, key and instrumentation as {len(same)} of the last five uploads "
                                 f"({same}). Change the musical or story shape so it is a distinct experience.", videos=same))
    elif same:
        findings.append(_finding("info", "SIMILAR_STRUCTURE", f"Same structure as video #{same[0]}; acceptable once, but vary it."))

    # --- assets / provenance / licences -------------------------------------------------------------------------------------
    all_assets = assets_svc.for_video(db, video.id)
    problems = assets_svc.license_problems(db, video.id)
    for p in problems:
        sev = "block" if p["code"] in ("COMMERCIAL_USE_PROHIBITED", "SIMULATED") else "revise"
        findings.append(_finding(sev, f"ASSET_{p['code']}", f"Asset {p['role'] or p['asset_id']}: {p['message']}",
                                     asset_id=p["asset_id"], role=p["role"], problem=p["code"]))
    if not all_assets:
        findings.append(_finding("revise", "NO_ASSETS", "No assets are registered for this video, so nothing can be cleared."))

    music = [a for a in all_assets if a.kind == "music"]
    mp = bc.get("musical_plan") or {}
    music_info: dict[str, Any] = {"assets": [{"id": a.id, "provider": a.provider, "model": a.model, "seed": a.seed,
                                              "generation_ref": a.generation_ref, "license_type": a.license_type,
                                              "commercial_use": a.commercial_use, "vocals": a.params.get("vocals")} for a in music]}
    if ch.kind in ("birthday", "story") and not music:
        findings.append(_finding("revise", "NO_MUSIC", "No music asset with a recorded origin was registered."))
    for a in music:
        if a.provider not in assets_svc.LOCAL_PROVIDERS:
            findings.append(_finding("human", "AUDIO_SIMILARITY_UNCHECKED",
                                     f"Music asset #{a.id} comes from an external provider and no audio-fingerprint tool is available, so melodic or "
                                     "recording similarity to existing music was not checked. A person must review it.", asset=a.id))
        wants_vocals = mp.get("vocal_delivery") == "sung"
        if wants_vocals and a.params.get("vocals") is not True:
            findings.append(_finding("revise", "VOCALS_MISSING", f"The plan calls for a sung song but music asset #{a.id} has no vocals. "
                                     "Do not present instrumental audio as a sung song."))
        elif wants_vocals and not a.params.get("vocals_verified"):
            findings.append(_finding("human", "VOCALS_UNVERIFIED", f"Music asset #{a.id} claims vocals but nobody has verified that the audio is genuinely sung."))
    if ch.policy.get("singing_must_be_verified") and mp.get("vocal_delivery") == "sung" and not music:
        findings.append(_finding("revise", "NO_SUNG_AUDIO", "A sung song is planned but no audio exists."))
    visual_info = [{"id": a.id, "role": a.role, "provider": a.provider, "license": a.license_type} for a in all_assets
                   if a.kind in ("image", "thumbnail", "clip")]

    # --- visual reuse ------------------------------------------------------------------------------------------------------------
    def hash_of(a: Asset) -> str | None:
        if a.params.get("dhash"):
            return a.params["dhash"]
        try:
            return imaging.dhash(assets_svc.resolve_asset_path(a.path))
        except Exception:  # noqa: BLE001 - an unreadable image is reported by the licence checks
            return None

    other_assets = [a for o in others for a in assets_svc.for_video(db, o.id)]
    thumb = next((a for a in all_assets if a.kind == "thumbnail"), None)
    if thumb is None:
        findings.append(_finding("revise", "NO_THUMBNAIL", "No thumbnail is registered."))
    else:
        th = hash_of(thumb)
        dists = [(imaging.hamming(th, h), a.video_id) for a in other_assets if a.kind == "thumbnail" and th and (h := hash_of(a))]
        scores["nearest_thumbnail_distance"] = min(dists) if dists else None
        if dists and min(dists)[0] <= THUMB_HAMMING:
            findings.append(_finding("revise", "THUMBNAIL_TOO_SIMILAR", f"The thumbnail is nearly identical to video #{min(dists)[1]}'s "
                                     f"(distance {min(dists)[0]}/64).", distance=min(dists)[0]))
    scenes = [a for a in all_assets if a.kind == "image"]
    old_scene_hashes = [h for a in other_assets if a.kind == "image" and (h := hash_of(a))]
    reused = [a.id for a in scenes if (h := hash_of(a)) and any(imaging.hamming(h, o) <= SCENE_HAMMING for o in old_scene_hashes)]
    scores["reused_scene_assets"] = reused
    if scenes and len(reused) / len(scenes) >= 0.5:
        findings.append(_finding("revise", "REUSED_SCENES", f"{len(reused)} of {len(scenes)} scenes are visually the same as scenes in earlier uploads."))

    # --- distinctive contribution -----------------------------------------------------------------------------------------------------
    distinct = [d for d in (bc.get("distinctive_elements") or []) if str(d).strip()]
    if not distinct:
        findings.append(_finding("revise", "NO_DISTINCTIVE_CONTRIBUTION", "The brief does not state what is distinctive about this video."))

    # --- medicine: sources and citations --------------------------------------------------------------------------------------------------
    if ch.policy.get("source_citations_required"):
        srcs = list(db.scalars(select(SourceRecord).where(SourceRecord.video_id == video.id)))
        if not srcs:
            findings.append(_finding("revise", "NO_SOURCES", "A medicine video needs recorded sources (URL, retrieval date, supported claim)."))
        if text_row is not None and not text_row.claims:
            findings.append(_finding("revise", "NO_CITED_CLAIMS", "The script has no source-cited factual claims."))
        findings.append(_finding("info", "MEDICAL_REVIEW_SEPARATE", "A human medical review is required separately before publication; this report does not replace it."))

    status, reasons = resolve_status(findings)
    actions = []
    for f in findings:
        if f["severity"] in ("block", "revise", "human"):
            actions.append({"code": f["code"], "action": _ACTIONS.get(f["code"], "Resolve this finding, then request a new review."),
                            "severity": f["severity"]})
    return {
        "video_id": video.id, "channel": ch.slug, "generated_at": iso(utcnow()), "method": "deterministic screening v1",
        "thresholds": {"block_containment": BLOCK_CONTAINMENT, "revise_containment": REVISE_CONTAINMENT,
                       "revise_long_run_words": REVISE_LONG_RUN, "revise_duplicate_lines": REVISE_DUP_LINES,
                       "thumbnail_hamming_max": THUMB_HAMMING, "formulaic_count": FORMULAIC_COUNT},
        "script_or_lyric_originality": script_info, "music_and_audio_provenance": music_info,
        "visual_asset_provenance": visual_info,
        "license_and_commercial_use": {"assets_checked": len(all_assets), "problems": problems,
                                       "all_clear": not problems and bool(all_assets)},
        "similarity_warnings": [f for f in findings if f["code"] in ("REPEATS_EARLIER_UPLOAD", "DUPLICATES_EARLIER_UPLOAD", "CLOSE_TO_SOURCE",
                                                                    "COPIED_FROM_SOURCE", "THUMBNAIL_TOO_SIMILAR", "REUSED_SCENES", "KNOWN_LINE")],
        "repetition_vs_previous_uploads": {"compared_with": [o.id for o in others], "scores_are_informational_only": True,
                                           "details": scores},
        "distinctive_creative_contributions": distinct,
        "findings": findings, "unresolved_risks": reasons, "required_corrective_actions": actions,
        "tools_used": TOOLS, "limitations": LIMITATIONS, "status": status,
        "status_basis": "The status comes only from the findings above. No similarity score can override a missing licence or an unresolved concern.",
    }


_ACTIONS = {
    "NO_TEXT": "Save the lyrics/script, then request a new review.",
    "TRADITIONAL_REFRAIN": "Rewrite the refrain with new words, or have a person decide on its rights status.",
    "COPIED_FROM_SOURCE": "Rewrite the passage in original words and cite the source instead of quoting it.",
    "CLOSE_TO_SOURCE": "Rewrite the passage in your own words.",
    "DUPLICATES_EARLIER_UPLOAD": "Do not publish a near-duplicate. Create genuinely new content or reject this video.",
    "REPEATS_EARLIER_UPLOAD": "Rewrite the repeated passages and change the concept so the video adds new value.",
    "FORMULAIC_STRUCTURE": "Change the structure, tempo, key or instrumentation so the experience is distinct.",
    "NO_MUSIC": "Generate or license music and register it with its provenance.",
    "AUDIO_SIMILARITY_UNCHECKED": "Have a person listen for similarity to existing music, or generate with a provider whose output can be fingerprinted.",
    "VOCALS_MISSING": "Produce sung audio with a music provider, or change the plan to instrumental. Do not call instrumental audio a sung song.",
    "VOCALS_UNVERIFIED": "Listen to the track and confirm the vocals before publication.",
    "NO_THUMBNAIL": "Create a thumbnail and register it.",
    "THUMBNAIL_TOO_SIMILAR": "Design a visibly different thumbnail.",
    "REUSED_SCENES": "Create new scenes rather than reusing earlier artwork.",
    "NO_DISTINCTIVE_CONTRIBUTION": "State, in the brief, what this video contributes that earlier ones do not.",
    "NO_SOURCES": "Record the sources consulted (URL, retrieval date, claim supported).",
    "NO_CITED_CLAIMS": "Re-save the script with a source marker on every factual sentence.",
    "NO_ASSETS": "Register every file used, with its origin and licence.",
    "NO_SUNG_AUDIO": "Produce the sung audio or change the plan.",
}


def run_review(db: Session, actor: Actor, video_id: int, *, task_id: int | None = None) -> OriginalityReview:
    """The reviewer's report. Saved as a hash-verifiable artifact so a manager and the CEO can inspect it."""
    actor.require(P.ORIGINALITY_REVIEW)
    video = db.get(Video, video_id)
    if video is None:
        raise ValueError("video not found")
    report = analyse(db, video)
    version = len(list(db.scalars(select(OriginalityReview.id).where(OriginalityReview.video_id == video_id)))) + 1
    report["review_version"] = version
    rel = write_json_artifact(f"videos/{video_id}", f"originality-v{version}.json", report)
    row = OriginalityReview(video_id=video_id, version=version, status=report["status"], report=report, artifact_path=rel,
                            created_by=actor.label, task_id=task_id)
    db.add(row)
    db.flush()
    audit.record(db, actor, "originality.reviewed", "video", video_id, {"status": report["status"], "version": version,
                                                                        "findings": len(report["findings"])})
    return row


class StaleReview(ValueError):
    pass


def apply_verdict(db: Session, actor: Actor, video_id: int, review_id: int, *, review_task_id: int) -> Video:
    """After the manager verified the review task, copy its verdict onto the video (and out of date reviews are refused)."""
    review = db.get(OriginalityReview, review_id)
    if review is None or review.video_id != video_id:
        raise ValueError("review not found for this video")
    latest = db.scalar(select(OriginalityReview).where(OriginalityReview.video_id == video_id).order_by(OriginalityReview.version.desc()))
    if latest is not None and latest.id != review.id:
        raise StaleReview("a newer review exists; apply that one")
    newest_change = max(
        [a.created_at for a in assets_svc.for_video(db, video_id)]
        + [s.created_at for s in db.scalars(select(ScriptVersion).where(ScriptVersion.video_id == video_id))]
        + [b.created_at for b in db.scalars(select(CreativeBrief).where(CreativeBrief.video_id == video_id))] or [review.created_at])
    if newest_change > review.created_at:
        raise StaleReview("assets, scripts or the brief changed after this review; run a new review")
    problems = assets_svc.license_problems(db, video_id)
    return videos.record_originality(db, actor, video_id, O(review.status), review_task_id=review_task_id,
                                     asset_license_issues=len(problems), note=f"review v{review.version}")


def ceo_inspect(db: Session, ceo: Actor, video_id: int) -> Video:
    """The CEO's own inspection: recompute the rights analysis from the records and compare it with the stored verdict.

    The stored review is not trusted. Sign-off happens only if the CEO's fresh analysis is a PASS *and* agrees with
    what the reviewer recorded; any difference is reported rather than smoothed over.
    """
    ceo.require(P.RIGHTS_VERIFY)
    video = db.get(Video, video_id)
    if video is None:
        raise videos.VideoError("video not found")
    stored = db.scalar(select(OriginalityReview).where(OriginalityReview.video_id == video_id).order_by(OriginalityReview.version.desc()))
    if stored is None:
        raise videos.VideoError("no originality review exists to inspect")
    fresh = analyse(db, video)
    if fresh["status"] != O.PASS.value:
        raise videos.VideoError("the CEO's independent re-check does not agree that this video is clear: " + "; ".join(fresh["unresolved_risks"][:3]))
    if stored.status != fresh["status"] or video.originality_status != fresh["status"]:
        raise videos.VideoError(f"the stored verdict ({stored.status}/{video.originality_status}) differs from the CEO's re-check ({fresh['status']})")
    return videos.ceo_verify_rights(db, ceo, video_id, note="independent re-check agreed with the reviewer")
