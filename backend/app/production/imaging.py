"""Procedurally generated scenes and thumbnails, plus perceptual hashing.

The scenes are deterministic functions of a seed: gradients, hills, a sun, confetti and text rendered with the
bundled Inter font. They are the pipeline's *own* output (owner-generated, no third-party material). They are
placeholders for real illustration, not a substitute for it.
"""

from __future__ import annotations

import colorsys
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.production.assets import resolve_asset_path

FONT_REGULAR = "resource:fonts/Inter-Regular.otf"
FONT_BOLD = "resource:fonts/Inter-Bold.otf"


def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(resolve_asset_path(FONT_BOLD if bold else FONT_REGULAR)), size)


def _hsl(hue: float, sat: float, light: float) -> tuple[int, int, int]:
    r, g, b = colorsys.hls_to_rgb(hue % 1.0, light, sat)
    return int(r * 255), int(g * 255), int(b * 255)


def palette(seed: int) -> dict[str, tuple[int, int, int]]:
    rnd = random.Random(seed)
    base = rnd.random()
    return {
        "sky_top": _hsl(base, 0.55, 0.30), "sky_bottom": _hsl(base + 0.07, 0.65, 0.62),
        "sun": _hsl(base + 0.12, 0.9, 0.78), "hill1": _hsl(base + 0.45, 0.35, 0.28),
        "hill2": _hsl(base + 0.5, 0.40, 0.36), "accent": _hsl(base + 0.55, 0.85, 0.65),
    }


