from __future__ import annotations

from datetime import timedelta

import pytest

from app.config import get_settings
from app.models.videos import Video
from app.production import assets, briefs, scripts, synth
from app.production import captions as cap
from app.production import similarity as sim
from app.security.permissions import PermissionDenied
from app.services import gate
from app.storage import resolve_data_path
from app.timeutil import utcnow
from tests.prod_helpers import (
    BDAY,
    MED,
    add_brief,
    add_lyrics,
    add_music,
    add_scenes,
    brief_content,
    full_package,
    make_video,
)

# --- similarity ------------------------------------------------------------------------------------------------------


def test_identical_and_unrelated_text():
    a = "Morning light is on the hill today and paper hats are on the way"
    assert sim.compare(a, a)["containment_in_other"] == 1.0
    c = sim.compare(a, "Take one tablet with water after a meal and keep out of reach of children")
    assert c["containment_in_other"] == 0 and c["longest_common_run_words"] <= 1


def test_a_few_swapped_words_or_reordered_lines_are_still_caught():
    original = "Morning light is on the hill today\nPaper hats and lanterns on the way\nSpin around the garden starts to glow"
    swapped = "Morning light is on the hill tonight\nPaper hats and lanterns on the road\nSpin around the garden starts to shine"
    reordered = "Spin around the garden starts to glow\nMorning light is on the hill today\nPaper hats and lanterns on the way"
    assert sim.compare(swapped, original)["near_duplicate_line_share"] == 1.0
    assert sim.compare(reordered, original)["near_duplicate_line_share"] == 1.0
    assert sim.compare(reordered, original)["containment_in_other"] > 0.6


def test_markers_and_punctuation_do_not_hide_copying():
    assert sim.normalize("Hello,   WORLD! [S1,S2]") == "hello world"
    assert sim.normalize("It’s  a [framing] day") == "it's a day"


def test_structure_signature_ignores_title_but_not_shape():
    a = brief_content()
    b = brief_content(main_concept="something else entirely", video_objective="different")
    assert sim.same_shape(sim.structure_signature(a), sim.structure_signature(b))
    c = brief_content(musical_plan={"tempo_bpm": 150})
    assert not sim.same_shape(sim.structure_signature(a), sim.structure_signature(c))


# --- assets -----------------------------------------------------------------------------------------------------------


def test_asset_registration_records_origin_and_hash(db, agent):
    v = make_video(db, agent)
    a, _ = add_music(db, agent, v)
    assert len(a.sha256) == 64 and a.size_bytes > 0 and a.seed == 3 and a.provider == "local-procedural"
    assert assets.problems_for(a) == []


def test_commercial_use_claim_needs_a_licence_reference(db, agent):
    v = make_video(db, agent)
    p = get_settings().artifacts_dir / "x.png"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"png-bytes")
    with pytest.raises(assets.AssetError, match="licence reference"):
        assets.register(db, agent("image-generation-specialist"), video_id=v.id, kind="image", role="s", path=p, provider="local-procedural",
                        license_type="owner-generated", commercial_use="yes", license_ref="  ")
    with pytest.raises(assets.AssetError, match="prompt, generation id or source URL"):
        assets.register(db, agent("image-generation-specialist"), video_id=v.id, kind="image", role="s", path=p, provider="some-ai",
                        license_type="provider-terms", commercial_use="unknown")


def test_missing_licence_or_unconfirmed_commercial_use_is_a_problem(db, agent):
    v = make_video(db, agent)
    a, _ = add_music(db, agent, v, license_type="unknown", commercial="unknown")
    codes = {p["code"] for p in assets.problems_for(a)}
    assert {"NO_LICENSE", "COMMERCIAL_USE_UNCONFIRMED"} <= codes


def test_changing_a_file_after_registration_voids_its_provenance(db, agent):
    v = make_video(db, agent)
    a, _ = add_music(db, agent, v)
    resolve_data_path(a.path).write_bytes(b"swapped for something else")
    assert [p["code"] for p in assets.problems_for(a)] == ["FILE_CHANGED"]


