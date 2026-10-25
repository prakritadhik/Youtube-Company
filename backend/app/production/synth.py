"""Seeded algorithmic composer and synthesizer (numpy).

This is the pipeline's own instrumental generator: it composes a small piece (chords, melody, bass, simple drums)
from a seed and a spec, renders it to WAV, and returns a bar-accurate timeline so lyrics and captions can be
aligned to the real audio.

Honesty note: the result is a short algorithmic instrumental with simple timbres. It has **no vocals**. It is a
working stand-in so the pipeline can be exercised end to end; it is not a produced song and does not replace
a sung track from a music provider.
"""

from __future__ import annotations

import math
import random
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

SR = 44100
NOTE_OFFSETS = {"C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "F": 5, "F#": 6, "GB": 6, "G": 7,
                "G#": 8, "AB": 8, "A": 9, "A#": 10, "BB": 10, "B": 11}
MODES = {"major": [0, 2, 4, 5, 7, 9, 11], "minor": [0, 2, 3, 5, 7, 8, 10]}
PROGRESSIONS = {
    "bright": [[0, 4, 5, 3], [0, 5, 3, 4], [0, 3, 4, 4], [0, 4, 3, 4]],
    "gentle": [[0, 5, 3, 4], [0, 2, 3, 4], [0, 3, 0, 4]],
    "energetic": [[0, 4, 5, 3], [0, 3, 4, 0], [5, 3, 0, 4]],
}
DEFAULT_BARS = {"intro": 2, "verse": 4, "chorus": 4, "bridge": 2, "outro": 2}
KNOWN_INSTRUMENTS = {"bells", "pluck", "piano", "pad", "bass", "drums"}
RHYTHMS = [  # (start beat, duration in beats) within a 4-beat bar
    [(0, 1), (1, 1), (2, 1), (3, 1)],
    [(0, 1.5), (1.5, 0.5), (2, 1), (3, 1)],
    [(0, 0.5), (0.5, 0.5), (1, 1), (2, 2)],
    [(0, 1), (1, 0.5), (1.5, 0.5), (2, 1), (3, 1)],
    [(0, 2), (2, 1), (3, 1)],
    [(0, 0.5), (0.5, 0.5), (1, 0.5), (1.5, 0.5), (2, 1), (3, 1)],
]


class SynthError(ValueError):
    pass


