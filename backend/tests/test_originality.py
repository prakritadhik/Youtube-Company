from __future__ import annotations

from datetime import timedelta

import pytest

from app.config import get_settings
from app.enums import OriginalityStatus as O
from app.enums import VideoStage as V
from app.models.org import Channel
from app.models.videos import Video
from app.production import assets, scripts
from app.production import originality as og
from app.security.permissions import PermissionDenied
from app.services import gate, videos
from app.storage import write_bytes_artifact
from app.timeutil import utcnow
from tests.helpers import verified_task
from tests.prod_helpers import (
    LYRICS_A,
    LYRICS_B,
    MED,
    add_brief,
    add_lyrics,
    add_music,
    add_scenes,
    add_thumbnail,
    approve_metadata,
    full_package,
    make_video,
    tiny_mp4,
)

DIFFERENT = dict(musical_plan={"tempo_bpm": 148, "key": "D minor", "mood": "energetic", "instrumentation": ["pluck", "bass", "drums"],
                               "song_structure": ["intro", "chorus", "verse", "chorus", "outro"]}, style_tags=["night", "city"])


def review(db, agent, video):
    return og.run_review(db, agent("copyright-originality-reviewer"), video.id)


def codes(rev, severity=None):
    return {f["code"] for f in rev.report["findings"] if severity is None or f["severity"] == severity}


def earlier(db, agent, **kw):
    """A previously produced video on the channel, deliberately different in every respect."""
    v = make_video(db, agent, title="Night Lanterns")
    full_package(db, agent, v, lyrics=kw.pop("lyrics", LYRICS_B), seed=kw.pop("seed", 11), scene_seed=kw.pop("scene_seed", 400),
                 thumb_seed=kw.pop("thumb_seed", 77), **{**DIFFERENT, **kw})
    return v


def test_a_clean_original_video_passes_and_the_report_is_complete(db, agent):
    earlier(db, agent)
    v = make_video(db, agent)
    full_package(db, agent, v)
    rev = review(db, agent, v)
    assert rev.status == O.PASS.value, rev.report["unresolved_risks"]
    r = rev.report
    for key in ("script_or_lyric_originality", "music_and_audio_provenance", "visual_asset_provenance", "license_and_commercial_use",
                "similarity_warnings", "repetition_vs_previous_uploads", "distinctive_creative_contributions", "unresolved_risks",
                "required_corrective_actions", "tools_used", "limitations", "status_basis"):
        assert key in r, key
    assert r["license_and_commercial_use"]["all_clear"] and r["distinctive_creative_contributions"]
    assert any("fingerprint" in x for x in r["limitations"]) and any("screening aids" in x for x in r["limitations"])
    assert r["repetition_vs_previous_uploads"]["scores_are_informational_only"] is True
    assert resolve(rev.artifact_path).is_file()


def resolve(rel):
    from app.storage import resolve_data_path

    return resolve_data_path(rel)


def test_a_missing_licence_can_never_pass(db, agent):
    earlier(db, agent)
    v = make_video(db, agent)
    add_brief(db, agent, v), add_lyrics(db, agent, v), add_scenes(db, agent, v, base_seed=100), add_thumbnail(db, agent, v, seed=5)
    add_music(db, agent, v, license_type="unknown", commercial="unknown")
    rev = review(db, agent, v)
    assert rev.status == O.NEEDS_REVISION.value and {"ASSET_NO_LICENSE", "ASSET_COMMERCIAL_USE_UNCONFIRMED"} <= codes(rev)
    # similarity is excellent here (nothing repeated) and still does not matter:
    assert rev.report["repetition_vs_previous_uploads"]["details"]["text_comparisons"][0]["containment_in_other"] == 0


def test_a_prohibited_commercial_use_is_blocked(db, agent):
    v = make_video(db, agent)
    add_brief(db, agent, v), add_lyrics(db, agent, v), add_scenes(db, agent, v), add_thumbnail(db, agent, v)
    add_music(db, agent, v, license_type="provider-terms", commercial="no")
    rev = review(db, agent, v)
    assert rev.status == O.BLOCKED.value and "ASSET_COMMERCIAL_USE_PROHIBITED" in codes(rev, "block")


def test_a_changed_asset_file_voids_the_clearance(db, agent):
    v = make_video(db, agent)
    full_package(db, agent, v)
    music = next(a for a in assets.for_video(db, v.id) if a.kind == "music")
    from app.storage import resolve_data_path

    resolve_data_path(music.path).write_bytes(b"replaced with something unverified")
    assert "ASSET_FILE_CHANGED" in codes(review(db, agent, v))