def test_simulated_assets_can_never_be_clean(db, agent):
    v = make_video(db, agent)
    p = get_settings().artifacts_dir / "fake.png"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"stand-in")
    a = assets.register(db, agent("image-generation-specialist"), video_id=v.id, kind="image", role="s", path=p, provider="local-procedural",
                        license_type="owner-generated", license_ref="x", commercial_use="yes", simulated=True)
    assert "SIMULATED" in [x["code"] for x in assets.problems_for(a)]


def test_bundled_font_is_a_licensed_resource(db, agent):
    v = make_video(db, agent)
    a = assets.register(db, agent("subtitle-caption-specialist"), video_id=v.id, kind="font", role="caption-font",
                        path="resource:fonts/Inter-Bold.otf", provider="bundled", license_type="ofl-1.1",
                        license_ref="resource:fonts/OFL-Inter.txt (SIL Open Font License 1.1)", commercial_use="yes")
    assert assets.problems_for(a) == []
    with pytest.raises(Exception, match="escapes"):
        assets.resolve_asset_path("resource:../../../etc/passwd")


def test_gate_sees_unlicensed_assets_even_if_the_stored_count_is_zero(db, agent):
    v = make_video(db, agent)
    add_music(db, agent, v, license_type="unknown", commercial="unknown")
    assert v.asset_license_issues == 0
    assert "ASSET_LICENSE_ISSUES" in gate.evaluate(db, db.get(Video, v.id)).codes()


def test_only_workers_with_task_permission_register_assets(db, agent, viewer):
    v = make_video(db, agent)
    p = get_settings().artifacts_dir / "y.png"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    with pytest.raises(PermissionDenied):
        assets.register(db, viewer, video_id=v.id, kind="image", role="s", path=p, provider="local-procedural",
                        license_type="owner-generated", commercial_use="unknown")


# --- briefs -------------------------------------------------------------------------------------------------------------


def test_incomplete_brief_is_rejected_with_every_problem_listed(db, agent):
    v = make_video(db, agent)
    with pytest.raises(briefs.BriefError) as e:
        briefs.create(db, agent("creative-director"), v.id, {"video_objective": "x"})
    for f in ("target_audience", "distinctive_elements", "musical_plan", "success_criteria"):
        assert f in str(e.value)


def test_birthday_brief_validates_the_musical_plan(db, agent):
    v = make_video(db, agent)
    with pytest.raises(briefs.BriefError, match="tempo_bpm"):
        briefs.create(db, agent("creative-director"), v.id, brief_content(musical_plan={"tempo_bpm": 999}))
    with pytest.raises(briefs.BriefError, match="vocal_delivery"):
        briefs.create(db, agent("creative-director"), v.id, brief_content(musical_plan={"vocal_delivery": "yodel"}))


def test_brief_review_is_the_creative_directors_managers_job(db, agent):
    v = make_video(db, agent)
    b = briefs.create(db, agent("creative-director"), v.id, brief_content())
    assert b.status == "DRAFT" and b.board_version_used is not None
    with pytest.raises(briefs.BriefError):
        briefs.require_approved(db, v.id)
    with pytest.raises(PermissionDenied):
        briefs.review(db, agent("creative-director"), b.id, approve=True)  # cannot approve its own work
    with pytest.raises(PermissionDenied):
        briefs.review(db, agent("research-manager"), b.id, approve=True)  # wrong department
    with pytest.raises(briefs.BriefError, match="notes"):
        briefs.review(db, agent("production-manager"), b.id, approve=False)
    briefs.review(db, agent("production-manager"), b.id, approve=True)
    assert briefs.require_approved(db, v.id).id == b.id


def test_the_brief_records_the_board_version_in_force(db, agent):
    from app.enums import BoardCategory as C
    from app.enums import BoardStatus as S
    from app.enums import Confidence
    from app.services import board

    e = board.propose(db, agent("continuous-improvement-scientist"), title="Intros with bells hold viewers", description="d",
                      category=C.CREATIVE, channel_slug=BDAY, confidence=Confidence.HIGH)
    board.transition(db, agent("analytics-manager"), e.id, S.UNDER_INVESTIGATION, "x")
    board.add_evidence(db, agent("continuous-improvement-scientist"), e.id, kind=board.EvidenceKind.NOTE, ref="n")
    from tests.helpers import make_file

    board.add_evidence(db, agent("continuous-improvement-scientist"), e.id, kind=board.EvidenceKind.FILE, ref=make_file("e.json"))
    board.transition(db, agent("ceo"), e.id, S.VERIFIED)
    board.transition(db, agent("ceo"), e.id, S.ACTIVE_INSTRUCTION)
    v = make_video(db, agent)
    b = briefs.create(db, agent("creative-director"), v.id, brief_content())
    assert b.board_version_used == board.current_release_version(db) >= 1