@dataclass
class Composition:
    samples: np.ndarray  # shape (n, 2) float32 in [-1, 1]
    timeline: list[dict[str, Any]]
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return len(self.samples) / SR

    def write_wav(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        pcm = (np.clip(self.samples, -1, 1) * 32767).astype("<i2")
        with wave.open(str(path), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(pcm.tobytes())
        return path


def parse_key(key: str) -> tuple[int, str]:
    parts = key.strip().split()
    root = parts[0].upper().replace("♯", "#").replace("♭", "B")
    mode = parts[1].lower() if len(parts) > 1 else "major"
    if root not in NOTE_OFFSETS or mode not in MODES:
        raise SynthError(f"unsupported key '{key}' (use e.g. 'C major' or 'A minor')")
    return NOTE_OFFSETS[root], mode


def midi_to_hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def _env(n: int, attack: float, decay_tau: float, release: float) -> np.ndarray:
    t = np.arange(n) / SR
    env = np.minimum(t / max(attack, 1e-4), 1.0) * np.exp(-t / max(decay_tau, 1e-3))
    r = int(release * SR)
    if 0 < r < n:
        env[-r:] *= np.linspace(1, 0, r)
    return env


def _tone(freq: float, dur: float, harmonics: list[float], attack: float, tau: float, release: float = 0.05) -> np.ndarray:
    n = max(int(dur * SR), 1)
    t = np.arange(n) / SR
    sig = sum(w * np.sin(2 * math.pi * freq * (i + 1) * t) for i, w in enumerate(harmonics))
    return (sig / sum(harmonics) * _env(n, attack, tau, release)).astype(np.float32)


TIMBRES = {
    "bells": dict(harmonics=[1, 0.55, 0.3, 0.18, 0.09], attack=0.004, tau=0.45),
    "pluck": dict(harmonics=[1, 0.6, 0.35, 0.2], attack=0.003, tau=0.28),
    "piano": dict(harmonics=[1, 0.5, 0.25, 0.12], attack=0.005, tau=0.5),
}


def _place(buf: np.ndarray, sig: np.ndarray, start: float, gain_l: float, gain_r: float) -> None:
    i = int(start * SR)
    if i >= len(buf):
        return
    j = min(i + len(sig), len(buf))
    seg = sig[: j - i]
    buf[i:j, 0] += seg * gain_l
    buf[i:j, 1] += seg * gain_r


def _kick(rng: np.random.Generator) -> np.ndarray:
    n = int(0.22 * SR)
    t = np.arange(n) / SR
    f = 45 + 90 * np.exp(-t * 28)
    return (np.sin(2 * math.pi * np.cumsum(f) / SR) * np.exp(-t * 14)).astype(np.float32)


def _hat(rng: np.random.Generator, length: float = 0.04) -> np.ndarray:
    n = int(length * SR)
    noise = rng.standard_normal(n).astype(np.float32)
    hp = np.diff(noise, prepend=0.0)  # crude high-pass
    return hp * np.exp(-np.arange(n) / SR * 90).astype(np.float32) * 0.5


def _snare(rng: np.random.Generator) -> np.ndarray:
    n = int(0.14 * SR)
    noise = rng.standard_normal(n).astype(np.float32)
    t = np.arange(n) / SR
    return (noise * np.exp(-t * 28) * 0.6 + np.sin(2 * math.pi * 190 * t) * np.exp(-t * 30) * 0.4).astype(np.float32)


def compose(spec: dict[str, Any]) -> Composition:
    """Compose and render. ``spec``: tempo_bpm, key, mood, structure, seed, instrumentation, bars (optional dict)."""
    bpm = float(spec.get("tempo_bpm", 100))
    if not 40 <= bpm <= 220:
        raise SynthError("tempo must be between 40 and 220 bpm")
    root, mode = parse_key(spec.get("key", "C major"))
    mood = spec.get("mood", "bright")
    mood = mood if mood in PROGRESSIONS else "bright"
    seed = int(spec.get("seed", 1))
    structure = [s.lower() for s in spec.get("structure") or ["intro", "verse", "chorus", "outro"]]
    bars_override = {k.lower(): int(v) for k, v in (spec.get("bars") or {}).items()}
    inst_in = [i.lower() for i in spec.get("instrumentation") or ["bells", "pad", "bass", "drums"]]
    unrecognised = [i for i in inst_in if i not in KNOWN_INSTRUMENTS]
    inst = [i for i in inst_in if i in KNOWN_INSTRUMENTS]
    lead = next((i for i in inst if i in TIMBRES), "bells")
    rnd = random.Random(seed)
    rng = np.random.default_rng(seed)
    scale = MODES[mode]
    beat = 60.0 / bpm
    bar_s = 4 * beat

    def degree_midi(deg: int, octave: int) -> int:
        return 12 * (octave + 1) + root + scale[deg % 7] + 12 * (deg // 7)

    progression = rnd.choice(PROGRESSIONS[mood])
    sections: list[dict[str, Any]] = []
    t = 0.0
    for name in structure:
        base = name.split()[0]
        bars = bars_override.get(name, bars_override.get(base, DEFAULT_BARS.get(base, 4)))
        sections.append({"section": name, "base": base, "bars": bars, "start_s": t, "end_s": t + bars * bar_s})
        t += bars * bar_s
    tail = 1.2  # ring-out so the last note decays instead of being cut
    total = t + tail
    buf = np.zeros((int(total * SR) + SR // 10, 2), dtype=np.float32)

    chords_used: list[list[int]] = []
    motif = rnd.choice(RHYTHMS)
    bar_global = 0
    last_deg = rnd.choice([0, 2, 4])
    for sec in sections:
        base = sec["base"]
        rhythm_pool = [motif] if base == "chorus" else RHYTHMS
        sec["bar_times"] = []
        for b in range(sec["bars"]):
            bs = sec["start_s"] + b * bar_s
            sec["bar_times"].append({"index": bar_global, "start_s": round(bs, 4), "end_s": round(bs + bar_s, 4)})
            chord_deg = progression[bar_global % len(progression)]
            tones = [chord_deg, chord_deg + 2, chord_deg + 4]
            chords_used.append(tones)
            if "pad" in inst:  # sustained triad, gently panned apart
                for k, d in enumerate(tones):
                    sig = _tone(midi_to_hz(degree_midi(d, 3)), bar_s * 1.02, [1, 0.4, 0.2], attack=0.25, tau=8.0, release=0.4) * 0.16
                    _place(buf, sig, bs, 0.6 + 0.2 * (k - 1) * -1, 0.6 + 0.2 * (k - 1))
            if "bass" in inst and base != "intro":
                for q in (0, 2):
                    sig = _tone(midi_to_hz(degree_midi(chord_deg, 2)), beat * 1.8, [1, 0.35], attack=0.01, tau=0.5, release=0.08) * 0.42
                    _place(buf, sig, bs + q * beat, 0.8, 0.8)
            if "drums" in inst and base in ("verse", "chorus", "bridge"):
                for q in range(4):
                    if q in (0, 2):
                        _place(buf, _kick(rng) * 0.7, bs + q * beat, 0.8, 0.8)
                    if q in (1, 3) and base != "bridge":
                        _place(buf, _snare(rng) * 0.35, bs + q * beat, 0.75, 0.85)
                    for h in (0, 0.5):
                        _place(buf, _hat(rng) * (0.5 if base == "chorus" else 0.32), bs + (q + h) * beat, 0.9, 0.7)
            # melody
            octave = 5 if base == "chorus" else 4
            rhythm = rnd.choice(rhythm_pool)
            for sb, db in rhythm:
                strong = sb in (0, 2)
                if strong:
                    target = rnd.choice(tones)
                    deg = target
                else:
                    step = rnd.choice([-2, -1, 1, 1, 2])
                    deg = max(0, min(13, last_deg + step))
                last_deg = deg
                sig = _tone(midi_to_hz(degree_midi(deg, octave)), db * beat * 0.95, **TIMBRES[lead]) * (0.5 if base == "intro" else 0.62)
                _place(buf, sig, bs + sb * beat, 0.72, 0.9)
                if base == "chorus" and strong:  # octave doubling lifts the chorus
                    sig2 = _tone(midi_to_hz(degree_midi(deg, octave + 1)), db * beat * 0.9, **TIMBRES[lead]) * 0.2
                    _place(buf, sig2, bs + sb * beat, 0.9, 0.72)
            bar_global += 1

    # master: soft limiter, fades, peak guard
    out = np.tanh(buf * 1.1)
    fade_in, fade_out = int(0.08 * SR), int(min(1.0, tail) * SR)
    out[:fade_in] *= np.linspace(0, 1, fade_in)[:, None]
    out[-fade_out:] *= np.linspace(1, 0, fade_out)[:, None]
    peak = float(np.max(np.abs(out))) or 1.0
    out = (out / peak * 0.89).astype(np.float32)
    end = int(total * SR)
    out = out[:end]

    timeline = [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in s.items()} for s in sections]
    meta = {
        "generator": "local-procedural-synth", "version": 1, "seed": seed, "tempo_bpm": bpm,
        "key": spec.get("key", "C major"), "mood": mood, "bar_seconds": round(bar_s, 4), "progression_degrees": progression,
        "lead_timbre": lead, "instrumentation_used": inst, "instrumentation_unrecognised": unrecognised,
        "vocals": False, "kind": "algorithmic instrumental",
        "limitations": ["no vocals", "simple synthesized timbres", "short form only"],
        "duration_s": round(end / SR, 3),
    }
    return Composition(samples=out, timeline=timeline, meta=meta)