def _gradient(size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    w, h = size
    ramp = np.linspace(0.0, 1.0, h)[:, None, None]
    rows = np.array(top, dtype=float)[None, None, :] * (1 - ramp) + np.array(bottom, dtype=float)[None, None, :] * ramp
    return Image.fromarray(np.repeat(rows, w, axis=1).astype("uint8"), "RGB")


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _draw_text_block(img: Image.Image, text: str, *, center_y: float, max_w_frac: float, base_size: int,
                     fill=(255, 255, 255), bold: bool = True, max_lines: int = 3) -> None:
    """Centre text, shrinking until it fits the width and line budget; soft shadow for legibility."""
    w, h = img.size
    draw = ImageDraw.Draw(img)
    size = base_size
    while size > 12:
        font = _font(size, bold)
        lines = _wrap(draw, text, font, int(w * max_w_frac))
        if len(lines) <= max_lines and all(draw.textlength(ln, font=font) <= w * max_w_frac for ln in lines):
            break
        size -= 2
    font = _font(size, bold)
    lh = int(size * 1.2)
    y = center_y - lh * len(lines) / 2
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    for ln in lines:
        tw = draw.textlength(ln, font=font)
        sd.text(((w - tw) / 2 + size * 0.04, y + size * 0.05), ln, font=font, fill=(0, 0, 0, 150))
        y += lh
    shadow = shadow.filter(ImageFilter.GaussianBlur(max(size // 18, 1)))
    img.paste(shadow, (0, 0), shadow)
    draw = ImageDraw.Draw(img)
    y = center_y - lh * len(lines) / 2
    for ln in lines:
        tw = draw.textlength(ln, font=font)
        draw.text(((w - tw) / 2, y), ln, font=font, fill=fill)
        y += lh


def make_scene(path: Path, *, size: tuple[int, int], seed: int, title: str | None = None, subtitle: str | None = None,
               motif: str = "sun") -> Path:
    """A deterministic illustrated backdrop. Different seeds/motifs give visibly different compositions."""
    w, h = size
    pal = palette(seed)
    rnd = random.Random(seed * 7919 + 13)
    img = _gradient(size, pal["sky_top"], pal["sky_bottom"]).convert("RGBA")
    d = ImageDraw.Draw(img)

    # glowing sun / moon
    sx, sy, sr = int(w * rnd.uniform(0.25, 0.75)), int(h * rnd.uniform(0.22, 0.4)), int(h * rnd.uniform(0.10, 0.17))
    glow = Image.new("RGBA", size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((sx - sr * 2, sy - sr * 2, sx + sr * 2, sy + sr * 2), fill=pal["sun"] + (90,))
    glow = glow.filter(ImageFilter.GaussianBlur(sr * 0.6))
    img.alpha_composite(glow)
    d = ImageDraw.Draw(img)
    d.ellipse((sx - sr, sy - sr, sx + sr, sy + sr), fill=pal["sun"] + (255,))

    # layered hills
    for layer, col in ((0, pal["hill2"]), (1, pal["hill1"])):
        base = h * (0.68 + 0.1 * layer)
        pts = [(0, h)]
        phase, amp, freq = rnd.uniform(0, 6.28), h * rnd.uniform(0.05, 0.1), rnd.uniform(1.5, 3.2)
        import math

        for x in range(0, w + 8, 8):
            pts.append((x, base + math.sin(phase + x / w * freq * 6.28) * amp))
        pts.append((w, h))
        d.polygon(pts, fill=col + (255,))

    # motif: confetti (celebration) or stars (night/story)
    n = 70 if motif == "confetti" else 40
    for _ in range(n):
        x, y = rnd.randint(0, w), rnd.randint(0, int(h * 0.75))
        r = rnd.randint(max(2, h // 160), max(4, h // 60))
        col = _hsl(rnd.random(), 0.9, 0.62) if motif == "confetti" else (255, 255, 235)
        if motif == "confetti":
            d.rectangle((x, y, x + r * 2, y + r), fill=col + (230,))
        else:
            d.ellipse((x, y, x + r, y + r), fill=col + (200,))
    img = img.convert("RGB")
    if title:
        _draw_text_block(img, title, center_y=h * 0.46, max_w_frac=0.82, base_size=int(h * 0.12), bold=True)
    if subtitle:
        _draw_text_block(img, subtitle, center_y=h * 0.62, max_w_frac=0.7, base_size=int(h * 0.05), bold=False, max_lines=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG")
    return path


def make_thumbnail(path: Path, *, title: str, seed: int, size: tuple[int, int] = (1280, 720), tagline: str | None = None) -> Path:
    """Bold, legible thumbnail: large title, high contrast, no tiny text. Different seed → different look."""
    w, h = size
    pal = palette(seed)
    img = _gradient(size, pal["sky_top"], pal["accent"]).convert("RGBA")
    rnd = random.Random(seed * 31 + 5)
    # soft colour blobs, drawn on a quarter-size layer and upscaled (far cheaper than blurring at full size)
    q = 4
    small = Image.new("RGBA", (w // q, h // q), (0, 0, 0, 0))
    sd = ImageDraw.Draw(small)
    for _ in range(14):
        x, y, r = rnd.randint(0, w), rnd.randint(0, h), rnd.randint(h // 10, h // 4)
        sd.ellipse(((x - r) // q, (y - r) // q, (x + r) // q, (y + r) // q), fill=_hsl(rnd.random(), 0.8, 0.7) + (60,))
    small = small.filter(ImageFilter.GaussianBlur(h // 4 // q * 0.25))
    img.alpha_composite(small.resize(size, Image.Resampling.BICUBIC))
    band = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(band).rectangle((0, int(h * 0.30), w, int(h * 0.80)), fill=(0, 0, 0, 105))
    img.alpha_composite(band)
    img = img.convert("RGB")
    _draw_text_block(img, title, center_y=h * 0.52, max_w_frac=0.86, base_size=int(h * 0.20), bold=True, max_lines=3)
    if tagline:
        _draw_text_block(img, tagline, center_y=h * 0.86, max_w_frac=0.8, base_size=int(h * 0.07), bold=False, max_lines=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG", optimize=True)
    return path


# --- perceptual hash ------------------------------------------------------------------------------------------------


def dhash(path: Path) -> str:
    """64-bit difference hash as 16 hex chars. Visually similar images have a small Hamming distance."""
    with Image.open(path) as im:
        g = im.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    px = list(g.tobytes())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (1 if px[row * 9 + col] > px[row * 9 + col + 1] else 0)
    return f"{bits:016x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def luminance_stats(path: Path) -> dict[str, float]:
    with Image.open(path) as im:
        g = im.convert("L").resize((128, 72))
    px = list(g.tobytes())
    mean = sum(px) / len(px)
    var = sum((p - mean) ** 2 for p in px) / len(px)
    return {"mean": mean, "stddev": var**0.5}
