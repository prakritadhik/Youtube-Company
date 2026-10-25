# Provider verification register

Nothing is assumed. Each entry says **what was checked, how, when, and what is still unknown**. The application also
refuses to use a provider until the owner records, in Settings, that they have confirmed their plan's terms
(commercial use, API access, limits) — see "Owner attestation" below.

Evidence grades used here:

* **Observed** — seen directly (a read-only call, or a successful live check) with no credits spent.
* **Reported** — stated by a web-search summary of an official page; the primary page could **not** be opened from the
  build environment (its network policy returned HTTP 403 for `developers.google.com` and `elevenlabs.io`). Reported
  facts may be outdated or wrong and are never used as the basis for code that spends money or publishes.
* **Unknown** — not checked.

Last updated: 2026-10-25.

## ElevenLabs (the owner has Pro)

| Question | Answer | Grade |
|---|---|---|
| Which models does the owner's workspace expose? | Text-to-speech (`eleven_v4`, `eleven_v3`, `eleven_multilingual_v2`, turbo/flash), **music** (`eleven_music_v1`, `v2`, `v2_5`), sound effects (`eleven_text_to_sound_v2`), speech-to-text (Scribe), voice isolator/changer, dubbing, image generation (many third-party models), video generation (many), a composition/timeline tool | **Observed** (read-only node-type listing through the build assistant's connector on 2026-10-25; nothing generated, no credits spent) |
| Can music be sung with custom lyrics? | The music node has `lyrics_type: auto \| custom \| instrumental`, an `instrumental` switch and a free-text `lyrics` field, 3–600 s | **Observed** that the parameters exist. **Unknown** whether the result is a good, correctly-sung rendition of the supplied lyrics — needs one owner-approved test generation |
| Does plain text-to-speech sing? | Not assumed. Use the music model for songs, TTS only for speech | design rule |
| Can *the app* call these with the owner's API key? | **Unknown.** The listing above is the connector's creative platform, not the public API | Unknown |
| Per-call cost / included credits | **Unknown.** A search summary reported Eleven Music at about $0.15 per minute on the API pricing page | Reported, unconfirmed |
| Commercial-use rights for generated music/images/video | A search summary said music is licensed for broad commercial use on paid plans, with film/TV/large-game use requiring Enterprise, and that terms are in a separate "Eleven Music" terms document | Reported — **owner must read the current terms** |

Design consequences: ElevenLabs is the **primary candidate** for narration *and* sung music *and* (possibly) images
on the existing subscription, which fits the "minimise additional spending" goal. It stays disabled until the owner
(1) supplies an API key, (2) confirms the terms, and (3) approves one small test generation.

## Anthropic Claude (language model for agent reasoning)

| Question | Answer | Grade |
|---|---|---|
| Does Claude Pro include API usage? | **No.** Official Anthropic support articles are titled "Why is Claude API usage billed separately from my paid Claude plan?" and state the Pro plan does not include API usage through the Console, which is billed separately. Monthly API credits exist for **Max** and **Team**, not Pro | Reported (official support-article titles and snippets via search; page not opened) |
| Rate limits, current model IDs, prices | Unknown — read from the Console when a key exists | Unknown |

Design consequence: LLM-backed agent behaviour needs a billed API key (or the owner chooses another provider). Until
then roles define permissions and duties only; the pipeline's deterministic parts do not need a model.

## YouTube Data API v3 / Analytics API

| Question | Answer | Grade |
|---|---|---|
| Default quota | Reported as 10,000 units/day for general endpoints | Reported |
| Cost of `videos.insert` | **Conflicting.** Search summaries mention both a large per-upload cost and a newer change that moves uploads to a separate "Video Uploads" bucket with a small default daily allowance (about 100/day). Cannot be settled from the build environment | Reported, conflicting |
| Are uploads from unverified API projects locked to private? | **Conflicting.** Google's revision history is reported to say videos from unverified projects created after 28 July 2020 are locked private until a compliance audit; another page is reported to say otherwise. YouTube Help says locked videos cannot be appealed and must be re-uploaded via a verified service | Reported, conflicting |
| Which metrics/analytics scopes exist | Unknown | Unknown |

| Metadata limits used by the SEO checks (title 100 characters, description 5 000 bytes, tags 500 characters, hashtags ignored above 15, chapters from 0:00 / at least three / at least 10 s, no `<` `>`) | Taken from YouTube Help and API documentation as reported to the build environment; kept as named constants (`app/research/seo.py::LIMITS`) | Reported — confirmed or corrected against the live API in Phase 4 |

| Do refresh tokens expire after 7 days while the OAuth consent screen is in "Testing"? | Reported by Google's documentation as understood by the build environment; not verified | Reported — the app detects a refused refresh and asks for a reconnect; docs/YOUTUBE.md tells the owner what to check |
| Does a Desktop-app client accept a `http://localhost:<port>` redirect? | Reported (loopback redirects are the documented flow for installed apps); not verified | Reported |

Design consequences (so the app is correct *whichever* statement is true): quota costs are **configuration, not
constants**; after every upload the adapter **reads back `status.privacyStatus`** and reports a private-lock as a
failure to publish rather than a success; the owner is told early that a compliance audit may be required before
public uploads work. The code for this exists (Phase 4, tested against a mock) and is **verified only when the owner runs the first real upload**: see docs/YOUTUBE.md for what that confirms.

## Canva

| Question | Answer | Grade |
|---|---|---|
| Is design automation available to a Canva Pro account? | **Conflicting.** A community announcement is reported to say Autofill is available on Pro and above; API reference pages are reported to still say Enterprise. Help Center says Autofill is available on all plans except Pro Lite and Free. Requires MFA and OAuth scopes | Reported, conflicting |

Design consequence: Canva is **optional and manual-first**: designs are made in Canva by hand and imported as assets (with
their licence recorded). Manual use needs no API confirmation. No automation is assumed until an integration has been
tested against the owner's actual account. The pipeline does not depend on Canva.

## Not yet selected

| Need | Notes |
|---|---|
| Image generation | The ElevenLabs workspace lists several image models; licence/commercial use and API access still to confirm. Otherwise licensed stock or the owner's own art |
| Video generation | Optional; same open questions |
| Audio fingerprinting | No fingerprint tool is installed in the build environment. Until one is chosen, audio-similarity screening is **unavailable** and external-provider audio defaults to HUMAN REVIEW REQUIRED |
| Licensed stock media | Optional |

## Local components (verified, no external provider)

| Component | Status | Grade |
|---|---|---|
| FFmpeg 6.1 (libx264, AAC, libass, drawtext, xfade, zoompan, loudnorm, blackdetect, silencedetect) | Present on the build machine; must be installed on the owner's PC | Observed |
| Inter font (SIL Open Font License 1.1), bundled with its licence | Used for captions and title cards; recorded as a licensed asset | Observed |
| Local procedural generator (stdlib + Pillow) for scenes and a simple instrumental | Real, deterministic, owner-owned output; it is **not** a substitute for a produced song | Observed |

## Owner attestation

Settings → Providers records, per provider and capability: "I have confirmed my plan's terms allow commercial use
and API access", with date and a note. Adapters refuse generating or publishing calls until this exists, a key is
configured, the budget allows it and the company is not paused. The CEO cannot grant it.

## Rules for every adapter

Expose capabilities, authentication needs, cost information and error states; record provider, model, prompt/seed,
generation id, licence and commercial-use status for every asset; never exceed plan limits or bypass quotas; never use
unauthorised keys, shared accounts or trial abuse; never imitate a living artist's recognisable voice.
