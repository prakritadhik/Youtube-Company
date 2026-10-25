# Performance data and comments (Phase 5)

**Status in one line:** built and tested against a *mock of Google's documented behaviour*. Like the upload, it has **never talked to the real
YouTube**; your first real collection is its live test. Nothing in this phase writes to YouTube except an owner-approved reply, and
every reply is read back.

## What is collected, and from where

Every figure is stored with its **source** and the time it was **retrieved**, and the raw response is saved under
`data/artifacts/youtube/metrics/<video id>/` as evidence.

| Source (as the dashboard names it) | Figures | Needs |
|---|---|---|
| YouTube Data API | total views, likes and comments *so far* (one value per day of collection) | the permissions you already granted |
| YouTube Analytics API | per day: views, minutes watched, average view duration, average percentage viewed, likes, comments, subscribers gained and lost; impressions and click-through rate (asked for separately, because YouTube may not offer them); the audience-retention curve | the **analytics** permission (Settings → YouTube → *Allow*) and the *YouTube Analytics API* enabled in your Google Cloud project |
| Entered by the owner | (reserved: a figure you type in with its source) | nothing |

**Revenue is never requested.** It needs a separate monetisation permission and a monetised channel; the dashboard says so instead of
showing a number.

## "Unavailable" is not zero

A figure that could not be retrieved has **no row at all**. A `MetricGap` records why, in Google's words where it gave any:

* permission not granted (it says where to switch it on);
* YouTube refused the request, or does not offer that figure for this channel or period;
* no rows yet (new or small videos lag by days);
* a null inside a returned row (a day without views has no average, so it has no row, not a zero).

A later successful collection removes the gap. The Performance page shows `unavailable` plus the reason and the time of the last attempt.
Daily figures that can be added (views, minutes, likes ...) show a total for the window; averages and rates are never summed.

## Retention analysis

Where YouTube returns a retention curve (at least 10 points), the video's page states what it shows, in plain arithmetic: how much of the audience
was still watching at 10%, 25%, 50% and 75% of the way through, how many reached the end, the steepest loss over any 5%-wide stretch, and YouTube's own
"relative to similar videos" number when it gives one. Positions are shares of the video's length (the app does not store a measured duration for uploaded
videos). It **describes the curve; it does not say why viewers left**, and a video with few views gives a noisy curve. With fewer than 10 points or no curve
the analysis says *unavailable* and why.

## When it runs

| Schedule (default) | What it does |
|---|---|
| Collect performance data, daily 06:00 | For every uploaded video on every connected channel: totals, daily figures, retention. One video failing does not stop the others |
| Fetch and sort comments, every 12 hours | The newest comments of every uploaded video (up to 3 pages of 100 per video per run), sorted by the rules below |

Both are reads. Both can also be started from the Performance page (*Collect performance now*, *Fetch comments now*); two requests in
the same minute are one task. If no channel is connected the task still completes and says so. The daily operating cycle's *monitoring* phase
reports what was collected, how many figures are unavailable, and how many comments wait for a person; it is "unavailable" only when no channel is
connected or nothing has been collected yet.

**Quota.** Only Data API calls use the Data API quota (`videos.list` and `commentThreads.list` 1 unit each, `comments.insert` 50, defaults you
can change). The app's own ledger refuses to start a call that would not fit and records failed calls too. Whether Analytics API calls have their
own quota or limits is **unconfirmed** here; the first real run will show it.

## Permissions are asked for only when you switch a feature on

Settings → YouTube → *Extra permissions*:

| Group | Scope requested | Needed for |
|---|---|---|
| Performance data | `yt-analytics.readonly` | daily figures and retention |
| Comments | `youtube.force-ssl` | reading comments and posting replies you approve |

Each is a separate Google sign-in on a channel that is already connected; the channel stays confirmed (the app checks that it is the
**same** Google channel; a different one must be confirmed again). If Google does not grant every requested permission, **nothing is
saved**. The revenue scope is never requested. You can remove the app's access at <https://myaccount.google.com/permissions>.

## Comments

### The text is untrusted

Comment text and author names are written by strangers. They are cleaned (hidden and direction-override characters removed, length capped), scanned for
instruction-like phrasing (`ignore previous instructions`, requests for secrets, `approve this now` ...), shown **quoted**, and never followed. A flag
only raises the risk and removes the template option; no code path lets a comment change a setting, a gate, an approval or a budget.

### How comments are sorted (`rules-v1`)

Fixed rules, listed with their reasons on every comment. **They are blunt and English-only**; text they cannot judge (for example Nepali or Hindi)
is marked for a person rather than guessed at. There is no language model.

