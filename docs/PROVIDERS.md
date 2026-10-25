# Provider verification register

Nothing below is assumed. Each provider is **verified immediately before its integration is built**, and the
finding is recorded here with the source and the date. Until a row says *verified*, treat the capability as unknown.

| Provider | What the master prompt wants | Must be verified before building | Status |
|---|---|---|---|
| Claude (Anthropic API) | Agent reasoning, writing, analysis | Whether the Claude Pro plan includes any API access (believed to be billed separately from the API — confirm from the official pricing/terms pages), rate limits, current model IDs and pricing, data-handling terms | not verified |
| ElevenLabs | Narration; possibly vocals | API access and quota on the Pro plan; commercial-use licence for generated audio; **whether it can sing** (do not assume text-to-speech can); voice-rights rules; per-character cost | not verified |
| Music generation (for original songs) | Genuinely sung original birthday songs | Which tool is affordable, API-accessible, licensed for commercial use, and produces real vocals | not selected |
| Image generation | Scenes, characters, thumbnails | Provider, licence/commercial use, provenance metadata, cost | not selected |
| Video generation | Optional | Provider, cost, licence | not selected |
| Canva | Branded designs, thumbnails | Whether the account has API/connect access for automation; licence of templates and assets; otherwise label as manual | not verified |
| YouTube Data API | Upload, metadata, thumbnails, scheduling, comments | Current quota costs per call (uploads are expensive), OAuth app verification/test-user limits, what an unverified app may do | not verified |
| YouTube Analytics API | Views, CTR, retention, revenue | Which metrics each scope exposes, delays, monetisation-only metrics | not verified |
| Licensed media (optional) | Stock music/footage | Licence terms per source | not selected |

Rules for every adapter: expose capabilities, auth requirements, cost information and error states; record the
provider, licence and generation history for every asset; never exceed plan limits or bypass quotas; never use
unauthorised keys, shared accounts or trial abuse.