def test_copying_an_earlier_upload_is_blocked(db, agent):
    first = make_video(db, agent, title="First")
    full_package(db, agent, first, lyrics=LYRICS_A, seed=11, scene_seed=400, thumb_seed=77, **DIFFERENT)
    copy = make_video(db, agent, title="Second")
    full_package(db, agent, copy, lyrics=LYRICS_A)
    rev = review(db, agent, copy)
    assert rev.status == O.BLOCKED.value and "DUPLICATES_EARLIER_UPLOAD" in codes(rev, "block")


def test_superficial_edits_do_not_make_a_copy_original(db, agent):
    first = make_video(db, agent, title="First")
    full_package(db, agent, first, lyrics=LYRICS_A, seed=11, scene_seed=400, thumb_seed=77, **DIFFERENT)
    swapped = """[Verse]
Morning light is on the hill tonight
Paper hats and lanterns on the road
[Chorus]
Spin around, the garden starts to shine
Candles dancing in a lovely row"""
    reordered_and_swapped = "\n".join(reversed(swapped.splitlines()))
    for text in (swapped, reordered_and_swapped):
        v = make_video(db, agent, title="Remix")
        full_package(db, agent, v, lyrics=text, seed=3)
        rev = review(db, agent, v)
        assert rev.status in (O.NEEDS_REVISION.value, O.BLOCKED.value), rev.status
        assert codes(rev) & {"REPEATS_EARLIER_UPLOAD", "DUPLICATES_EARLIER_UPLOAD"}


def test_the_traditional_refrain_needs_a_human_decision(db, agent):
    v = make_video(db, agent)
    full_package(db, agent, v, lyrics="[Chorus]\nHappy birthday to you\nHappy birthday to you\nHappy birthday dear Sam\nHappy birthday to you")
    rev = review(db, agent, v)
    assert "TRADITIONAL_REFRAIN" in codes(rev, "human")
    assert rev.status == O.HUMAN_REVIEW_REQUIRED.value


def test_a_greeting_phrase_alone_is_not_a_problem(db, agent):
    v = make_video(db, agent)
    full_package(db, agent, v, lyrics="[Verse]\nHappy birthday to you, Sam, the sun is up\nHappy birthday to you, bring the biggest cup\n[Chorus]\nBells ring out across the green\nBrightest morning ever seen")
    rev = review(db, agent, v)
    assert rev.status == O.PASS.value and "GREETING_PHRASE" in codes(rev, "info")


def test_external_music_without_fingerprinting_needs_a_human(db, agent):
    v = make_video(db, agent)
    add_brief(db, agent, v), add_lyrics(db, agent, v), add_scenes(db, agent, v), add_thumbnail(db, agent, v)
    add_music(db, agent, v, provider="elevenlabs", license_type="provider-terms", commercial="yes")
    rev = review(db, agent, v)
    assert rev.status == O.HUMAN_REVIEW_REQUIRED.value and "AUDIO_SIMILARITY_UNCHECKED" in codes(rev, "human")


def test_instrumental_audio_cannot_stand_in_for_a_sung_song(db, agent):
    v = make_video(db, agent)
    full_package(db, agent, v, musical_plan={"vocal_delivery": "sung"})
    rev = review(db, agent, v)
    assert rev.status == O.NEEDS_REVISION.value and "VOCALS_MISSING" in codes(rev, "revise")


def test_claimed_vocals_must_be_verified(db, agent):
    v = make_video(db, agent)
    add_brief(db, agent, v, musical_plan={"vocal_delivery": "sung"}), add_lyrics(db, agent, v), add_scenes(db, agent, v), add_thumbnail(db, agent, v)
    add_music(db, agent, v, provider="elevenlabs", license_type="provider-terms", commercial="yes", vocals=True)
    assert "VOCALS_UNVERIFIED" in codes(review(db, agent, v), "human")


def test_formulaic_repetition_of_the_same_shape_is_flagged(db, agent):
    for i, lyr in enumerate((LYRICS_B, "[Verse]\nSnow is falling softly on the lane\nSomeone hums a little sweet refrain\n[Chorus]\nSparkle bright and shimmer in the air\nWinter wishes dancing everywhere")):
        v = make_video(db, agent, title=f"Same shape {i}")
        full_package(db, agent, v, lyrics=lyr, seed=20 + i, scene_seed=500 + i * 50, thumb_seed=60 + i * 9)
    third = make_video(db, agent, title="Same shape 3")
    full_package(db, agent, third, lyrics="[Verse]\nCrickets play along the garden wall\nFireflies are answering the call\n[Chorus]\nHold the lantern, wave it side to side\nStars are rolling in like a tide", seed=33, scene_seed=900, thumb_seed=91)
    rev = review(db, agent, third)
    assert "FORMULAIC_STRUCTURE" in codes(rev, "revise") and rev.status == O.NEEDS_REVISION.value


