# Research, planning and packaging (Phase 3)

What exists, how it decides, and what it cannot know.

## Capability map

| Step | Today | Notes |
|---|---|---|
| Idea proposal | **Manual** (owner or any role with task permission) | No model drafts ideas yet. The proposer's judgement and estimates are stored and **labelled as judgement/estimate** |
| Idea assessment | **Automated, deterministic** | Distinctness, feasibility, evidence; recomputed at decision time |
| Content plan | **Automated ordering** | Per channel; approved first, then by an explainable score. Not a forecast |
| Source tracking | **Automated + manual** | Fetched pages keep a hashed snapshot; manual notes record URL, date and the claim supported |
| Web fetching | **Built, mock-tested** | SSRF-safe, robots-aware. Not run against the live internet from the build environment (its network policy blocks most hosts), so treat the first real fetch as the live test |
| Trend / keyword / competitor / audience *data* | **Unavailable** | No search, trends or analytics source is connected. Only observations someone records with a source and date exist |
| Metadata drafting | **Template (`template-v1`)** | From the approved brief, script and sources. The AI brain does not write metadata yet |
| Metadata checking | **Automated, deterministic** | Rules below |
| Chapters | **Automated, measured only** | From section starts of the generated audio; never from estimated timing |
| Thumbnail concepts | **Manual + automated checks** | Contrast is measured; the image itself is produced in Phase 2 |
| Title/thumbnail A/B tests | **Unavailable** | Need YouTube analytics (Phase 5). Every option is labelled a hypothesis |

## The assessment of an idea

* **Distinctness** (word-overlap screening against the same channel's videos and open ideas; other channels never count;
  demonstration videos never count): *distinct*, *overlaps* (topic overlap ≥ 45 %) or *duplicate* (≥ 75 % or near-identical
  title). A duplicate scores 0 and cannot be approved. This cannot see meaning or other catalogues.
* **Feasibility** from the live provider registry: each requirement the idea states (`instrumental_music`, `sung_vocals`,
  `narration`, `illustrated_scenes`, `ai_images`, `llm_drafting`, `video_render`) must be implemented, configured, attested
  by the owner, inside budget and not paused. An idea that needs something missing is **parked**, with the reason.
* **Evidence**: counts sources by class. Medicine ideas need at least two sources, one **authoritative** (the class is a
  heuristic on the web address: `.gov`, `.edu`, `who.int`, `nih.gov`, `nhs.uk` and similar; a person still judges the
  source). **Demand** reads *unavailable* until a trend/audience note exists, and then says "recorded observations; not a forecast".
* **Score** (0-100): weighted mean of the parts that exist — distinctness (3), feasibility (2), evidence (2, medicine only),
  audience relevance (2, *the proposer's judgement*), cost (1, *an estimate* against the per-video budget). Missing parts are
  listed as unavailable and excluded, not guessed.
* **Decision**: research manager, COO, CEO or owner; the author of an idea cannot approve it (the owner can); a reason is
  required; the assessment is re-run first so a stale "distinct" cannot approve a video that has since been made.

## The metadata checks

Platform limits come from YouTube's published documentation as reported to the build environment (title 100 characters,
description 5 000 bytes, tags 500 characters, hashtags ignored above 15, chapters from 0:00, at least three, each at
least 10 s, no `<` or `>`); the live API is the final judge (Phase 4 reads back what it accepted).

| Severity | Findings |
|---|---|
| **Must fix** (cannot be waived) | over-length title/description/tags; angle brackets; invalid or too many hashtags; medicine claims that promise results (*guaranteed*, *miracle*, *100 %*, *no side effects* …); a title that promises singing when the plan is not sung; missing medical disclaimer, no sources, or sources not listed (medicine); chapters that are not from measured section starts, fewer than three, shorter than 10 s, or on a video with only estimated timing; a recommendation that claims better performance |
| **Revise** (fix in a new version, or accept with a recorded reason) | shouting, repeated punctuation, false urgency, clickbait phrasing; repeated words or keyword stuffing in title, description or tags; title words that appear nowhere in the brief, script or sources (*accuracy*); near-duplicate of an existing title; **"song" in the title of an instrumental**; tags or hashtags the video's content does not support; too many hashtags; missing AI disclosure (story channel, AI narration); unlabelled recommendation; chapters missing from the description |
| **Note** | long for display; no chapters; unassessed contrast |

Accuracy can only be judged if there is a brief or script to compare with, and the working title is deliberately **not**
part of that material (a title cannot vouch for itself). Without material the finding is *unassessed*, never a pass.

Approval: research manager, publishing manager, COO, CEO or owner; the author cannot approve; the set is **re-checked at
approval**; blocking findings cannot be waived; each waived revise-level finding keeps its reason in the record.

## Thumbnail concepts

Checks: text ≤ 5 words and 32 characters; shouting/clickbait; words supported by the video's material; medicine claim
wording and before/after imagery; valid `#RRGGBB` colours; **measured WCAG contrast** between text and background (at least
4.5:1); resemblance (same colours and a very similar layout) to the last five concepts in the same channel. An approved
concept's overlay text is used by the local thumbnail generator. **Known gap:** the gate does not yet require an approved
concept; that becomes necessary when an image provider produces thumbnails.

## Untrusted content

Everything from outside (fetched pages, pasted excerpts, comments later) is cleaned, capped, scanned and stored as
*untrusted data*. Instruction-like text ("ignore previous instructions", role markers, requests for secrets or commands,
requests to skip review or publish) is **flagged for the reader**; flags never grant or remove any ability. When a language
model is connected, `quote_for_agents` wraps such text so it cannot pass for an instruction.

## Reporting

The Research and Growth manager's department report carries nine machine-checkable counts (ideas proposed/approved/
rejected/parked, notes, trend observations, metadata approved/needing revision, thumbnail concepts approved). The CEO re-runs
every one; an inflated claim is rejected (`test_the_ceo_rechecks_research_claims_and_rejects_an_inflated_one`). Data the
company does not have is covered by the standard "unavailable: YouTube is not connected" statement.

## What it cannot do yet

* Find trends, keywords or competitor data by itself (no data source is connected).
* Write titles or descriptions in a human voice (the templates are plain). The AI brain can propose *ideas* (AI Brain → Ask for ideas) but does not research: it cannot look anything up.
* Test which title or thumbnail works better (needs analytics).
* Check meaning: all similarity and accuracy checks are word-based screening aids.
