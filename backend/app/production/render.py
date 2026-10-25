"""FFmpeg video assembly. Builds one real command from still scenes, an audio track and an ASS caption file.

To avoid platform-specific path escaping inside filter expressions (a notorious problem with Windows drive letters),
every input is copied into a private working directory under a simple name and ffmpeg runs with that directory as its
working directory.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.production.assets import PACKAGE_DIR


class RenderError(RuntimeError):
    def __init__(self, message: str, command: list[str] | None = None, stderr: str = ""):
        super().__init__(message)
        self.command = command or []
        self.stderr = stderr


@dataclass
class Scene:
    image: Path
    duration_s: float
    label: str = ""


@dataclass
class RenderSpec:
    scenes: list[Scene]
    audio: Path
    captions_ass: Path | None = None
    size: tuple[int, int] = (1920, 1080)
    fps: int = 30
    crossfade_s: float = 0.8
    fade_in_s: float = 0.5
    fade_out_s: float = 1.0
    crf: int = 20
    preset: str = "medium"
    loudnorm: bool = True
    font_dir: Path = field(default_factory=lambda: PACKAGE_DIR / "fonts")

    @property
    def total_s(self) -> float:
        return round(sum(s.duration_s for s in self.scenes), 3)


@dataclass
class RenderResult:
    output: Path
    command: list[str]
    elapsed_s: float
    stderr_tail: str
    total_s: float


def ffmpeg_path() -> str:
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise RenderError("FFmpeg was not found. Install it (Windows: winget install Gyan.FFmpeg) and restart.")
    return exe


def validate(spec: RenderSpec) -> None:
    if not spec.scenes:
        raise RenderError("a video needs at least one scene")
    n = len(spec.scenes)
    for i, s in enumerate(spec.scenes):
        if s.duration_s <= 0:
            raise RenderError(f"scene {i + 1} has no duration")
        if not s.image.is_file():
            raise RenderError(f"scene {i + 1} image is missing: {s.image}")
        if i < n - 1 and s.duration_s <= spec.crossfade_s:
            raise RenderError(f"scene {i + 1} is shorter than the crossfade ({spec.crossfade_s}s)")
    if not spec.audio.is_file():
        raise RenderError(f"audio is missing: {spec.audio}")
    w, h = spec.size
    if w % 2 or h % 2 or w < 160 or h < 90:
        raise RenderError("width and height must be even and at least 160x90")


def build_command(spec: RenderSpec, ffmpeg: str = "ffmpeg") -> list[str]:
    """Command for inputs named s0.png.. / audio.wav / subs.ass / fonts/ in the working directory."""
    validate(spec)
    w, h = spec.size
    n = len(spec.scenes)
    T = spec.crossfade_s
    total = spec.total_s
    cmd = [ffmpeg, "-y", "-hide_banner", "-nostdin", "-loglevel", "error", "-stats_period", "5"]
    for i, s in enumerate(spec.scenes):
        length = s.duration_s + (T if i < n - 1 else 0.0)  # overlap into the next scene for the crossfade
        cmd += ["-loop", "1", "-framerate", str(spec.fps), "-t", f"{length:.3f}", "-i", f"s{i}.png"]
    cmd += ["-i", "audio.wav"]

    f: list[str] = []
    for i, s in enumerate(spec.scenes):
        length = s.duration_s + (T if i < n - 1 else 0.0)
        frames = max(int(length * spec.fps), 1)
        dz = 0.14 / frames
        z = f"min(zoom+{dz:.6f},1.14)" if i % 2 == 0 else f"if(eq(on,0),1.14,max(zoom-{dz:.6f},1.0))"
        f.append(
            f"[{i}:v]scale={int(w * 1.3)}:{int(h * 1.3)}:force_original_aspect_ratio=increase,crop={int(w * 1.3)}:{int(h * 1.3)},"
            f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={w}x{h}:fps={spec.fps},setsar=1,format=yuv420p[v{i}]"
        )
    if n == 1:
        last = "v0"
    else:
        last, start = "v0", spec.scenes[0].duration_s
        for i in range(1, n):
            out = f"x{i}"
            f.append(f"[{last}][v{i}]xfade=transition=fade:duration={T}:offset={start:.3f}[{out}]")
            last = out
            start += spec.scenes[i].duration_s
    tail = []
    if spec.captions_ass is not None:
        tail.append("subtitles=subs.ass:fontsdir=fonts")
    if spec.fade_in_s > 0:
        tail.append(f"fade=t=in:st=0:d={spec.fade_in_s}")
    if spec.fade_out_s > 0:
        tail.append(f"fade=t=out:st={max(total - spec.fade_out_s, 0):.3f}:d={spec.fade_out_s}")
    f.append(f"[{last}]{','.join(tail) if tail else 'null'}[vout]")

    af = ["aresample=44100"]
    if spec.loudnorm:
        af.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    af += ["afade=t=in:st=0:d=0.3", f"afade=t=out:st={max(total - 1.2, 0):.3f}:d=1.2", f"apad=whole_dur={total:.3f}", f"atrim=0:{total:.3f}"]
    f.append(f"[{n}:a]{','.join(af)}[aout]")

    cmd += ["-filter_complex", ";".join(f), "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", spec.preset, "-crf", str(spec.crf), "-pix_fmt", "yuv420p", "-r", str(spec.fps),
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-movflags", "+faststart", "-t", f"{total:.3f}", "out.mp4"]
    return cmd


def render(spec: RenderSpec, output: Path, *, timeout_s: int = 3600) -> RenderResult:
    exe = ffmpeg_path()
    validate(spec)
    work = Path(tempfile.mkdtemp(prefix="render-"))
    try:
        for i, s in enumerate(spec.scenes):
            shutil.copyfile(s.image, work / f"s{i}.png")
        shutil.copyfile(spec.audio, work / "audio.wav")
        if spec.captions_ass is not None:
            shutil.copyfile(spec.captions_ass, work / "subs.ass")
        (work / "fonts").mkdir()
        for font in spec.font_dir.glob("*.otf"):
            shutil.copyfile(font, work / "fonts" / font.name)
        cmd = build_command(spec, exe)
        t0 = time.time()
        proc = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=timeout_s, check=False)
        elapsed = time.time() - t0
        produced = work / "out.mp4"
        if proc.returncode != 0 or not produced.is_file() or produced.stat().st_size == 0:
            raise RenderError(f"FFmpeg failed (exit {proc.returncode})", cmd, proc.stderr[-3000:])
        output.parent.mkdir(parents=True, exist_ok=True)
        tmp = output.with_suffix(".part")
        shutil.copyfile(produced, tmp)
        tmp.replace(output)
        return RenderResult(output, cmd, elapsed, proc.stderr[-1500:], spec.total_s)
    finally:
        shutil.rmtree(work, ignore_errors=True)