# --- sources and scripts -------------------------------------------------------------------------------------------------


def test_sources_need_a_real_url_a_past_date_and_a_claim(db, agent):
    v = make_video(db, agent, MED, "Understanding paracetamol")
    w = agent("research-specialist")
    ok = dict(title="Label", publisher="Regulator", retrieved_at=utcnow() - timedelta(days=1), note="n", supports="max daily dose")
    with pytest.raises(scripts.ScriptError, match="http"):
        scripts.add_source(db, w, v.id, url="javascript:alert(1)", **ok)
    with pytest.raises(scripts.ScriptError, match="future"):
        scripts.add_source(db, w, v.id, url="https://example.org/x", **{**ok, "retrieved_at": utcnow() + timedelta(days=1)})
    with pytest.raises(scripts.ScriptError, match="claim"):
        scripts.add_source(db, w, v.id, url="https://example.org/x", **{**ok, "supports": " "})
    assert scripts.add_source(db, w, v.id, url="https://example.org/x", **ok).id


def test_medicine_script_needs_a_source_marker_on_every_sentence(db, agent):
    v = make_video(db, agent, MED, "Understanding paracetamol")
    w = agent("research-specialist")
    s1 = scripts.add_source(db, w, v.id, url="https://example.org/label", title="Label", publisher="Regulator",
                            retrieved_at=utcnow() - timedelta(days=1), note="adult dose", supports="adult dosing")
    writer = agent("script-lyrics-writer")
    bad = f"Paracetamol is used for pain. [S{s1.id}] It never causes problems."
    with pytest.raises(scripts.ScriptError, match="no source marker"):
        scripts.add_script(db, writer, v.id, kind="script", text=bad, author_kind="human", author_label="t")
    with pytest.raises(scripts.ScriptError, match="unknown source"):
        scripts.add_script(db, writer, v.id, kind="script", text="Paracetamol treats pain. [S9999]", author_kind="human", author_label="t")
    good = f"Welcome to the channel. [framing] Paracetamol is used for pain and fever. [S{s1.id}] Ask a pharmacist if unsure. [framing]"
    sv = scripts.add_script(db, writer, v.id, kind="script", text=good, author_kind="human", author_label="t")
    assert len(sv.claims) == 1 and sv.claims[0]["source_ids"] == [s1.id]
    assert "[S" not in scripts.spoken_text(good)


def test_other_channels_do_not_require_citations(db, agent):
    v = make_video(db, agent)
    sv = add_lyrics(db, agent, v)
    assert sv.claims == [] and sv.artifact_path and resolve_data_path(sv.artifact_path).is_file()


def test_script_versions_increment_and_are_kept(db, agent):
    v = make_video(db, agent)
    a, b = add_lyrics(db, agent, v), add_lyrics(db, agent, v, "[Verse]\nA different line entirely here")
    assert (a.version, b.version) == (1, 2) and scripts.latest(db, v.id, "lyrics").id == b.id


# --- synth and captions ------------------------------------------------------------------------------------------------------


def test_composition_is_deterministic_and_seeds_differ():
    spec = {"tempo_bpm": 110, "key": "A minor", "seed": 4, "structure": ["intro", "verse", "chorus"], "bars": {"verse": 2, "chorus": 2, "intro": 1}}
    a, b, c = synth.compose(spec), synth.compose(spec), synth.compose({**spec, "seed": 5})
    assert (a.samples == b.samples).all() and not (a.samples == c.samples).all()
    assert a.meta["vocals"] is False and "no vocals" in a.meta["limitations"]


