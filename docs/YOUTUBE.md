# YouTube integration (Phase 4; analytics and comments are Phase 5, see [ANALYTICS.md](ANALYTICS.md))

**Status in one line:** the integration is built and tested against a *mock of Google's documented behaviour*. It has **never
talked to the real YouTube**, because that needs a Google Cloud OAuth client that only you can create. This page lists exactly what
you must do, and what your first real run will confirm or correct.

## What you need to do (once)

1. **Google Cloud project.** Open <https://console.cloud.google.com/>, create a project (any name), and in *APIs & Services → Library*
   enable **YouTube Data API v3**. If you want performance data (Phase 5), also enable **YouTube Analytics API**
   (*Reported* to be a separate API; not confirmed from the build environment).
2. **OAuth consent screen.** *APIs & Services → OAuth consent screen*: user type **External**, app name, your e-mail. Add the Google
   account(s) that manage your channels as **test users**. Under *Scopes* add `youtube.upload` and `youtube.readonly`; for Phase 5 also `yt-analytics.readonly` and `youtube.force-ssl`
   (the app asks Google for these only when you press *Allow* next to a feature in Settings).
   * **Reported, not confirmed:** while the consent screen's publishing status is *Testing*, Google expires refresh tokens after
     about 7 days. The app handles that: the channel turns to *Needs reconnect* and one click reconnects it. Moving the status to
     *In production* (an unverified personal app shows an "unverified app" warning screen) is reported to avoid the expiry. Check
     Google's current wording before relying on either.
3. **OAuth client.** *Credentials → Create credentials → OAuth client ID → Application type: **Desktop app***. Copy the client ID and
   client secret into `.env` in this folder:
   ```
   GOOGLE_OAUTH_CLIENT_ID=...apps.googleusercontent.com
   GOOGLE_OAUTH_CLIENT_SECRET=...
   ```
   Never commit the downloaded JSON file or `.env`. Restart the app (`python launcher.py`).
4. **Terms.** In *Settings → Providers → YouTube Data/Analytics API* confirm that you have read the YouTube API Services Terms and
   that your use complies. The app refuses to upload until you do (and the CEO cannot do it for you).
5. **Connect each channel.** *Settings → YouTube → Connect to YouTube*. Sign in with the Google account that **manages that channel**
   (for a brand account, choose the brand channel on Google's account screen). Back in the dashboard the app shows the channel
   Google says you signed in as; confirm it is the right one, and **answer the audience question** (*made for kids* or not). YouTube
   requires the answer and the responsibility is yours; the app will not guess. Each internal channel needs its own YouTube channel.
6. **Operating mode.** Switch to **Approval-first** (Settings). In *Development* mode every upload is refused by design.
7. **Verify the channel for thumbnails.** YouTube only lets verified channels set custom thumbnails (phone verification in YouTube
   Studio). The app reports a refused thumbnail with this hint; the upload itself still stands.

## The first real test (about a minute)

```
cd backend
python -m app.cli youtube-status
python -m app.cli youtube-selftest --channel happy-birthday-songs --yes-upload-a-private-test-video
```

It makes 4 seconds of test pattern, uploads it to your channel as **private**, reads it back from YouTube, sets a thumbnail, and
deletes it again (add `--keep` to leave it). It touches no catalogue video. Please record what it reports:

| Question | Where the answer appears | What to do with it |
|---|---|---|
| Does the resumable upload work end to end with your client? | `result: VERIFIED` and the `read_back` list | If not, send me the message; nothing else is affected |
| May this channel set custom thumbnails? | `thumbnail: SET` or `FAILED` plus the reason | Verify the channel in Studio if `FAILED` |
| What did each call cost in quota? | Compare `quota_units_used_today` with the Google Cloud console (*APIs & Services → YouTube Data API → Quotas*) | Correct the costs in the `youtube` setting if they differ |
| Do tokens last? | The channel stays connected after a week | If it turns to *Needs reconnect* after ~7 days, the consent screen is in *Testing* |

**What the self-test cannot tell you:** whether a *public* upload stays public (it only ever uploads private). That is answered by
the first real public upload, below.

## The first real upload

1. Produce and clear a real video (not the demo), then on its page choose *Queue upload* with visibility **Private**. Check it in
   YouTube Studio.
2. If it is right, queue the next one as **Public**, or give a *go public at* time (uploaded private; YouTube publishes it then).
3. The app reports **success only from a read-back**. Outcomes:

| Status | Meaning | What to do |
|---|---|---|
| **Verified** | YouTube returned the video on the expected channel with the requested title, category, audience, privacy and schedule | Nothing |
| **Privacy locked** | The video exists but YouTube made it **private although public/unlisted was requested**. It is *not* published. YouTube is reported to do this for API projects that have not passed its compliance audit; this build could not confirm that (docs/PROVIDERS.md) | Apply for YouTube's API compliance audit (Google's "API Compliance Audit" form for the project), or publish from YouTube Studio by hand. Reported: such videos cannot be made public through the API afterwards, so a re-upload may be needed |
| **Mismatch** | What YouTube holds differs from what was asked (wrong channel, title, audience, schedule, or the video is missing on read-back) | Look at it in Studio. The task is blocked for you |
| **Failed** | Rejected or failed processing, or the daily upload limit was hit | The reason is on the card; fix and queue again after abandoning the failed one |

