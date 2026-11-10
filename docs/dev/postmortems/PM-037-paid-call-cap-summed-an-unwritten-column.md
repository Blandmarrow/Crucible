# PM-037: The paid-call cap never counted a caption call

### Symptom

An automated ComfyUI run with a billed caption provider and a paid-call cap
(`loop.max_paid_calls`) counted zero caption calls, whatever it had sent. Nothing errored
and no number on any page was visibly wrong.

Inside one caption job the cap still held, because the stage trims its image list to the
calls left before it starts the job. It failed across jobs. After a caption job that
failed, was cancelled from the top bar, or was interrupted by a restart, **Resume**
started a new job over the whole tail with the full cap again, `overwrite` on, so every
image was sent and billed a second time, and a third time after the next stop. The same
zero went into the rewrite stage's reserve for captioning, where it happened to be
harmless: that formula cancels the caption count out while it is at most `max_images`.

Found by reading the code during M9 stage 8 (2026-11-08), not from a bill. The cap shipped
with M6 (PR #110, merged 2026-11-07).

### Root cause

`run_service._spent` reads what a run has spent from its child `BackgroundJob` rows. For
captioning it sums `done_items` over the run's `caption` children. No caption job wrote
`done_items`: the column existed from the first migration, defaulted to 0, and the only
writers in the repo were `run_service.py`'s own inline steps. The caption job reports its
progress through SSE events, which are not stored.

So a reader was written against a column on the assumption that the job it describes
fills it in. The rewrite half of the same function was right, because its count
(`result_data["calls"]`) was added to the rewrite job in the same change that read it.

### Generalizable rule

**A value read from a row written by other code is only as real as its writer. Find the
writer before trusting the reader.**

- When code reads a counter, status or result field off a job row (or any row another
  module owns), grep for the assignment. A column that has a default and no writer reads
  as a plausible zero forever.
- A test of such a reader must run the real writer at least once. Hand-built rows prove
  the arithmetic; only the real job proves the number exists.
- Flag any test that replaces the function under suspicion with a stub returning the
  answer. It tests the caller and says nothing about the function.
- For a limit that guards money or data, test the paths where the work stops early: a
  failed job, a cancelled job, an interrupted one, and the resume after each.

### Why it wasn't caught the first time

- **The one test that named a caption count supplied it from a stub.**
  `test_the_rewrite_reserve_counts_captions_already_made` patched `_spent` to return
  `_Spent(caption_calls=40)`, a number the real function could never produce. Removing
  the stub would not have caught the bug either: the reserve it checks gives the same
  answer for a count of 0 and of 40. So nothing anywhere depended on the real count, and
  the stub made it look as if something did.
- **The end-to-end test used a fresh run.** `test_a_billed_caption_tail_captions_what_the_cap_allows`
  runs a real caption job under a cap, but nothing was spent before it, and zero is the
  right answer for a run's first caption stage.
- **No test resumed a stopped caption stage under a cap**, which is the only place the
  count matters.
- **The gap was known and filed in prose.** PM-008 noted in July that every job leaves
  `done_items` unwritten, as a Logs-page cosmetic. The cap was then built on that column.
  The note that the cap counted nothing sat in the roadmap's handoff text with no place in
  the order until 2026-11-09 (decision 14 names this).

### Fix

M9 stage 10 (`docs/dev/scale-plan/README.md` § Stage 10), PR #135:

- **The caption job writes `done_items` after every call**, whatever the call returned,
  before the caption is saved, committed per image (`routers/captioning.py`,
  `start_caption_job`). It survives a failed, cancelled and interrupted job. `_spent` is
  unchanged.
- **A caption stage captions only what is left.** `_stage_images` skips images whose
  `captioned_by` is already the stage's model (`run_service._not_captioned_by`), so the
  calls left go to images that still need one. Counting alone would have capped the
  spend and then spent it on the images already done.
- **`backend/tests/test_run_paid_calls.py`** runs the real job body: the count after each
  way a job can end, `_spent` over rows in every end state, and one run under a cap of
  five across a failed job, a cancelled job and two resumes. The stubbed test now goes
  through the real `_spent` over rows built by hand, which checks the reserve and still
  not the writer.

`docs/dev/comfyui/run-budgets.md` § Who writes the paid-call counts holds the rule as it
now stands, with the two accepted inexactnesses.

### Status & date

MITIGATED — fixed 2026-11-10 by M9 stage 10. A call in flight when the process dies is
still not counted (at most one per stop), by the user's ruling that day. Other readers of
`done_items` would meet the same empty column: only the caption job and a run's own steps
write it. Two edges are left open in `docs/dev/comfyui/run-budgets.md` § Not yet: the
rewrite's reserve reads failed caption calls as captions made, and a caption edited by
hand between attempts is captioned again on resume.
Last reviewed for staleness: 2026-11-10.