def test_a_near_identical_thumbnail_is_flagged(db, agent):
    earlier(db, agent, thumb_seed=5)
    v = make_video(db, agent)
    full_package(db, agent, v, thumb_seed=5)
    assert "THUMBNAIL_TOO_SIMILAR" in codes(review(db, agent, v), "revise")


def test_reused_scenes_are_flagged(db, agent):
    earlier(db, agent, scene_seed=100)
    v = make_video(db, agent)
    full_package(db, agent, v, scene_seed=100)  # the very same artwork
    assert "REUSED_SCENES" in codes(review(db, agent, v), "revise")


def test_deliberate_brand_lines_are_not_mistaken_for_repetition(db, agent):
    signoff = "\nThanks for singing with us today\nCome back soon and sing along\nSee you at the next song"
    a = make_video(db, agent, title="A")
    full_package(db, agent, a, lyrics=LYRICS_B + "\n[Outro]" + signoff, seed=11, scene_seed=400, thumb_seed=77, **DIFFERENT)
    b = make_video(db, agent, title="B")
    full_package(db, agent, b, lyrics=LYRICS_A + "\n[Outro]" + signoff)
    assert "REPEATS_EARLIER_UPLOAD" in codes(review(db, agent, b))  # without an exemption the shared sign-off counts
    ch = db.get(Channel, b.channel_id)
    ch.brand = {**ch.brand, "brand_phrases": ["Thanks for singing with us today", "Come back soon and sing along", "See you at the next song"]}
    db.flush()
    assert "REPEATS_EARLIER_UPLOAD" not in codes(review(db, agent, b))


def test_a_bare_video_is_not_a_pass(db, agent):
    v = make_video(db, agent)
    rev = review(db, agent, v)
    assert rev.status == O.NEEDS_REVISION.value and {"NO_TEXT", "NO_ASSETS", "NO_MUSIC", "NO_THUMBNAIL"} <= codes(rev)
    assert all(a["action"] for a in rev.report["required_corrective_actions"])


def test_simulated_assets_block(db, agent):
    v = make_video(db, agent)
    full_package(db, agent, v)
    a = assets.for_video(db, v.id)[0]
    a.simulated = True
    db.flush()
    assert review(db, agent, v).status == O.BLOCKED.value


def test_only_the_reviewer_role_can_run_reviews(db, agent):
    v = make_video(db, agent)
    for who in ("script-lyrics-writer", "production-manager", "research-manager"):
        with pytest.raises(PermissionDenied):
            og.run_review(db, agent(who), v.id)


# --- medicine -------------------------------------------------------------------------------------------------------------------


def medicine_video(db, agent, text_builder):
    v = make_video(db, agent, MED, "Understanding paracetamol")
    long_note = ("paracetamol should not be taken with other products containing paracetamol and the maximum daily dose must "
                 "not be exceeded in any twenty four hour period for adults")
    s = scripts.add_source(db, agent("research-specialist"), v.id, url="https://example.org/label", title="Label", publisher="Regulator",
                           retrieved_at=utcnow() - timedelta(days=1), note=long_note, supports="dosing limits")
    scripts.add_script(db, agent("script-lyrics-writer"), v.id, kind="script", text=text_builder(s.id, long_note), author_kind="human", author_label="t")
    add_brief(db, agent, v, musical_plan=None) if False else None
    return v, s


def test_a_medicine_video_needs_sources_and_cited_claims(db, agent):
    bare = make_video(db, agent, MED, "No sources")
    rev = review(db, agent, bare)
    assert {"NO_SOURCES", "NO_TEXT"} <= codes(rev) and "MEDICAL_REVIEW_SEPARATE" in codes(rev, "info")


def test_copying_a_source_verbatim_is_blocked_and_paraphrase_is_not(db, agent):
    v, _ = medicine_video(db, agent, lambda sid, note: f"{note.capitalize()}. [S{sid}]")
    assert "COPIED_FROM_SOURCE" in codes(review(db, agent, v), "block")
    v2, _ = medicine_video(db, agent, lambda sid, note: f"Check labels so you do not double up on the same medicine, and stay under the daily limit. [S{sid}]")
    assert not codes(review(db, agent, v2)) & {"COPIED_FROM_SOURCE", "CLOSE_TO_SOURCE"}


