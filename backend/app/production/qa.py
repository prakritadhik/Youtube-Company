"""Automated quality assurance for a rendered video, using ffprobe/ffmpeg measurements.

Every check reports ``pass``, ``warn``, ``fail`` or ``skipped`` with the measured value. Nothing passes silently:
a check that could not run is ``skipped`` with the reason, and the overall result is only ``passed`` when no
check failed and no required check was skipped.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.production import captions as cap
from app.production.imaging import luminance_stats


class QAError(RuntimeError):
    pass


@dataclass
class QAConfig:
    expected_size: tuple[int, int] = (1920, 1080)
    expected_fps: float = 30.0
    expected_duration_s: float | None = None
    duration_tolerance_s: float = 1.0
    loudness_lufs: tuple[float, float] = (-20.0, -12.0)
    max_true_peak_db: float = -0.5
    max_silence_s: float = 2.0
    max_black_s: float = 0.5
    max_freeze_s: float = 6.0
    av_sync_tolerance_s: float = 0.15
    require_audio: bool = True
    require_captions: bool = False
    thumbnail_size: tuple[int, int] = (1280, 720)
    title: str | None = None
    description: str | None = None
    tags: list[str] = field(default_factory=list)


def _tool(name: str) -> str:
    exe = shutil.which(name)
    if exe is None:
        raise QAError(f"{name} was not found; install FFmpeg")
    return exe


def probe(path: Path) -> dict[str, Any]:
    out = subprocess.run([_tool("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True, timeout=120, check=False)
    if out.returncode != 0:
        raise QAError(f"ffprobe could not read the file: {out.stderr.strip()[:300]}")
    return json.loads(out.stdout)


def _ff(args: list[str], timeout: int = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run([_tool("ffmpeg"), "-hide_banner", "-nostdin", *args], capture_output=True, text=True,
                          timeout=timeout, check=False)


def _check(cid: str, name: str, status: str, detail: str, measured: Any = None, correction: str = "") -> dict[str, Any]:
    return {"id": cid, "name": name, "status": status, "detail": detail, "measured": measured, "correction": correction}


def _fraction(s: str) -> float:
    a, _, b = s.partition("/")
    return float(a) / float(b) if b and float(b) else float(a)


def run_checks(video: Path, cfg: QAConfig, *, thumbnail: Path | None = None, cues: list[dict[str, Any]] | None = None,
               asset_problems: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    add = checks.append

    # 1. file exists
    if not video.is_file() or video.stat().st_size == 0:
        return [_check("file", "Video file exists", "fail", "The video file is missing or empty.", None, "re-render")]
    add(_check("file", "Video file exists", "pass", f"{video.stat().st_size:,} bytes", video.stat().st_size))

    # 2. decodes cleanly
    dec = _ff(["-v", "error", "-i", str(video), "-f", "null", "-"])
    if dec.returncode != 0 or dec.stderr.strip():
        add(_check("decode", "Decodes without errors", "fail", (dec.stderr.strip() or f"exit {dec.returncode}")[:300], None, "re-render"))
        return checks  # nothing else is trustworthy
    add(_check("decode", "Decodes without errors", "pass", "Every frame decoded."))

    info = probe(video)
    vs = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    au = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    dur = float(info["format"].get("duration", 0))

    # 3. duration
    if cfg.expected_duration_s is not None:
        delta = abs(dur - cfg.expected_duration_s)
        add(_check("duration", "Duration matches the plan", "pass" if delta <= cfg.duration_tolerance_s else "fail",
                   f"{dur:.2f}s vs expected {cfg.expected_duration_s:.2f}s", round(dur, 3), "re-render with matching scene timing"))
    else:
        add(_check("duration", "Duration is valid", "pass" if dur >= 3 else "fail", f"{dur:.2f}s", round(dur, 3)))

    # 4. resolution / codec / frame rate
    if vs is None:
        add(_check("video_stream", "Has a video stream", "fail", "No video stream found.", None, "re-render"))
        return checks
    size = (int(vs["width"]), int(vs["height"]))
    add(_check("resolution", "Resolution is correct", "pass" if size == cfg.expected_size else "fail",
               f"{size[0]}x{size[1]} (expected {cfg.expected_size[0]}x{cfg.expected_size[1]})", list(size), "re-render at the right size"))
    fps = _fraction(vs.get("avg_frame_rate", "0/1"))
    ok_codec = vs.get("codec_name") == "h264" and vs.get("pix_fmt") == "yuv420p"
    add(_check("codec", "H.264 / yuv420p (YouTube-friendly)", "pass" if ok_codec else "fail",
               f"{vs.get('codec_name')} {vs.get('pix_fmt')} at {fps:.2f} fps", {"codec": vs.get("codec_name"), "fps": round(fps, 3)}, "re-encode"))
    if abs(fps - cfg.expected_fps) > 0.5:
        add(_check("fps", "Frame rate", "warn", f"{fps:.2f} fps (expected {cfg.expected_fps})", round(fps, 3)))

    # 5. audio present / levels / silence / sync
    if au is None:
        add(_check("audio_present", "Audio track present", "fail" if cfg.require_audio else "skipped",
                   "No audio stream." if cfg.require_audio else "Audio not required.", None, "add the audio track and re-render"))
    else:
        add(_check("audio_present", "Audio track present", "pass", f"{au.get('codec_name')} {au.get('sample_rate')} Hz"))
        ana = _ff(["-i", str(video), "-vn", "-af", f"volumedetect,silencedetect=n=-50dB:d={cfg.max_silence_s},ebur128=peak=true",
                   "-f", "null", "-"])
        err = ana.stderr
        mean = re.search(r"mean_volume:\s*(-?[\d.]+) dB", err)
        mx = re.search(r"max_volume:\s*(-?[\d.]+) dB", err)
        if mean and float(mean.group(1)) < -45:
            add(_check("audio_level", "Audio is audible", "fail", f"mean volume {mean.group(1)} dB", float(mean.group(1)), "fix the audio mix"))
        elif mean:
            add(_check("audio_level", "Audio is audible", "pass", f"mean volume {mean.group(1)} dB", float(mean.group(1))))
        lufs = re.findall(r"I:\s+(-?[\d.]+) LUFS", err)
        tp = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", err)
        if lufs:
            i = float(lufs[-1])
            lo, hi = cfg.loudness_lufs
            add(_check("loudness", "Integrated loudness in range", "pass" if lo <= i <= hi else "fail",
                       f"{i:.1f} LUFS (target {lo}..{hi})", i, "normalise the audio"))
        if tp:
            p = float(tp[-1])
            add(_check("clipping", "No clipping (true peak)", "pass" if p <= cfg.max_true_peak_db else "fail",
                       f"true peak {p:.1f} dBFS (limit {cfg.max_true_peak_db})", p, "lower the level / limiter"))
        elif mx:
            m = float(mx.group(1))
            add(_check("clipping", "No clipping (peak)", "pass" if m <= -0.1 else "fail", f"peak {m} dB", m, "lower the level"))
        sil = [float(x) for x in re.findall(r"silence_duration:\s*([\d.]+)", err)]
        add(_check("silence", "No awkward long silences", "pass" if not sil else "fail",
                   f"none longer than {cfg.max_silence_s:.1f}s" if not sil else f"silent for {', '.join(f'{s:.1f}s' for s in sil)}",
                   sil, "fill or trim the silent section"))
        adur = float(au.get("duration") or dur)
        vdur = float(vs.get("duration") or dur)
        add(_check("av_sync", "Audio and video lengths agree", "pass" if abs(adur - vdur) <= cfg.av_sync_tolerance_s else "fail",
                   f"video {vdur:.2f}s / audio {adur:.2f}s", {"video": round(vdur, 3), "audio": round(adur, 3)}, "re-render so the tracks end together"))

    # 6. black / frozen frames
    vid = _ff(["-i", str(video), "-an", "-vf", f"blackdetect=d={cfg.max_black_s}:pix_th=0.10,freezedetect=n=-60dB:d={cfg.max_freeze_s}",
               "-f", "null", "-"])
    blacks = [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", vid.stderr)]
    edge = 1.2  # fade-in/out from black at the very start and end is intentional
    bad_black = [(a, b) for a, b in blacks if not (a < 0.05 and b <= edge) and not (b >= dur - 0.05 and a >= dur - edge)]
    add(_check("black_frames", "No unintended black screens", "pass" if not bad_black else "fail",
               "none" if not bad_black else ", ".join(f"{a:.1f}-{b:.1f}s" for a, b in bad_black), bad_black, "fix the scene that renders black"))
    frozen = [float(x) for x in re.findall(r"freeze_duration:\s*([\d.]+)", vid.stderr)]
    add(_check("frozen", "No frozen sections", "pass" if not frozen else "warn",
               "none" if not frozen else f"frozen for {', '.join(f'{f:.1f}s' for f in frozen)}", frozen))

    # 7. opening and ending
    with tempfile.TemporaryDirectory() as td:
        shot = Path(td) / "open.png"
        _ff(["-ss", "1.0", "-i", str(video), "-frames:v", "1", str(shot), "-y"])
        if shot.is_file():
            st = luminance_stats(shot)
            add(_check("opening", "Opening frame is not blank", "pass" if st["mean"] > 12 and st["stddev"] > 6 else "fail",
                       f"mean luminance {st['mean']:.0f}, contrast {st['stddev']:.0f}", st, "fix the opening scene"))
        else:
            add(_check("opening", "Opening frame is not blank", "fail", "Could not extract a frame at 1 s.", None, "re-render"))
        endshot = Path(td) / "end.png"
        _ff(["-ss", f"{max(dur - 2.0, 0):.2f}", "-i", str(video), "-frames:v", "1", str(endshot), "-y"])
        add(_check("ending", "Ending is complete", "pass" if endshot.is_file() and (cfg.expected_duration_s is None or dur >= cfg.expected_duration_s - 0.5) else "fail",
                   "last seconds decode and the length matches the plan" if endshot.is_file() else "could not read the last seconds",
                   round(dur, 2), "re-render the ending"))

    # 8. captions
    if cues is None:
        add(_check("captions", "Captions are readable and timed", "fail" if cfg.require_captions else "skipped",
                   "No captions supplied." if cfg.require_captions else "Captions not required.", None, "generate captions"))
    else:
        issues = cap.validate_cues(cues, dur)
        add(_check("captions", "Captions are readable and timed", "pass" if not issues else "fail",
                   f"{len(cues)} cues, all within reading-speed and timing limits" if not issues else "; ".join(i["message"] for i in issues[:4]),
                   {"cues": len(cues), "issues": issues}, "retime or shorten the captions"))

    # 9. thumbnail
    if thumbnail is None:
        add(_check("thumbnail", "Thumbnail exists and is valid", "fail", "No thumbnail.", None, "make a thumbnail"))
    else:
        try:
            from PIL import Image

            with Image.open(thumbnail) as im:
                tsize = im.size
            small = thumbnail.stat().st_size <= 2 * 1024 * 1024
            st = luminance_stats(thumbnail)
            ok = tsize == cfg.thumbnail_size and small and st["stddev"] > 15
            add(_check("thumbnail", "Thumbnail exists and is valid", "pass" if ok else "fail",
                       f"{tsize[0]}x{tsize[1]}, {thumbnail.stat().st_size / 1024:.0f} KiB, contrast {st['stddev']:.0f}",
                       {"size": list(tsize), "bytes": thumbnail.stat().st_size, **st}, "remake the thumbnail (1280x720, under 2 MB, strong contrast)"))
        except Exception as e:  # noqa: BLE001 - an unreadable image is a failed check, not a crash
            add(_check("thumbnail", "Thumbnail exists and is valid", "fail", f"unreadable: {e}", None, "remake the thumbnail"))

    # 10. metadata
    problems = []
    if cfg.title is not None:
        t = cfg.title.strip()
        if not t or len(t) > 100:
            problems.append("title must be 1-100 characters")
        if t.isupper() and len(t) > 12:
            problems.append("title is all capitals")
    if cfg.description is not None and not cfg.description.strip():
        problems.append("description is empty")
    if cfg.title is not None or cfg.description is not None:
        if len(cfg.tags) > 15 or sum(len(x) for x in cfg.tags) > 500:
            problems.append("too many tags/hashtags")
        add(_check("metadata", "Title and description are valid", "pass" if not problems else "fail",
                   "ok" if not problems else "; ".join(problems), None, "fix the metadata"))

    # 11. provenance records
    if asset_problems is not None:
        add(_check("records", "Asset provenance and licence records are complete", "pass" if not asset_problems else "fail",
                   "every asset has a recorded origin, licence and unchanged file" if not asset_problems
                   else f"{len(asset_problems)} problem(s): " + "; ".join(f"{p['role'] or p['asset_id']}: {p['code']}" for p in asset_problems[:4]),
                   asset_problems, "complete the licence/provenance records"))
    return checks


def summarize(checks: list[dict[str, Any]]) -> dict[str, Any]:
    fails = [c for c in checks if c["status"] == "fail"]
    return {"passed": not fails, "failed": [c["id"] for c in fails], "warned": [c["id"] for c in checks if c["status"] == "warn"],
            "skipped": [c["id"] for c in checks if c["status"] == "skipped"], "total": len(checks)}
