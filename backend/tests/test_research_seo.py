"""Metadata and thumbnail concepts: drafting, checking, chapters from measured time, approval."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.enums import VideoStage
from app.models.production import Asset
from app.research import seo, thumbs
from app.research.seo import SeoError
from app.security.permissions import PermissionDenied
from app.services import videos
from app.timeutil import utcnow
from tests.prod_helpers import BDAY, MED, add_brief, add_lyrics, add_music, brief_content, make_video

TIMELINE = [  # a measured 62 s song: the intro is too short to be a chapter by itself
    {"section": "intro", "base": "intro", "bars": 4, "start_s": 0.0, "end_s": 9.2},
    {"section": "verse 1", "base": "verse", "bars": 8, "start_s": 9.2, "end_s": 28.0},
    {"section": "chorus", "base": "chorus", "bars": 8, "start_s": 28.0, "end_s": 47.0},
    {"section": "outro", "base": "outro", "bars": 6, "start_s": 47.0, "end_s": 62.0},
]
DESC = "A morning garden celebration with bells. A song they can sing along to.\n\nIf you enjoyed this, you can subscribe for more original birthday music."


def package(db, agent, channel=BDAY, title="Garden Morning Song", *, timeline=True, vocal="instrumental", **brief_over):
    v = make_video(db, agent, channel=channel, title=title)
    extras = {MED: {"source_requirements": "two sources, one authoritative", "safety_notes": "ask a doctor"},
              "ai-story-videos": {"premise": "a fox finds a lantern", "characters": ["Fox"]}}.get(channel, {})
    add_brief(db, agent, v, main_concept="A morning garden celebration with bells", style_tags=["garden", "sunrise"], tone_and_style="warm, playful",
              musical_plan={"vocal_delivery": vocal}, **{**extras, **brief_over})
    add_lyrics(db, agent, v)
    music, _ = add_music(db, agent, v)
    if timeline:
        music.params = {"timeline": TIMELINE, "duration_s": 62.0}
    return v


def ok_args(**over):
    base = dict(chosen_title="Garden Morning Music | Original Birthday Instrumental", description=DESC, tags=["happy birthday", "garden"], hashtags=["#HappyBirthday"],
                chapters=[], recommendation=seo.HYPOTHESIS)
    return {**base, **over}


def codes(db, video, **over):
    return {f["code"] for f in seo.check_content(db, video, **ok_args(**over))}


def test_a_clean_set_has_no_blocking_or_revision_findings(db, agent):
    v = package(db, agent)
    f = seo.check_content(db, v, **ok_args())
    assert [x for x in f if x["severity"] in ("block", "revise")] == []


@pytest.mark.parametrize("title,code,sev", [
    ("x" * 101, "TITLE_TOO_LONG", "block"),
    ("Garden <b>Morning</b> Song", "TITLE_ANGLE_BRACKETS", "block"),
    ("GARDEN MORNING SONG FOR EVERYONE", "TITLE_SHOUTING", "revise"),
    ("Garden Morning Song!!!", "TITLE_PUNCTUATION", "revise"),
    ("Garden Morning Song - watch now before it's deleted", "TITLE_FALSE_URGENCY", "revise"),
    ("Garden Morning Song You Won't Believe", "TITLE_CLICKBAIT", "revise"),
    ("Garden garden garden morning song", "TITLE_REPEATED_WORDS", "revise"),
    ("Garden Morning Song sung by a choir", "TITLE_PROMISES_VOCALS", "block"),
    ("Dragon Volcano Spaceship Song", "TITLE_NOT_SUPPORTED", "revise"),
])
def test_title_rules(db, agent, title, code, sev):
    v = package(db, agent)
    found = {f["code"]: f["severity"] for f in seo.assess_title(db, v, title)["findings"]}
    assert found.get(code) == sev


def test_a_long_title_is_flagged_for_display_but_not_blocked(db, agent):
    v = package(db, agent)
    found = {f["code"]: f["severity"] for f in seo.assess_title(db, v, "Garden Morning Song | An Original Birthday Music Piece With Bells And Sunrise Colours")["findings"]}
    assert found.get("TITLE_LONG_FOR_DISPLAY") == "info" and "TITLE_TOO_LONG" not in found


def test_an_instrumental_is_not_sold_as_a_song(db, agent):
    v = package(db, agent)
    assert {f["code"]: f["severity"] for f in seo.assess_title(db, v, "Garden Morning Song")["findings"]}["TITLE_SAYS_SONG"] == "revise"
    assert "TITLE_SAYS_SONG" not in {f["code"] for f in seo.assess_title(db, v, "Garden Morning Music")["findings"]}
    assert "TITLE_SAYS_SONG" not in {f["code"] for f in seo.assess_title(db, package(db, agent, vocal="sung", title="Another Song"), "Garden Morning Song")["findings"]}


def test_a_sung_plan_may_say_sung(db, agent):
    v = package(db, agent, vocal="sung")
    assert "TITLE_PROMISES_VOCALS" not in {f["code"] for f in seo.assess_title(db, v, "Garden Morning Song, sung with bells")["findings"]}


def test_accuracy_cannot_be_judged_without_a_brief_or_script(db, agent):
    v = make_video(db, agent, title="Garden Morning Song")
    a = seo.assess_title(db, v, "Garden Morning Song")
    assert "ACCURACY_UNASSESSED" in {f["code"] for f in a["findings"]} and a["criteria"]["accuracy_and_consistency"]["state"] == "unassessed"


def test_the_working_title_cannot_vouch_for_itself(db, agent):
    v = make_video(db, agent, title="Dragon Volcano Spaceship")
    add_brief(db, agent, v, main_concept="A morning garden celebration with bells")
    add_lyrics(db, agent, v)
    assert "TITLE_NOT_SUPPORTED" in {f["code"] for f in seo.assess_title(db, v, "Dragon Volcano Spaceship")["findings"]}


def test_title_criteria_say_what_is_unavailable(db, agent):
    v = package(db, agent)
    c = seo.assess_title(db, v, "Garden Morning Song | Original Birthday Music")["criteria"]
    assert c["search_intent"]["state"] == "unavailable" and c["historical_performance"]["state"] == "unavailable"
    assert c["accuracy_and_consistency"]["state"] == "ok" and c["distinctiveness"]["state"] == "ok"


def test_a_title_nearly_identical_to_an_existing_one_is_flagged(db, agent):
    v = package(db, agent)
    other = make_video(db, agent, title="Garden Morning Song | Original Birthday Music")
    other.title = other.working_title
    assert "TITLE_NEAR_DUPLICATE" in {f["code"] for f in seo.assess_title(db, v, "Garden Morning Song | Original Birthday Music")["findings"]}
    demo = make_video(db, agent, title="Garden Morning Song | Original Birthday Music 2")
    demo.metadata_json = {"demo": True}


def test_medicine_titles_cannot_promise_results(db, agent):
    v = package(db, agent, channel=MED, title="How aspirin works")
    for t, code, sev in (("Aspirin: a miracle that works 100%", "TITLE_MEDICAL_OVERCLAIM", "block"), ("Aspirin cures headaches", "TITLE_MEDICAL_WORDING", "revise")):
        assert {f["code"]: f["severity"] for f in seo.assess_title(db, v, t)["findings"]}.get(code) == sev
    birthday = package(db, agent)
    assert "TITLE_MEDICAL_OVERCLAIM" not in {f["code"] for f in seo.assess_title(db, birthday, "Garden Morning Song guaranteed")["findings"]}


@pytest.mark.parametrize("over,code", [
    ({"description": ""}, "DESCRIPTION_EMPTY"),
    ({"description": "é" * 2600}, "DESCRIPTION_TOO_LONG"),  # 5200 bytes though only 2600 characters
    ({"description": DESC + " <script>"}, "DESCRIPTION_ANGLE_BRACKETS"),
    ({"description": DESC + " This is the best performing birthday song and will go viral."}, "UNSUPPORTED_CLAIM"),
    ({"description": " ".join(["Garden"] * 12 + ["words"] * 40)}, "DESCRIPTION_KEYWORD_STUFFING"),
    ({"tags": ["garden", "Garden"]}, "TAGS_DUPLICATE"),
    ({"tags": ["a" * 100] * 6}, "TAGS_TOO_LONG"),
    ({"tags": ["bad <tag>"]}, "TAGS_ANGLE_BRACKETS"),
    ({"tags": ["garden song", "garden music", "garden tune", "garden bells", "garden morning"]}, "TAGS_REPETITIVE"),
    ({"tags": ["garden", "cryptocurrency"]}, "TAGS_UNSUPPORTED"),
    ({"hashtags": ["#Happy Birthday"]}, "HASHTAG_FORMAT"),
    ({"hashtags": [f"#Garden{i}" for i in range(16)]}, "HASHTAGS_TOO_MANY"),
    ({"hashtags": ["#Garden", "#Sunrise", "#HappyBirthday", "#Morning", "#Bells", "#Song"]}, "HASHTAGS_MANY"),
    ({"hashtags": ["#Garden", "#Bitcoin"]}, "HASHTAGS_IRRELEVANT"),
    ({"recommendation": "This title will outperform the others and is the best performing option."}, "RECOMMENDATION_CLAIMS_PERFORMANCE"),
    ({"recommendation": "Use the first title."}, "RECOMMENDATION_NOT_LABELLED"),
])
def test_description_tag_and_hashtag_rules(db, agent, over, code):
    v = package(db, agent)
    assert code in codes(db, v, **over)


def test_hashtags_may_use_camel_case_words_the_video_supports(db, agent):
    v = package(db, agent)
    assert not ({"HASHTAGS_IRRELEVANT"} & codes(db, v, hashtags=["#HappyBirthday", "#GardenMorning"]))


def test_a_medicine_description_needs_a_disclaimer_and_its_sources(db, agent, owner):
    from app.production import scripts
    v = package(db, agent, channel=MED, title="How aspirin works")
    base = dict(chosen_title="How aspirin works explained", tags=["health education"], hashtags=["#HealthEducation"], description="Aspirin is an antiplatelet drug.")
    assert {"MEDICAL_DISCLAIMER_MISSING", "NO_SOURCES"} <= codes(db, v, **base)
    scripts.add_source(db, agent("research-specialist"), v.id, url="https://www.cdc.gov/aspirin", title="CDC aspirin", publisher="CDC",
                       retrieved_at=utcnow() - timedelta(hours=1), note="", supports="Aspirin is an antiplatelet drug.")
    scripts.add_source(db, agent("research-specialist"), v.id, url="https://www.nhs.uk/aspirin", title="NHS aspirin", publisher="NHS",
                       retrieved_at=utcnow() - timedelta(hours=1), note="", supports="Ask a doctor first.")
    with_disclaimer = base["description"] + " " + seo.DISCLAIMER
    assert "SOURCES_NOT_LISTED" in codes(db, v, **{**base, "description": with_disclaimer})
    partial = with_disclaimer + " Source: https://www.cdc.gov/aspirin"
    assert "SOURCES_PARTLY_LISTED" in codes(db, v, **{**base, "description": partial})
    full = partial + " https://www.nhs.uk/aspirin"
    assert not ({"SOURCES_NOT_LISTED", "SOURCES_PARTLY_LISTED", "MEDICAL_DISCLAIMER_MISSING", "NO_SOURCES"} & codes(db, v, **{**base, "description": full}))
    assert "MEDICAL_OVERCLAIM" in codes(db, v, **{**base, "description": full + " Guaranteed to work."})


def test_story_and_narration_disclosures(db, agent):
    s = package(db, agent, channel="ai-story-videos", title="The Lantern Fox")
    base = dict(chosen_title="The Lantern Fox", tags=["ai story"], hashtags=["#AIStory"], description="A fox finds a lantern.")
    assert "AI_DISCLOSURE_MISSING" in codes(db, s, **base)
    assert "AI_DISCLOSURE_MISSING" not in codes(db, s, **{**base, "description": base["description"] + " " + seo.STORY_DISCLOSURE})
    v = package(db, agent)
    from app.production import assets
    nar = db.query(Asset).filter_by(video_id=v.id, kind="music").one()
    db.add(Asset(video_id=v.id, kind="narration", role="narration", path=nar.path, sha256=nar.sha256, size_bytes=nar.size_bytes, provider="elevenlabs",
                 license_type="provider-terms", commercial_use="yes", created_by="test"))
    db.flush()
    assert assets and "AI_DISCLOSURE_MISSING" in codes(db, v)
    assert "AI_DISCLOSURE_MISSING" not in codes(db, v, description=DESC + " The narration is AI-generated.")


# --- chapters -------------------------------------------------------------------------------------------------------


def test_chapters_come_only_from_measured_section_starts(db, agent):
    v = package(db, agent)
    ch, why = seo.chapters_from(seo.boundaries(db, v))
    assert why == "" and [c["start_s"] for c in ch] == [0.0, 28.0, 47.0]
    assert [c["title"] for c in ch] == ["Intro & Verse 1", "Chorus", "Outro"]  # the 9 s intro folded into the verse
    assert seo.chapters_text(ch).splitlines() == ["0:00 Intro & Verse 1", "0:28 Chorus", "0:47 Outro"]
    assert not [f for f in seo.check_chapters(ch, seo.boundaries(db, v)) if f["severity"] != "info"]


def test_no_chapters_without_a_measured_timeline_or_when_too_short(db, agent):
    no_tl = package(db, agent, timeline=False)
    assert seo.boundaries(db, no_tl)["kind"] == "unavailable"
    ch, why = seo.chapters_from(seo.boundaries(db, no_tl))
    assert ch == [] and "estimated" in why
    short = package(db, agent, title="Another Garden Song")
    Asset_ = db.query(Asset).filter_by(video_id=short.id, kind="music").one()
    Asset_.params = {"timeline": TIMELINE[:2], "duration_s": 25.0}
    ch, why = seo.chapters_from(seo.boundaries(db, short))
    assert ch == [] and "too short" in why


@pytest.mark.parametrize("chapters,code", [
    ([{"start_s": 0.0, "title": "A"}, {"start_s": 28.0, "title": "B"}], "CHAPTERS_TOO_FEW"),
    ([{"start_s": 2.0, "title": "A"}, {"start_s": 28.0, "title": "B"}, {"start_s": 47.0, "title": "C"}], "CHAPTERS_START"),
    ([{"start_s": 0.0, "title": "A"}, {"start_s": 31.0, "title": "B"}, {"start_s": 47.0, "title": "C"}], "CHAPTERS_NOT_MEASURED"),
    ([{"start_s": 0.0, "title": "A"}, {"start_s": 28.0, "title": "B"}, {"start_s": 47.0, "title": "C"}, {"start_s": 55.0, "title": "D"}], None),
    ([{"start_s": 0.0, "title": "A"}, {"start_s": 28.0, "title": "B"}, {"start_s": 28.0, "title": "C"}], "CHAPTERS_ORDER"),
])
def test_invented_chapters_are_blocked(db, agent, chapters, code):
    v = package(db, agent)
    found = {f["code"] for f in seo.check_chapters(chapters, seo.boundaries(db, v))}
    if code:
        assert code in found
    else:
        assert "CHAPTERS_NOT_MEASURED" in found  # 55 s is not a section start


def test_chapters_on_a_video_with_only_estimated_timing_are_blocked(db, agent):
    v = package(db, agent, timeline=False)
    found = seo.check_chapters([{"start_s": 0.0, "title": "A"}, {"start_s": 20.0, "title": "B"}, {"start_s": 40.0, "title": "C"}], seo.boundaries(db, v))
    assert [f["code"] for f in found] == ["CHAPTERS_TIMING_ESTIMATED"] and found[0]["severity"] == "block"


def test_chapters_must_appear_in_the_description(db, agent):
    v = package(db, agent)
    ch, _ = seo.chapters_from(seo.boundaries(db, v))
    assert "CHAPTERS_NOT_IN_DESCRIPTION" in codes(db, v, chapters=ch)
    assert "CHAPTERS_NOT_IN_DESCRIPTION" not in codes(db, v, chapters=ch, description=DESC + "\n\n" + seo.chapters_text(ch))


# --- drafting, versions and approval --------------------------------------------------------------------------------


def test_generate_drafts_a_checked_set_from_the_brief_and_labels_it_a_template(db, agent):
    v = package(db, agent)
    ms = seo.generate(db, agent("keyword-seo-specialist"), v.id)
    assert ms.version == 1 and ms.status == "CHECKED" and ms.generated_by == "template-v1"
    assert ms.chosen_title.startswith("Garden Morning Music") and "Song" not in ms.chosen_title  # an instrumental is not sold as a song
    assert [c["start_s"] for c in ms.chapters] == [0.0, 28.0, 47.0] and "0:28 Chorus" in ms.description
    assert ms.hashtags[0] == "#HappyBirthday" and "happy birthday" in ms.tags and "hypothesis" in ms.recommendation.lower()
    assert all(c["label"] == "hypothesis" and c["source"] == "template-v1" for c in ms.title_candidates) and len(ms.title_candidates) >= 2
    assert {"block": 0, "revise": 0}.items() <= seo.serialize(ms)["finding_counts"].items()


def test_a_sung_plan_drafts_song_titles(db, agent):
    v = package(db, agent, vocal="sung")
    ms = seo.generate(db, agent("keyword-seo-specialist"), v.id)
    assert any(t["title"].endswith("Original Birthday Song") for t in ms.title_candidates) and ms.chosen_title.startswith("Garden Morning Song")


def test_generate_needs_a_title(db, agent):
    v = make_video(db, agent, title="Untitled")
    v.working_title = ""
    with pytest.raises(SeoError):
        seo.generate(db, agent("keyword-seo-specialist"), v.id)


def test_new_versions_supersede_drafts_but_not_the_approved_set(db, agent):
    v = package(db, agent)
    a = seo.generate(db, agent("keyword-seo-specialist"), v.id)
    b = seo.generate(db, agent("keyword-seo-specialist"), v.id)
    assert (a.status, b.status, b.version) == ("SUPERSEDED", "CHECKED", 2)
    seo.approve(db, agent("research-manager"), b.id, note="fine")
    c = seo.generate(db, agent("keyword-seo-specialist"), v.id)
    assert (b.status, c.status) == ("APPROVED", "CHECKED") and seo.approved(db, v.id).id == b.id
    with pytest.raises(SeoError):
        seo.check(db, agent("keyword-seo-specialist"), b.id)


def test_approval_applies_the_set_to_the_video_and_records_who(db, agent):
    v = package(db, agent)
    ms = seo.generate(db, agent("keyword-seo-specialist"), v.id)
    seo.approve(db, agent("research-manager"), ms.id, note="accurate and plain")
    db.refresh(v)
    md = v.metadata_json
    assert v.title == ms.chosen_title and md["description"] == ms.description and md["metadata_set_id"] == ms.id and md["tags"] == ms.tags
    assert ms.approved_by == "agent:research-manager" and seo.matches_video(ms, v)
    v.title = "Edited afterwards"
    assert not seo.matches_video(ms, v)


def test_who_may_approve(db, agent, owner):
    v = package(db, agent)
    ms = seo.generate(db, agent("keyword-seo-specialist"), v.id)
    for who in ("keyword-seo-specialist", "production-manager", "creative-director"):
        with pytest.raises(PermissionDenied):
            seo.approve(db, agent(who), ms.id, note="ok")
    with pytest.raises(SeoError, match="reason"):
        seo.approve(db, agent("research-manager"), ms.id, note=" ")
    mine = seo.create_set(db, agent("research-manager"), v.id, **ok_args(), generated_by="manual")
    with pytest.raises(SeoError, match="author"):
        seo.approve(db, agent("research-manager"), mine.id, note="mine")
    assert seo.approve(db, agent("publishing-manager"), mine.id, note="publishing review").status == "APPROVED"
    other = seo.create_set(db, agent("keyword-seo-specialist"), v.id, **ok_args(), generated_by="manual")
    assert seo.approve(db, owner, other.id, note="owner").status == "APPROVED"


def test_blocking_findings_can_never_be_waived_and_revisions_need_a_reason(db, agent):
    v = package(db, agent)
    blocked = seo.create_set(db, agent("keyword-seo-specialist"), v.id, **ok_args(chosen_title="Garden <Morning> Song"))
    assert blocked.status == "NEEDS_REVISION"
    with pytest.raises(SeoError, match="cannot be waived"):
        seo.approve(db, agent("research-manager"), blocked.id, note="ok", waive={"TITLE_ANGLE_BRACKETS": "I insist"})
    shouting = seo.create_set(db, agent("keyword-seo-specialist"), v.id, **ok_args(chosen_title="GARDEN MORNING MUSIC FOR EVERYONE"))
    with pytest.raises(SeoError, match="unresolved"):
        seo.approve(db, agent("research-manager"), shouting.id, note="ok")
    with pytest.raises(SeoError, match="unresolved"):
        seo.approve(db, agent("research-manager"), shouting.id, note="ok", waive={"TITLE_SHOUTING": "  "})
    done = seo.approve(db, agent("research-manager"), shouting.id, note="house style", waive={"TITLE_SHOUTING": "brand style for this series"})
    assert done.status == "APPROVED" and any(f.get("waived") == "brand style for this series" for f in done.findings)


def test_approval_rechecks_instead_of_trusting_the_last_check(db, agent):
    v = package(db, agent)
    ms = seo.generate(db, agent("keyword-seo-specialist"), v.id)
    ms.description = ""  # edited after the check
    with pytest.raises(SeoError, match="DESCRIPTION_EMPTY|cannot be waived"):
        seo.approve(db, agent("research-manager"), ms.id, note="ok")


def test_metadata_is_frozen_once_scheduled(db, agent):
    v = package(db, agent)
    v.stage = VideoStage.SCHEDULED.value
    with pytest.raises(SeoError, match="scheduled"):
        seo.generate(db, agent("keyword-seo-specialist"), v.id)
    assert videos is not None


# --- thumbnail concepts ---------------------------------------------------------------------------------------------


def concept_args(**over):
    base = dict(overlay_text="Garden Morning", composition="Sun rising over a bright garden with lanterns, big title at the top",
                palette=["#FFFFFF", "#1B3A57"], relation_to_content="Shows the garden sunrise described in the lyrics")
    return {**base, **over}


def tcodes(db, v, **over):
    return {f["code"] for f in thumbs.check_concept(db, v, **concept_args(**over))}


def test_contrast_is_measured_not_guessed():
    assert thumbs.contrast_ratio("#000000", "#FFFFFF") == 21.0
    assert thumbs.contrast_ratio("#777777", "#888888") < 1.5


def test_a_clean_concept_has_nothing_to_fix(db, agent):
    v = package(db, agent)
    assert [f for f in thumbs.check_concept(db, v, **concept_args()) if f["severity"] != "info"] == []


@pytest.mark.parametrize("over,code", [
    ({"composition": ""}, "COMPOSITION_MISSING"),
    ({"relation_to_content": " "}, "RELATION_MISSING"),
    ({"overlay_text": "Garden morning song for a very happy day"}, "OVERLAY_TOO_LONG"),
    ({"overlay_text": "GARDEN PARTY NOW!"}, "OVERLAY_SHOUTING"),
    ({"overlay_text": "You won't believe this"}, "OVERLAY_CLICKBAIT"),
    ({"overlay_text": "Dragon Volcano"}, "OVERLAY_NOT_SUPPORTED"),
    ({"palette": ["#FFFFFF", "#F0F0F0"]}, "LOW_CONTRAST"),
    ({"palette": ["white", "#000000"]}, "PALETTE_INVALID"),
    ({"palette": ["#FFFFFF"]}, "CONTRAST_UNASSESSED"),
    ({"palette": ["#FFFFFF", "#000000", "#111111", "#222222", "#333333", "#444444"]}, "PALETTE_BUSY"),
])
def test_thumbnail_concept_rules(db, agent, over, code):
    v = package(db, agent)
    assert code in tcodes(db, v, **over)


def test_medicine_thumbnails_cannot_imply_results(db, agent):
    v = package(db, agent, channel=MED, title="How aspirin works")
    assert "THUMBNAIL_MEDICAL_OVERCLAIM" in tcodes(db, v, overlay_text="Miracle 100%", composition="x")
    assert "THUMBNAIL_BEFORE_AFTER" in tcodes(db, v, overlay_text="Aspirin", composition="a before and after comparison of two faces")
    assert "THUMBNAIL_BEFORE_AFTER" not in tcodes(db, package(db, agent), composition="a before and after of the garden")


def test_a_thumbnail_that_copies_a_recent_look_is_flagged_within_the_channel_only(db, agent):
    first = package(db, agent)
    c = thumbs.create(db, agent("thumbnail-designer"), first.id, **concept_args())
    thumbs.approve(db, agent("research-manager"), c.id, note="fine")
    nxt = package(db, agent, title="Garden Evening Song")
    assert "SIMILAR_TO_RECENT" in tcodes(db, nxt, overlay_text="Garden Evening")
    assert "SIMILAR_TO_RECENT" not in tcodes(db, nxt, composition="A crowd of paper boats on a night river, moon above", overlay_text="Garden Evening")
    assert "SIMILAR_TO_RECENT" not in tcodes(db, nxt, palette=["#FFFF00", "#800000"], overlay_text="Garden Evening")
    med = package(db, agent, channel=MED, title="Garden Evening Facts")
    assert "SIMILAR_TO_RECENT" not in tcodes(db, med, overlay_text="Garden")


def test_concepts_are_versioned_and_approval_follows_the_same_rules(db, agent):
    v = package(db, agent)
    a = thumbs.create(db, agent("thumbnail-designer"), v.id, **concept_args(palette=["#FFFFFF", "#F0F0F0"]))
    assert a.status == "NEEDS_REVISION"
    with pytest.raises(thumbs.ThumbError, match="unresolved"):
        thumbs.approve(db, agent("research-manager"), a.id, note="ok")
    b = thumbs.create(db, agent("thumbnail-designer"), v.id, **concept_args())
    assert (a.status, b.status, b.version) == ("SUPERSEDED", "CHECKED", 2)
    with pytest.raises(PermissionDenied):
        thumbs.approve(db, agent("thumbnail-designer"), b.id, note="ok")
    with pytest.raises(thumbs.ThumbError, match="author"):
        thumbs.approve(db, agent("research-manager"), thumbs.create(db, agent("research-manager"), v.id, **concept_args()).id, note="mine")
    c = thumbs.create(db, agent("thumbnail-designer"), v.id, **concept_args())
    assert thumbs.approve(db, agent("research-manager"), c.id, note="clear and accurate").status == "APPROVED" and thumbs.approved(db, v.id).id == c.id
    assert "no performance data" in thumbs.serialize(c)["note"]


def test_brief_fixture_is_valid():
    assert brief_content()["main_concept"]
