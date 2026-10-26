# The AI brain: Claude Code on your computer

**What this is:** the roles that write (creative director, script and lyrics writer, content strategist) and the managers who review them do their
thinking by running the **Claude Code** program installed on your computer, signed in with **your own Claude account**. There is no API key and
nothing is sent anywhere except through that program. Everything it returns is *text*, and the company checks that text with its own rules before
using it.

**What it does not do:** it never approves, publishes, pays, replies to a comment, changes a setting or runs a command. Rights concerns, medical
review and publication approval remain yours. Switching it off (Settings → remove the confirmation, or set the allowance to 0, or the emergency
pause) stops every model call at once.

## Setting it up (once)

1. **Install Claude Code** and run `claude` once in a terminal to sign in with your account.
2. If `claude` is not on the PATH of the terminal that starts the company, put its full path in `.env`: `YTC_CLAUDE_CODE_BIN=C:\\...\\claude.cmd`.
3. **Settings → Providers → "Claude Code on this computer"**: confirm that your plan allows running Claude Code like this. *This software cannot check
   your plan's terms and does not assume them; read them yourself.* Nothing runs until you tick this.
4. **Settings → Spending and usage limits**: set *Language-model requests per day* and *Provider requests per day* above 0 (they default to 0, which means
   "not permitted"). A request is one call, including a correction round. A video needs roughly **4 to 8**; a batch of ideas needs 1.
5. **AI Brain page → Run the connection test.** It asks Claude Code one harmless question and checks that it starts, is signed in and has no tools.

The page shows each of those as a tick or a cross, with what to do about it.

## What you do with it

* **AI Brain → Ask for ideas** (or the Content Plan): the content strategist proposes up to five ideas for a channel. They arrive *assessed* (distinctness
  against what the channel already made, feasibility against what can be produced today). Ideas that duplicate existing work are screened out. Choosing what to
  make is yours: you approve or reject them as before.
* **Content Plan → an approved idea → Start with the AI company.** This creates the video and its whole task chain with the writing steps done by the brain.
  *Start by hand* still exists and creates only the video, as before.

## What happens after "Start with the AI company"

```
creative director (AI) writes the brief ──► production manager reviews it (AI-assisted) ──► approved brief
script/lyrics writer (AI) writes lyrics or a script ──► manager reviews it (AI-assisted)
music ─► scenes ─► captions ─► render ─► thumbnail ─► QA ─► originality review     (software; manager re-verifies each one's evidence)
conductor: records the QA verdict, the rights verdict, the CEO's independent rights re-check, approves clean metadata, moves the video to READY,
           and ASKS YOU to approve publication (medicine: asks for the human medical review first)
```

The video page shows a banner with **who has to move next**: *the company*, *you*, or nobody, and why.

### Where it always stops and waits for you

| Situation | What the company does | What you do |
|---|---|---|
| Quality check fails | Opens correction tasks for the owning roles | Fix them (the company does not paper over a failed check) |
| Originality/rights verdict is anything but PASS | Nothing downstream | Decide on the Rights page |
| The CEO's independent re-check disagrees with the reviewer | Stops and says why on the video page | Look at the rights report |
| Metadata has findings | Waits | Edit or approve with a reason |
| Medicine channel | Asks for the **human medical review** and does not mark the video READY until it is given | Review against the sources and approve |
| Everything passes | Moves the video to READY and **asks** for publication approval | Approve or reject (the company never asks twice after a decision) |
| A draft is returned twice and still not good | Hands it to you | Read the notes and decide |
| The brain is unavailable (not signed in, usage limit, allowance used, paused) | Parks the work with the reason, shows it on the video and in "needs you", and **resumes by itself** when the brain is ready | Sign in / wait / raise the allowance |

A medicine idea additionally needs ≥2 recorded sources (one authoritative) *before* you can approve it, as before. The brain is shown only those recorded
sources (quoted as untrusted data), must end every sentence with a marker naming one of them (`[S3]`) or `[framing]`, and the script is rejected by the
software if any sentence lacks one. **The brain cannot look anything up**, so it can only restate what the recorded sources say. A person still reviews it.

## How the brain is kept safe

