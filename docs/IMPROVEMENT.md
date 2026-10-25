# Learning from its own work (Phase 6)

**What this phase adds:** lessons mined from finished workflows and driven through the board from the dashboard; a record on every later task of which
lessons were in force; experiments with random assignment and an honest test; a like-for-like comparison of videos; and a real *learning* phase in the daily
cycle. It is built and tested. Everything that depends on YouTube's numbers is **mock-tested only** (docs/ANALYTICS.md), so no real experiment has been run.

The reporting chain the phase also lists (worker → manager → COO → CEO, daily executive report, worker acknowledgements) was built earlier and is unchanged;
the daily cycle (Phase 7) produces the report.

## Lessons: from a finished workflow to a later task

The path the acceptance criterion asks for ("a verified lesson from one completed workflow can be approved, stored, communicated to relevant workers, and
referenced in a later task") is one tested sequence (`test_a_verified_lesson_is_approved_stored_sent_to_relevant_workers_and_referenced_in_a_later_task`):

1. **Mined from a completed workflow.** The *Continuous Improvement Scientist* examines a video that has been cleared, scheduled or published, using only what
   was recorded:
   * **Quality:** a QA check that failed in an earlier report and passed in the final one, with the correction that was recorded;
   * **Originality:** a finding that appeared in an earlier originality review and was gone from the final PASS, with the corrective action recorded.

   A workflow that went smoothly teaches nothing, and a check that was never corrected teaches nothing. Each proposal is an **OBSERVED** entry, **low
   confidence**, for the channel it came from, with `db_record` evidence (the QA or review rows) that the CEO can re-read. The same finding on another video of
   the channel adds evidence to the existing lesson instead of creating a second one; a lesson you rejected is not reopened. Lessons are *policy class*,
   because they change how workers are told to work, so becoming an instruction is your decision.
2. **Investigated, then verified.** *Learning → Lessons* (owner): *Start investigating* (a reason is kept), then *Ask the CEO to verify the evidence*.
   The second runs the board's real check as the CEO role (every evidence item is re-read; missing records fail it; a note alone is never enough) and is audited with
   who asked for it.
3. **Approved.** *Approve as an instruction* is the owner's step. An observation cannot skip ahead, and editing a lesson afterwards withdraws it until it is verified again.
4. **Stored and published.** Approval publishes a new board version; workers must acknowledge the new instructions before starting work on that channel.
5. **Communicated to the relevant workers only.** An instruction reaches workers of the departments its category concerns, **on its own channel**. A quality lesson for
   Birthday Songs does not reach the medicine channel's workers or a department it does not concern (tested).
6. **Referenced in a later task.** When a task starts, the board entries in force are recorded on it (`lessons_applied`, shown on the video page and the Organization
   page), passed to the handler (`ctx.lessons`) and written into the task's result. A task started before approval references nothing.

**Honest limit:** a lesson is text workers are told to follow. The software does not change its own thresholds or behaviour because of one; that would let a single
observation weaken a safety check. Applying a lesson is the job of whoever does the task (today: you, until a language-model provider is connected).

## Experiments

An experiment is a *registered plan plus its results*: hypothesis, the change, one primary metric (and how it is combined), days observed per video, videos per
arm, confounders, and the board entry that carries it to the workers.

| Rule | How it is enforced |
|---|---|
| At least **5** videos per arm | creation is refused below that: with fewer, the smallest p-value that is possible cannot reach 0.05, however the numbers fall |
| Random assignment, **before publication** | permuted blocks of two, seeded; the draw (seed, flip, counts) is stored so anyone can recompute it; a video that is on YouTube (even privately), scheduled, published or rejected cannot be added; an arm is never changed |
| One experiment per video | a video in an open experiment cannot join another, so two changes never share a video |
| Strict measurement | a video's figure is the metric over the first *N* days after it went public (Pacific dates), used only two days after the window ends, and only if **every day has a figure**; a missing day is unknown, never zero; a private video has no value |
| **No peeking** | below the planned number of finished videos per arm no p-value is computed at all; the result says so |
| Honest test | a permutation test on the difference of means: exact (every relabelling counted) up to 20,000 relabellings, otherwise a seeded sample of 20,000, so the same data always gives the same answer; the effect size and the arms' summaries are shown |
| Attrition is shown | videos that never produced complete figures are listed with the reason; if the arms lost different numbers the limits say the comparison may be biased |
| Repeated looks are counted | each analysis with enough data increments *look*; from the second, the limits say repeated looks raise the chance of a false positive |
| Confidence is capped | the best an experiment earns is **medium**: one result is not proof |
| The owner decides | *adopt* needs a supported improvement and becomes an active instruction (with the result written into it, a new board version, and workers re-acknowledging); *reject* is recorded on the board as a rejected (failed) experiment; *repeat* closes the entry and opens a new plan with a new seed; *revise* closes it |

Results are described as what the numbers show: "the treatment videos were better, and a difference this large arose in x% of random relabellings", or "no clear
difference... that does not show the change does nothing". They never say *why*, and the limits list is always on the page: the unit is the video; random
assignment supports a causal reading only if both arms were made and published alike; one experiment is a single result; only the registered metric is tested.

**Starting an experiment** runs the board's real steps: proposed by the Experimentation Specialist, investigated by the Analytics manager, verified and approved for
testing by the CEO (the plan is the evidence), all audited. The board entry tells workers that this is *a test, not a rule* and to apply the change **only** to
treatment videos; each video's own page says which arm it is in and what to do (or not do).

**A baseline** (the same figure for the channel's latest public videos with complete data) is measured at the start, or states that it is unavailable and why.

## Comparing videos

*Learning → Compare videos* sets a channel's published and scheduled videos side by side on one figure with the same window and rules as experiments: complete
values are ranked and shown against the channel's own median and range; the others are listed with the reason. It never mixes channels, marks videos that belong to an
experiment, warns when fewer than five videos have complete data, and states that it is **not** an experiment: topic, timing and YouTube's recommendations can explain a
difference.

## The daily cycle's learning phase

Each day: queues the lesson-mining task if finished workflows have not been examined (one task a day), counts what waits for you (lessons to investigate or verify,
approved-by-CEO lessons awaiting your approval, experiments with enough finished videos to analyse, experiments awaiting your decision) and adds it to "needs you".
It **never analyses an experiment by itself**, because every look is counted. A day with nothing on the board says so.

## What was verified, and what was not

Verified (tests, plus mutation checks in which each safety rule was broken on purpose and a test failed; two mutants survived at first and were closed by new tests):
mining and its idempotence and channel isolation; the full lesson path to a later task; evidence failure; permissions; the statistics against a brute-force count;
the no-peeking and threshold rules; assignment; measurement; the board link; every decision; comparisons; the learning phase; the API; the dashboard in a real browser
(seeded data) at desktop and 390 px width.

**Not verified, and why it matters:**
* **No real experiment has ever run.** Real figures need a connected channel and at least ten public videos (five per arm), which is weeks of publishing.
* If YouTube omits days with zero views from its daily report, the strict completeness rule will keep small channels' windows "incomplete" until the first real
  collection shows what YouTube returns. The experiment says why; nothing is counted as zero in the meantime. (Unconfirmed; docs/ANALYTICS.md.)
* Mining recognises only QA and originality histories. Lessons about audience behaviour need real analytics and a proposer; they are written by a person on the board.
* No language model proposes, words or judges lessons or experiments; the wording is fixed templates over recorded facts.