A video is **never uploaded twice**: one upload per video; the owner can abandon a wrong one (and should delete the YouTube copy by
hand).

## How an upload is protected

* Re-checked **at upload time**, not only when queued: the full publication gate (rights, QA, approved and unmodified metadata,
  medical review, owner approval, operating mode, pause), the connection and audience setting, owner attestation, the file's hash,
  and the quota.
* The upload row is written first and updated at every step; an interrupted upload **resumes the same session** (the session is
  asked where it stands; bytes are never assumed). If the app is killed after the bytes began, the task is blocked until you release
  it, then it asks YouTube instead of uploading again.
* A quota or permission problem **parks** the upload with its session kept; releasing it later resumes rather than restarts.
* Raw API responses are saved under `data/artifacts/youtube/uploads/<id>/` and attached to the task as evidence the manager re-hashes.
* Nothing is applied to the video record (stage, YouTube id, publish time) until the read-back verified it.

## Quota

YouTube gives each Google Cloud project a daily quota that resets at midnight Pacific time. The app counts **its own calls** at the
costs in the `youtube` setting (defaults: upload 1600, thumbnail 50, read 1, channel lookup 1, delete 50) against the daily limit you
allow it (default 10 000), refuses an upload that would not fit, and records quota even for failed calls. **Reported, conflicting:**
Google may have changed upload costs or moved uploads to a separate allowance (docs/PROVIDERS.md); that is why the costs are
settings, not constants. The Google Cloud console is the authority; the dashboard says so next to the number.

## Security

* Sign-in uses OAuth with **PKCE**, a single-use `state` (only a hash is stored) and a loopback redirect to this app
  (`http://localhost:8000/api/youtube/callback`, which Google allows for Desktop clients). It requests only `youtube.upload` and
  `youtube.readonly` at first. Performance data (`yt-analytics.readonly`) and comments (`youtube.force-ssl`) are requested separately, only when you
  press *Allow* for them; the revenue scope is never requested ([ANALYTICS.md](ANALYTICS.md)).
* Refresh and access tokens and upload-session URLs are stored **encrypted** (Fernet). The key is `data/secrets/token.key`, outside the
  database and outside database backups. If you lose it, you reconnect; the app never silently creates a replacement for tokens that
  still exist. **Back up `data/secrets/` separately from `data/backups/`**, and keep both private (BitLocker helps).
* Tokens never appear in the audit log, logs or API responses. The access token is sent only to Google hosts; an upload address that
  is not an https Google address is refused.
* Only the owner can connect, confirm, disconnect or abandon. Disconnecting revokes the token at Google (best effort) and forgets it.

## Not built (yet)

* Editing a video's metadata or thumbnail **after** upload through the app (`videos.update` is not implemented): change it in YouTube
  Studio, or abandon and re-upload.
* Playlists, end screens, cards and other Studio-only features (not offered by the public API or not built).
* Deleting uploads from the app (only the self-test deletes its own test video).
* The `containsSyntheticMedia` ("altered or synthetic content") disclosure flag is **not sent**, because this build could not confirm
  the field. Review that setting in Studio for videos with realistic synthetic content; AI narration is already disclosed in the
  description by the metadata checks.