| Category | Examples of what triggers it | Needs |
|---|---|---|
| Safety | harm to self or others | **a person** (escalated) |
| Copyright | "copyright", "DMCA", "you stole this", "this is my song" | **a person** (escalated); handle through the rights process |
| Medical concern | doses, symptoms, "is it safe", "can I take"; **any question** on the medicine channel | **a person** (escalated); the software never answers |
| Personal data | e-mail addresses, phone numbers, "my address" | review; no reply is generated |
| Spam | links, "sub4sub", "check out my channel", repeated characters | ignored |
| Abuse | insults | review; not answered |
| Similarity | "sounds like the song from another channel" | review (category *copyright*) |
| Criticism | boring, too loud, misleading, bad audio ... | review; useful feedback |
| Question | a question mark or a question word | review; a person answers |
| Praise | thanks, love, great ... with no question or concern | the only category that may get a template |
| Other | nothing matched, or not English | low or review |

The Comments page also shows a plain **feedback picture** derived from these categories (positive, negative, questions, needing a person, unclassified; spam
left out). It inherits every limit of the rules: it is a count of how the rules sorted the comments, not a reading of tone.

Escalated comments are listed first. Nothing is hidden, deleted, reported or blocked on YouTube; the software only reads.

## Replies

* **Draft-only by default.** A reply is a draft until the owner approves that exact text.
* **Wording.** The software writes only a short **thank-you template, and only for plain praise** (three variants, chosen by comment). For
  anything else it writes nothing: you write the reply yourself (owner only, up to 1,000 characters). It never gives personal medical advice,
  never answers on the medicine channel beyond a thank-you, and never replies to its own comments or to replies (YouTube only allows replies
  to top-level comments).
* **Approval.** Asking for approval creates a *comment reply* approval showing the comment and the exact text. Approving queues one task;
  rejecting discards the draft. The text's hash travels with the approval, so a changed text is refused at approval time and again at send time.
* **Automatic thank-yous (optional).** Settings → Autonomy → *Comment replies*. A reply goes out without asking only if **all** hold: you chose
  the automatic policy; the mode is **Controlled autonomy**; the channel is on your autonomy list; the channel is **not** the medicine channel;
  the draft is the unmodified template; the comment is plain praise with low risk and no instruction-like text. The policy is checked again when
  the worker sends; if you changed your mind in between, the reply is parked.
* **Sending.** The task checks: not paused; the mode is not Development (public replies are disabled there); you attested YouTube's terms; the
  channel is connected with the comments permission; quota. Before posting it **lists the thread's existing replies and adopts a matching one
  instead of posting again**, so a retry can never produce a second reply. It records the intent before the call; if the answer is lost after
  the call started, the task is **blocked for you** to check YouTube Studio, then released.
* **Success means a read-back.** The reply counts as sent only when YouTube returns it under the right comment, written by this channel, with the
  approved text. Otherwise the draft is marked failed and the task is blocked with the reason.

## What was verified, and what only your first run can confirm

Verified against the mock (it follows the documented behaviour, it is not Google): every rule above, including mutation checks: removing the
text-hash check, the duplicate lookup, the medicine rule, the read-back, the policy re-check, the "unavailable not zero" rule, the permission check and the
instruction flag each made a test fail. Tests: `test_analytics_collect.py`, `test_community_triage.py`, `test_community_comments.py`,
`test_community_replies.py`, `test_youtube_permissions.py`, `test_api_community.py`, `test_community_reporting.py`.

**Unconfirmed until your first real collection** (all *Reported*, none observed from the build environment):

* that the Analytics request shape is right (`ids=channel==MINE`, `filters=video==<id>`, `dimensions=day`, these metric names, and
  `elapsedVideoTimeRatio` for retention) and whether *impressions* and *click-through rate* are offered to your channels;
* that the *YouTube Analytics API* must be enabled in the Google Cloud project as well as the Data API;
* that comment reading and replying work with `youtube.force-ssl` as built (the same scope is documented for both);
* the real quota costs, and whether Analytics calls have their own limits;
* how long YouTube takes before a new video shows daily data.

Whatever YouTube answers is recorded as an unavailable figure with its reason, so a wrong assumption shows up as a visible gap, not as a wrong number.

## Not built

* Revenue, demographics, traffic sources, search terms, per-card or end-screen figures, competitor figures.
* Moderation actions on YouTube (hide, report, ban, delete, heart, pin). Replies are the only write.
* Comment edits and deletions made by viewers after collection are not tracked (a stored comment is a snapshot).
* A language-model reading of sentiment, translation of non-English comments, replies longer than a template or in your voice.
* Experiments and comparisons across videos: Phase 6.
