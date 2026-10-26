"""Real FFmpeg: render a video, then prove each QA check can *fail* on a deliberately bad file."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from app.production import captions as cap
from app.production import imaging, qa, synth
from app.production import render as rd
from tests.prod_helpers import HAVE_FFMPEG, tiny_mp4

pytestmark = pytest.mark.skipif(not HAVE_FFMPEG, reason="FFmpeg/ffprobe not installed")
SIZE = (320, 180)


def cfg(**kw) -> qa.QAConfig:
    return qa.QAConfig(expected_size=SIZE, **kw)


def by_id(checks):
    return {c["id"]: c for c in checks}


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    d = tmp_path_factory.mktemp("render")
    comp = synth.compose({"tempo_bpm": 120, "key": "C major", "seed": 2, "structure": ["intro", "verse", "chorus", "outro"],
                          "bars": {"intro": 1, "verse": 2, "chorus": 2, "outro": 1}})
    wav = comp.write_wav(d / "m.wav")
    tl = comp.timeline
    scenes = []
    for i, s in enumerate(tl):
        end = tl[i + 1]["start_s"] if i + 1 < len(tl) else comp.duration_s
        scenes.append(rd.Scene(imaging.make_scene(d / f"s{i}.png", size=SIZE, seed=40 + i, title=s["section"].title()), round(end - s["start_s"], 3)))
    scenes[-1].duration_s = round(comp.duration_s - sum(x.duration_s for x in scenes[:-1]), 3)
    cues = cap.align_lyrics(cap.parse_lyrics("[Verse]\nSunshine on the window pane\nA song is coming through\n[Chorus]\nCelebrate, celebrate\nThis day is shining new"), tl)
    ass = cap.write_ass(cues, d / "c.ass", SIZE)
    res = rd.render(rd.RenderSpec(scenes, wav, ass, size=SIZE, crossfade_s=0.4, preset="ultrafast"), d / "out.mp4")
    thumb = imaging.make_thumbnail(d / "t.png", title="Garden Morning", seed=9)
    return {"res": res, "dur": comp.duration_s, "thumb": thumb, "cues": cues, "dir": d, "comp": comp}


def test_a_good_render_passes_every_check(rendered):
    r = rendered
    checks = qa.run_checks(r["res"].output, cfg(expected_duration_s=r["dur"], title="Garden Morning Song", description="An original song.",
                                                   tags=["birthday"]), thumbnail=r["thumb"], cues=r["cues"], asset_problems=[])
    summary = qa.summarize(checks)
    assert summary["passed"], [c for c in checks if c["status"] == "fail"]
    ids = {c["id"] for c in checks}
    assert {"decode", "duration", "resolution", "codec", "audio_present", "loudness", "clipping", "silence", "av_sync", "black_frames",
            "opening", "ending", "captions", "thumbnail", "metadata", "records"} <= ids
    assert 0 < r["res"].output.stat().st_size and r["res"].total_s == pytest.approx(r["dur"], abs=0.01)


def test_render_command_is_real_and_uses_the_expected_filters(rendered):
    res = rendered["res"]
    cmd, audio = " ".join(res.command[1:]), " ".join(res.audio_command[1:])  # [0] is the FFmpeg program itself, whose path is platform-specific
    for piece in ("libx264", "zoompan", "xfade", "subtitles=subs.ass", "-movflags +faststart", "yuv420p"):
        assert piece in cmd
    for piece in ("loudnorm", "alimiter", "-c:a aac"):
        assert piece in audio
    assert "-c:a copy" in cmd  # the soundtrack that was measured is the one that goes into the video
    for text in (cmd, audio):
        assert "C:\\" not in text and "/tmp" not in text  # inputs are referenced by simple names, no platform paths


def test_the_soundtrack_in_the_finished_file_is_measured_and_under_the_peak_limit(rendered):
    res = rendered["res"]
    assert res.audio_true_peak_db is not None and res.audio_true_peak_db <= rd.PEAK_ACCEPT_DB
    assert by_id(qa.run_checks(res.output, cfg()))["clipping"]["status"] == "pass"


def test_audio_that_comes_out_too_hot_is_turned_down_and_encoded_again(tmp_path, monkeypatch):
    """The encoder can overshoot by a different amount on every FFmpeg version, so the level is corrected from what was measured."""
    wav = synth.compose({"tempo_bpm": 120, "key": "C major", "seed": 2, "structure": ["intro"], "bars": {"intro": 1}}).write_wav(tmp_path / "m.wav")
    img = imaging.make_scene(tmp_path / "s.png", size=SIZE, seed=1, title="A")
    spec = rd.RenderSpec([rd.Scene(img, 2.0)], wav, None, size=SIZE, preset="ultrafast")
    work = tmp_path / "w"
    work.mkdir()
    shutil.copyfile(wav, work / "audio.wav")
    readings = iter([1.2, -1.4])  # first take overshoots by 2.7 dB over the target, the second is fine
    monkeypatch.setattr(rd, "true_peak_db", lambda exe, path: next(readings))
    cmd, peak, trim = rd.encode_audio(spec, rd.ffmpeg_path(), work, timeout_s=120)
    assert peak == -1.4 and trim == pytest.approx(-(1.2 + 1.5) - 0.2, abs=0.01)
    assert f"volume={trim:.2f}dB" in " ".join(cmd)
    assert (work / rd.AUDIO_FILE).stat().st_size > 0


def test_audio_that_can_never_be_brought_under_the_limit_is_reported_not_hidden(tmp_path, monkeypatch):
    wav = synth.compose({"tempo_bpm": 120, "key": "C major", "seed": 2, "structure": ["intro"], "bars": {"intro": 1}}).write_wav(tmp_path / "m.wav")
    img = imaging.make_scene(tmp_path / "s.png", size=SIZE, seed=1, title="A")
    spec = rd.RenderSpec([rd.Scene(img, 2.0)], wav, None, size=SIZE, preset="ultrafast")
    work = tmp_path / "w"
    work.mkdir()
    shutil.copyfile(wav, work / "audio.wav")
    monkeypatch.setattr(rd, "true_peak_db", lambda exe, path: 3.0)
    _, peak, _ = rd.encode_audio(spec, rd.ffmpeg_path(), work, timeout_s=120)
    assert peak == 3.0  # it stops trying after a few passes; the quality check will then fail the video honestly


def test_a_missing_or_empty_file_fails_immediately(tmp_path):
    c = qa.run_checks(tmp_path / "nope.mp4", cfg())
    assert c[0]["status"] == "fail" and len(c) == 1
    (tmp_path / "empty.mp4").write_bytes(b"")
    assert qa.run_checks(tmp_path / "empty.mp4", cfg())[0]["status"] == "fail"


def test_a_corrupt_file_fails_decoding_and_stops(rendered, tmp_path):
    data = rendered["res"].output.read_bytes()
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(data[: len(data) // 3] + b"\x00" * 4000 + data[len(data) // 3 + 4000 :])
    checks = by_id(qa.run_checks(bad, cfg()))
    assert checks["decode"]["status"] == "fail" or "resolution" not in checks  # corruption must never pass silently
    assert not qa.summarize(list(checks.values()))["passed"]


def test_wrong_resolution_fails(tmp_path):
    v = tiny_mp4(tmp_path / "v.mp4", size="640x360")
    assert by_id(qa.run_checks(v, cfg()))["resolution"]["status"] == "fail"


def test_wrong_duration_fails(tmp_path):
    v = tiny_mp4(tmp_path / "v.mp4", seconds=3)
    assert by_id(qa.run_checks(v, cfg(expected_duration_s=10)))["duration"]["status"] == "fail"


def test_no_audio_fails_when_required_and_is_skipped_when_not(tmp_path):
    v = tiny_mp4(tmp_path / "v.mp4", audio=False)
    assert by_id(qa.run_checks(v, cfg()))["audio_present"]["status"] == "fail"
    assert by_id(qa.run_checks(v, cfg(require_audio=False)))["audio_present"]["status"] == "skipped"


def test_silent_audio_fails(tmp_path):
    v = tiny_mp4(tmp_path / "v.mp4", extra_a="volume=-90dB")
    c = by_id(qa.run_checks(v, cfg()))
    assert c["audio_level"]["status"] == "fail" and c["silence"]["status"] == "fail"


def test_clipped_loud_audio_fails(tmp_path):
    v = tiny_mp4(tmp_path / "v.mp4", extra_a="volume=24dB,alimiter=limit=1:level=disabled")
    c = by_id(qa.run_checks(v, cfg()))
    assert c["clipping"]["status"] == "fail" or c["loudness"]["status"] == "fail"


def test_a_long_silence_in_the_middle_fails(tmp_path):
    v = tiny_mp4(tmp_path / "v.mp4", seconds=8, extra_a="volume=enable='between(t,2,6)':volume=0")
    assert by_id(qa.run_checks(v, cfg()))["silence"]["status"] == "fail"


def test_an_unintended_black_screen_fails(tmp_path):
    v = tiny_mp4(tmp_path / "v.mp4", seconds=8, extra_v="drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='between(t,3,6)'")
    c = by_id(qa.run_checks(v, cfg()))
    assert c["black_frames"]["status"] == "fail"
    assert "3.0-6.0s" in c["black_frames"]["detail"]  # the exact black interval is reported


def test_a_blank_opening_fails(tmp_path):
    v = tiny_mp4(tmp_path / "v.mp4", seconds=6, extra_v="drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='lt(t,3)'")
    assert by_id(qa.run_checks(v, cfg()))["opening"]["status"] == "fail"


def test_a_frozen_picture_is_flagged(tmp_path):
    p = tmp_path / "still.png"
    imaging.make_scene(p, size=SIZE, seed=1, title="Frozen")
    out = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", "30", "-t", "9", "-i", str(p), "-f", "lavfi", "-i",
                    "sine=frequency=330:duration=9", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-af", "volume=-12dB", str(out)], check=True)
    assert by_id(qa.run_checks(out, cfg()))["frozen"]["status"] in ("warn", "fail")


def test_audio_and_video_of_different_length_fail_sync(tmp_path):
    out = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=4", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=9", "-af", "volume=-12dB", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)], check=True)
    assert by_id(qa.run_checks(out, cfg()))["av_sync"]["status"] == "fail"


def test_unreadable_captions_fail(tmp_path):
    v = tiny_mp4(tmp_path / "v.mp4", seconds=6)
    cues = [{"start": 0.0, "end": 0.4, "text": "a very long caption that nobody could possibly read in this time", "section": "s"}]
    assert by_id(qa.run_checks(v, cfg(), cues=cues))["captions"]["status"] == "fail"
    assert by_id(qa.run_checks(v, cfg(require_captions=True)))["captions"]["status"] == "fail"


def test_thumbnail_problems_fail(tmp_path):
    v = tiny_mp4(tmp_path / "v.mp4")
    assert by_id(qa.run_checks(v, cfg()))["thumbnail"]["status"] == "fail"  # none
    small = tmp_path / "small.png"
    imaging.make_thumbnail(small, title="x", seed=1, size=(640, 360))
    assert by_id(qa.run_checks(v, cfg(), thumbnail=small))["thumbnail"]["status"] == "fail"  # wrong size
    junk = tmp_path / "junk.png"
    junk.write_bytes(b"not an image")
    assert by_id(qa.run_checks(v, cfg(), thumbnail=junk))["thumbnail"]["status"] == "fail"


def test_metadata_problems_fail(tmp_path):
    v = tiny_mp4(tmp_path / "v.mp4")
    c = by_id(qa.run_checks(v, cfg(title="A" * 120, description="  ", tags=["t"] * 30)))
    assert c["metadata"]["status"] == "fail" and "title" in c["metadata"]["detail"] and "description" in c["metadata"]["detail"]
    c2 = by_id(qa.run_checks(v, cfg(title="BUY NOW BEST SONG EVER", description="ok")))
    assert "capitals" in c2["metadata"]["detail"]


def test_missing_provenance_records_fail_qa(tmp_path):
    v = tiny_mp4(tmp_path / "v.mp4")
    probs = [{"asset_id": "3", "role": "music", "code": "NO_LICENSE", "message": "No licence is recorded."}]
    assert by_id(qa.run_checks(v, cfg(), asset_problems=probs))["records"]["status"] == "fail"


def test_overall_result_fails_if_any_check_fails():
    ok = [{"id": "a", "status": "pass"}, {"id": "b", "status": "warn"}]
    assert qa.summarize(ok)["passed"] is True
    bad = ok + [{"id": "c", "status": "fail"}]
    s = qa.summarize(bad)
    assert s["passed"] is False and s["failed"] == ["c"]


# --- render input validation ------------------------------------------------------------------------------------------------


def test_render_refuses_bad_specs(tmp_path):
    img = imaging.make_scene(tmp_path / "a.png", size=SIZE, seed=1)
    wav = synth.compose({"seed": 1, "structure": ["intro"], "bars": {"intro": 1}}).write_wav(tmp_path / "a.wav")
    with pytest.raises(rd.RenderError, match="at least one scene"):
        rd.render(rd.RenderSpec([], wav, size=SIZE), tmp_path / "o.mp4")
    with pytest.raises(rd.RenderError, match="shorter than the crossfade"):
        rd.render(rd.RenderSpec([rd.Scene(img, 0.5), rd.Scene(img, 3)], wav, size=SIZE, crossfade_s=0.8), tmp_path / "o.mp4")
    with pytest.raises(rd.RenderError, match="even"):
        rd.render(rd.RenderSpec([rd.Scene(img, 3)], wav, size=(321, 180)), tmp_path / "o.mp4")
    with pytest.raises(rd.RenderError, match="missing"):
        rd.render(rd.RenderSpec([rd.Scene(tmp_path / "gone.png", 3)], wav, size=SIZE), tmp_path / "o.mp4")


def test_render_without_ffmpeg_gives_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda _n: None)
    with pytest.raises(rd.RenderError, match="winget install"):
        rd.ffmpeg_path()


def test_vertical_and_single_scene_videos_render(tmp_path):
    size = (180, 320)
    img = imaging.make_scene(tmp_path / "a.png", size=size, seed=3, title="Short")
    wav = synth.compose({"seed": 1, "structure": ["intro", "verse"], "bars": {"intro": 1, "verse": 1}}).write_wav(tmp_path / "a.wav")
    out = rd.render(rd.RenderSpec([rd.Scene(img, 4.0)], wav, size=size, preset="ultrafast", fade_in_s=0.2, fade_out_s=0.5), tmp_path / "v.mp4").output
    info = qa.probe(out)
    vs = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (vs["width"], vs["height"]) == size