| Measure | Detail |
|---|---|
| **No tools** | Claude Code is started with every tool switched off, so it cannot read files, run commands or browse. It can only return text. The connection test fails if it reports any tool |
| **Empty working folder** | Run from a folder outside the project (`<temp>/ytc-ai-sandbox`) |
| **Your plan, never a key** | `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` and the cloud-provider switches are removed from the program's environment, so it can only use your signed-in account |
| **Prompt on standard input** | Not on the command line: no quoting problems on Windows, no length limit surprises, nothing visible in a process list |
| **One call at a time** | Calls are serialised, so a burst cannot burn the plan's limit |
| **Strict output** | The answer must parse as one JSON object that satisfies a schema. If not, the company sends it back once, listing the problems; a second failure is never used |
| **The same rules as for a person** | Brief validation; lyrics must use the planned section headers, line counts and line lengths so the captions fit the music; no well-known birthday lines; not too close to the channel's earlier writing (same thresholds as the originality review); medicine citations |
| **External text is data** | Source excerpts are fenced and labelled as untrusted in the prompt; the model's output is never executed or followed as an instruction |
| **Board lessons reach the writer** | The verified instructions for the channel and the video's experiment arm are put into the prompt word for word and recorded on the task |
| **Bounded review** | A returned draft is revised at most twice; an unusable review answer is never turned into an approval |
| **Everything is recorded** | Each call is a row (who, what for, outcome, tokens, duration) plus a file with the exact prompt and answer (secrets redacted), shown under *AI Brain → Everything the brain has done* |
| **Counted as usage** | Every call is a usage record (category *model*, "included in subscription"), counted against your daily limits |

## Honest limits

* **The manager's review of a creative draft is a second reading by the same kind of model.** It is not independent assurance. The real assurances are the
  deterministic checks above, the originality review and QA, the CEO's independent rights re-check, and *your* decisions.
* **Quality is not measured.** The software can check that lyrics fit the music and do not copy; it cannot judge whether a song is good. Read what it writes.
* **No narration voice, no singing voice** unless a provider for them is connected (see PROVIDERS.md). The AI-written birthday songs are *lyrics shown as
  captions over a generated instrumental*; the briefs are told so and the originality review flags any claim of vocals. Medicine and story videos get
  captioned text over an instrumental bed until narration exists.
* **Usage:** the cost figures are the program's own *list-price* estimates, not what you are charged. Whether and how fast your plan's limit is reached depends on
  the plan; the software cannot see it.
* **The exact wording of Claude Code's sign-in and usage-limit messages could not be reproduced here.** The software recognises likely wording; anything else is kept
  word for word, parks the work and waits 20 minutes (or until a connection test passes) rather than retrying in a loop.
* **Terms:** whether your plan permits unattended use of Claude Code is your call. The confirmation in Settings is the record that you made it.

## What was verified

* **Tests with a stand-in `claude` program** (a real executable, so process handling is real): readiness and limits, permission, correction rounds, every failure kind,
  recording, usage, heartbeat during a long call, the drafting rules, the review rules and bounds, the conductor's every stopping point (QA failure, rights concern,
  CEO disagreement, medical review, publication), parking and automatic resume, the API and its permissions. The full path
  idea → brief → lyrics → music → scenes → captions → render → QA → rights → metadata → READY → publication request runs through the real queue and real FFmpeg.
* **Against the real program** (opt-in, `YTC_LIVE_CLAUDE=1`): the adapter's connection test and a real call (`tests/test_claude_code_live.py`), and the whole company on
  one idea (`tests/test_ai_live.py`). See STATUS.md for the latest result.
* **Not verified:** Windows (the program is run through `claude.cmd`; the arguments are kept short for that reason, but it has not been run there), the real
  not-signed-in and usage-limit messages, how long your plan lasts under daily use, and the quality of what a real model writes over many videos.

## If something is wrong

| You see | Meaning | Do |
|---|---|---|
| "Not ready: the `claude` program was not found" | Not installed or not on the PATH | Install it, or set `YTC_CLAUDE_CODE_BIN` in `.env` and restart |
| "needs your confirmation" | You have not confirmed the plan terms | Settings → Providers |
| "daily limits … are 0" | Allowance not set | Settings → Spending and usage limits |
| "Daily … limit reached" | Used today's allowance | Raise it, or wait for tomorrow; work resumes by itself |
| "not signed in" / "waits until …" | Claude Code needs `/login`, or hit a usage limit | Sign in or wait, then run the connection test (it ends the wait) |
| A task "Blocked: AI_REVIEW_FAILED" | The draft was returned twice or the review could not be read | Open the task, read the notes; unblock to have it written again, or write it by hand |
| Calls listed as "Sent back" | The answer broke a rule and was corrected; normal in moderation | Open one to see the problems |
