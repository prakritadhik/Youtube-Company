# Production pipeline (Phase 2)

What the platform can really produce today, and what it cannot.

## Capability map

| Step | Today | Detail |
|---|---|---|
| Creative brief | **Manual** (validated, versioned) | All 14 required fields plus channel extras; a musical plan for birthday songs. The creative director's manager approves it. An LLM will draft briefs once a model provider is connected |
| Lyrics / script | **Manual** (validated, versioned) | Author recorded (human / agent / provider). Medicine scripts need a source marker on every factual sentence |
| Soundtrack | **Automated, local** | Seeded algorithmic **instrumental** with a bar-accurate timeline. **No vocals.** A *sung* song needs a music provider (ElevenLabs music is *observed in the owner's workspace* but its adapter is not written) |
| Narration | **Built, unverified live** | ElevenLabs text-to-speech, gated by key + owner attestation + budget; tested against a mock transport only |
| Scenes, thumbnail | **Automated, local** | Deterministic illustrated backdrops and thumbnails with the bundled Inter font. Placeholders for real illustration |
| Captions | **Automated** | Lyrics aligned to the music's bars (real alignment) or narration text with *estimated* timing (labelled as such) |
| Video assembly | **Automated, real FFmpeg** | Ken Burns motion, crossfades, burned-in captions, loudness normalisation, H.264/AAC, any aspect ratio |
| Quality assurance | **Automated, real measurements** | 19 checks (below) |
| Originality & rights | **Automated screening + CEO re-check** | Findings-based; see below |
| Singing, image/video generation, Canva automation | **Unavailable** | Reported as such; nothing is faked |

## The task chain

```
brief (manual) ─┬─► script (manual) ─────────────────┐
                ├─► music ─► scenes ─┐               ├─► originality review
                │        └─► captions ┴─► render ─┬──┤
                └─► thumbnail ────────────────────┴─► QA
```

Every step is a queue task assigned to the role that owns it (permissions, tool allowlist, board acknowledgement and
board version are real), produces evidence, and counts as done only after the **manager re-verifies the evidence**
(file hashes, saved reports). A generation step refuses to start until the brief is approved: it is *blocked* with a
reason, not failed or retried, and the manager releases it.

After the tasks, three explicit finalizers apply verdicts to the video:

1. `finalize_qa` — records pass/fail. **A failure opens correction tasks** (one per responsible role, with the measured
   problem and the fix) and the video cannot proceed; asking again never duplicates them.
2. `finalize_originality` — copies the reviewer's manager-verified verdict onto the video; refuses an out-of-date review.
3. `ceo_inspect` — the CEO **recomputes** the rights analysis from the records and signs off only if it agrees. It does not trust the stored verdict.

Only then can the video enter the publishing queue, and only if every gate condition holds ([gate rules](../README.md)).

## Research and packaging (Phase 3)

Phase 3 adds the steps around production. Full detail: [RESEARCH.md](RESEARCH.md).

* **Source-based channels** (Medicine Information) start the chain with a `manual.research` task for the Research
  Specialist; the brief cannot start until that task is verified.
* **Metadata** is a separate queue task (`seo.prepare_metadata`) created by the Research and Growth manager
  (`workflow.start_packaging`), owned by the Keyword and SEO Specialist, waiting for the script and soundtrack. It drafts and
  checks a set but **applies nothing**: a research or publishing manager approves it, and only approval writes the title,
  description, tags, hashtags and chapters onto the video.
* **The publication gate now requires approved metadata** that still matches the video (`METADATA_NOT_APPROVED`,
  `METADATA_CHANGED_AFTER_APPROVAL`; neither can be waived).
* An approved **thumbnail concept** supplies the thumbnail's overlay text; its palette and composition guide a person or
  a future image provider (the local generator only draws text on a generated backdrop).

## Originality and rights screening

Status comes **only from findings**; similarity numbers are recorded but never decide anything.

| Finding | Effect |
|---|---|
| Near-duplicate of an earlier upload (≥ 60 % shared 4-word phrases) | **BLOCKED** |
| Substantial overlap with an earlier upload (≥ 30 %, an 8-word run, or ≥ 3 near-identical lines — catches swapped words and reordered lines) | NEEDS REVISION |
| 25 consecutive words copied from a recorded source | **BLOCKED** |
| 12+ words close to a source | NEEDS REVISION |
| Asset licence prohibits commercial use, or asset is a stand-in | **BLOCKED** |
| Asset has no licence, unconfirmed commercial use, no provenance, or its file changed after registration | NEEDS REVISION |
| Same structure/tempo/key/instrumentation as 2 of the last 5 uploads | NEEDS REVISION (formulaic) |
| Thumbnail within 8/64 bits of an earlier one; ≥ half the scenes reused | NEEDS REVISION |
| Plan says *sung* but the audio is instrumental | NEEDS REVISION |
| Traditional four-line "Happy Birthday to You" refrain reproduced | HUMAN REVIEW REQUIRED |
| External-provider audio (no fingerprint tool installed) | HUMAN REVIEW REQUIRED |
| Vocals claimed but not verified by a person | HUMAN REVIEW REQUIRED |
| No lyrics/script, no assets, no music, no thumbnail, no distinctive contribution stated | NEEDS REVISION |

Deliberate brand lines (listed in the channel's `brand_phrases`) are exempt from repetition counting.
Demonstration videos are excluded from the history.

**What it does not do:** detect synonym-level paraphrase; search any external lyric, music or footage database; compare
audio to third-party music (no fingerprint tool). Every report says so. A PASS means these checks found no concern.

## QA checks

file exists · decodes without errors · duration matches the plan · resolution · H.264/yuv420p · frame rate · audio present ·
audible · integrated loudness (−20…−12 LUFS) · no clipping (true peak) · no long silences · audio/video length agree ·
no unintended black screens (exact interval reported) · no frozen sections · opening frame not blank · ending complete ·
captions readable and timed · thumbnail valid (1280×720, < 2 MB, contrast) · title/description valid · provenance records complete.

The automated tests build deliberately broken videos (corrupt, wrong size, silent, clipped, black, desynchronised, unreadable
captions) and assert each check fails — a validator that has only ever passed proves nothing.

## Asset provenance

Every file records provider, model, seed or prompt/generation id, licence type and reference, and commercial-use status,
plus a SHA-256 that is re-checked (editing a file voids its clearance). Claiming commercial use requires a licence
reference. Bundled fonts (Inter, SIL OFL 1.1) are registered like any other asset. The gate re-reads the live register,
so a stale "no issues" count can never let an unlicensed asset through.

## Medicine scripts

Every factual sentence ends with `[S12]` (a recorded source: URL, retrieval date, the claim it supports); connective text
ends with `[framing]`. A sentence with no marker, or citing an unknown source, is rejected at save time. Citations do not
replace the mandatory human medical review.

## Try it

```bash
cd backend && python -m app.cli demo-production        # about 20 s at 720p
```

Creates a clearly-labelled DEMO video through the real pipeline (including a thumbnail concept and metadata that are checked and approved by the research department) and prints the result. Open the dashboard → Happy
Birthday Songs → the DEMO video to watch it and read its QA and rights reports. Demo videos can **never** be
published (`DEMO_CONTENT` is not waivable). The demo's lyrics are sample text written by the build assistant and are
labelled as such.

## What a real production needs next

1. A language-model provider so the brief and lyrics/script steps can be drafted (still reviewed by the manager).
2. A sung-music provider (ElevenLabs music, subject to the owner's terms confirmation and one approved test generation).
3. An audio-fingerprint tool, or accept that external audio always goes to human review.
4. Real artwork source (provider or the owner's own) in place of the procedural placeholders.