def test_composition_timeline_and_levels():
    c = synth.compose({"tempo_bpm": 120, "key": "C major", "seed": 1, "structure": ["intro", "verse", "chorus", "outro"],
                       "bars": {"intro": 1, "verse": 2, "chorus": 2, "outro": 1}})
    bar = 2.0  # 120 bpm, 4/4
    assert [t["bars"] for t in c.timeline] == [1, 2, 2, 1]
    assert c.timeline[1]["start_s"] == pytest.approx(bar) and c.timeline[-1]["end_s"] == pytest.approx(6 * bar)
    assert c.duration_s > 6 * bar and abs(c.samples).max() <= 0.9
    assert c.samples.shape[1] == 2 and abs(c.samples).max() > 0.3


def test_unsupported_key_or_tempo_is_refused():
    with pytest.raises(synth.SynthError):
        synth.compose({"key": "H lydian"})
    with pytest.raises(synth.SynthError):
        synth.compose({"tempo_bpm": 10})


def test_lyrics_align_to_the_matching_musical_sections():
    c = synth.compose({"tempo_bpm": 120, "key": "C major", "seed": 1, "structure": ["intro", "verse", "chorus", "outro"],
                       "bars": {"intro": 1, "verse": 2, "chorus": 2, "outro": 1}})
    sections = cap.parse_lyrics("[Verse]\nOne two three\nFour five six\n[Chorus]\nSing it out loud\nSing it once more")
    cues = cap.align_lyrics(sections, c.timeline)
    verse, chorus = c.timeline[1], c.timeline[2]
    assert all(verse["start_s"] <= q["start"] < q["end"] <= verse["end_s"] for q in cues[:2])
    assert all(chorus["start_s"] <= q["start"] < q["end"] <= chorus["end_s"] for q in cues[2:])
    assert cap.validate_cues(cues, c.duration_s) == []
    with pytest.raises(cap.CaptionError, match="no 'bridge' section"):
        cap.align_lyrics(cap.parse_lyrics("[Bridge]\nan unplanned section line"), c.timeline)


def test_caption_readability_rules_flag_problems():
    fast = [{"start": 0.0, "end": 1.0, "text": " ".join(["wordy"] * 28), "section": "s"}]
    codes = {i["code"] for i in cap.validate_cues(fast, 10)}
    assert {"TOO_FAST", "TOO_MANY_LINES"} <= codes
    overlapping = [{"start": 0.0, "end": 3.0, "text": "one two three", "section": "s"}, {"start": 2.0, "end": 5.0, "text": "four five six", "section": "s"}]
    assert "OVERLAP" in {i["code"] for i in cap.validate_cues(overlapping, 10)}
    assert "PAST_END" in {i["code"] for i in cap.validate_cues([{"start": 9.0, "end": 12.0, "text": "late caption", "section": "s"}], 10)}
    assert "TOO_SHORT" in {i["code"] for i in cap.validate_cues([{"start": 0.0, "end": 0.3, "text": "blink and gone", "section": "s"}], 10)}


def test_estimated_narration_timing_is_labelled_as_an_estimate():
    cues = cap.align_text_estimated("First sentence here. Second one is a bit longer than the first. Third.", 0, 12)
    assert all(c["timing"] == "estimated-from-text-length" for c in cues) and cues[-1]["end"] <= 12


def test_srt_and_ass_roundtrip(tmp_path):
    cues = [{"start": 0.5, "end": 3.2, "text": "Hello there", "section": "v"}, {"start": 3.5, "end": 6.0, "text": "A second line", "section": "v"}]
    srt = cap.write_srt(cues, tmp_path / "a.srt")
    back = cap.read_srt(srt)
    assert [round(c["start"], 2) for c in back] == [0.5, 3.5] and back[1]["text"] == "A second line"
    ass = cap.write_ass(cues, tmp_path / "a.ass", (1280, 720)).read_text()
    assert "PlayResY: 720" in ass and "Dialogue: 0,0:00:00.50,0:00:03.20" in ass and "Fontname" in ass


def test_full_package_helper_builds_a_complete_set_of_licensed_assets(db, agent):
    v = make_video(db, agent)
    full_package(db, agent, v)
    assert assets.license_problems(db, v.id) == []
    assert {a.kind for a in assets.for_video(db, v.id)} == {"music", "image", "thumbnail"}
    add_scenes(db, agent, v, base_seed=900, n=1)
    add_brief(db, agent, v, approve=False)