# --- verdict, staleness and the road to the queue ----------------------------------------------------------------------------------


def ready_video(db, agent):
    v = make_video(db, agent)
    full_package(db, agent, v)
    approve_metadata(db, agent, v)
    v.final_video_path = write_bytes_artifact(f"videos/{v.id}", "final.mp4", tiny_mp4(get_settings().artifacts_dir / "tmp.mp4").read_bytes())
    v.thumbnail_path = next(a.path for a in assets.for_video(db, v.id) if a.kind == "thumbnail")
    db.flush()
    pm = agent("production-manager")
    for st in (V.RESEARCH, V.BRIEF, V.SCRIPT, V.PRODUCTION, V.QA):
        videos.set_stage(db, pm, v.id, st)
    qa_task = verified_task(db, agent, "final-qa-specialist", "production-manager", "QA", evidence_name=f"qa{v.id}.txt")
    videos.record_qa(db, pm, v.id, True, qa_task_id=qa_task.id)
    videos.set_stage(db, pm, v.id, V.PACKAGING)
    return v


def test_the_whole_road_from_review_to_the_publishing_queue(db, agent):
    v = ready_video(db, agent)
    pm = agent("production-manager")
    rev = review(db, agent, v)
    assert rev.status == "PASS"
    with pytest.raises(gate.GateBlocked) as e:  # a review alone changes nothing
        videos.set_stage(db, pm, v.id, V.READY)
    assert "ORIGINALITY_NOT_REVIEWED" in e.value.result.codes()
    task = verified_task(db, agent, "copyright-originality-reviewer", "production-manager", "Rights review", evidence_name=f"rr{v.id}.txt")
    og.apply_verdict(db, agent("copyright-originality-reviewer"), v.id, rev.id, review_task_id=task.id)
    with pytest.raises(gate.GateBlocked) as e:
        videos.set_stage(db, pm, v.id, V.READY)
    assert e.value.result.codes() == ["CEO_RIGHTS_NOT_VERIFIED"]  # the CEO still has to inspect the evidence itself
    videos.ceo_verify_rights(db, agent("ceo"), v.id)
    videos.set_stage(db, pm, v.id, V.READY)
    assert db.get(Video, v.id).stage == V.READY.value


def test_a_review_that_is_out_of_date_cannot_be_applied(db, agent):
    v = make_video(db, agent)
    full_package(db, agent, v)
    rev = review(db, agent, v)
    task = verified_task(db, agent, "copyright-originality-reviewer", "production-manager", "Rights", evidence_name="rs.txt")
    add_scenes(db, agent, v, base_seed=777, n=1)  # something changed after the review
    with pytest.raises(og.StaleReview, match="changed after this review"):
        og.apply_verdict(db, agent("copyright-originality-reviewer"), v.id, rev.id, review_task_id=task.id)
    newer = review(db, agent, v)
    with pytest.raises(og.StaleReview, match="newer review"):
        og.apply_verdict(db, agent("copyright-originality-reviewer"), v.id, rev.id, review_task_id=task.id)
    og.apply_verdict(db, agent("copyright-originality-reviewer"), v.id, newer.id, review_task_id=task.id)


def test_a_blocking_verdict_pulls_a_queued_video_back_out(db, agent):
    v = ready_video(db, agent)
    pm = agent("production-manager")
    rev = review(db, agent, v)
    task = verified_task(db, agent, "copyright-originality-reviewer", "production-manager", "Rights", evidence_name="rb.txt")
    og.apply_verdict(db, agent("copyright-originality-reviewer"), v.id, rev.id, review_task_id=task.id)
    videos.ceo_verify_rights(db, agent("ceo"), v.id)
    videos.set_stage(db, pm, v.id, V.READY)
    # later the music is found to be unlicensed
    music = next(a for a in assets.for_video(db, v.id) if a.kind == "music")
    music.commercial_use = "no"
    db.flush()
    assert "ASSET_LICENSE_ISSUES" in gate.evaluate(db, db.get(Video, v.id)).codes()  # the live register overrides the old sign-off
    rev2 = review(db, agent, v)
    task2 = verified_task(db, agent, "copyright-originality-reviewer", "production-manager", "Rights again", evidence_name="rb2.txt")
    og.apply_verdict(db, agent("copyright-originality-reviewer"), v.id, rev2.id, review_task_id=task2.id)
    db.refresh(v)
    assert v.originality_status == "BLOCKED" and v.stage == V.PACKAGING.value and not v.ceo_rights_verified
    with pytest.raises(videos.VideoError):
        videos.ceo_verify_rights(db, agent("ceo"), v.id)
