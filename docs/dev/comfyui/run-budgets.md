# ComfyUI — run budgets and carrying on

A looping run (`docs/dev/comfyui/run-loop.md`) is held by hard caps, and a run a cap stopped
can carry on. This file covers how the budgets are recomputed from the run's child jobs, when
each is checked, the paid-call cap, raising a cap and carrying on (`extend_run`,
`_tail_rows`), the seed check, and what is not yet handled. The loop's shape, `next_pass` and
the clamp are `docs/dev/comfyui/run-loop.md`; the gate is `docs/dev/comfyui/run-gate.md`. The
user guide is `docs/repeating-runs.md` § The caps.

## Budgets: recomputed, never accumulated

`LoopSettings` holds four caps, all required except the paid one: `max_passes`,
`max_images`, `max_minutes`, `max_paid_calls`. `_spent` reads what the run used from **its
child `BackgroundJob` rows** (`config.run_id`) every time:
- **minutes**: the sum of the children's `finished_at − started_at`, so pauses do not
  count. A child the restart marked interrupted (`INTERRUPTED_MSG`) counts as no time:
  its `finished_at` is the restart, so its span holds the downtime, and counting it would
  block every resume after a crash or a sleep. Undercounting it is the safe side;
- **paid calls**: `result_data.calls` over `comfy_rewrite` children when the rewrite
  provider is billed, plus `done_items` over `caption` children when the caption
  provider is billed (§ Who writes the paid-call counts);
- **images**: every image the run's rows produced (`run_image_ids` over `row_ids`).

Stage entries are not the record: resume and the restart reconcile wipe a failed stage's
numbers, and a failed rewrite's calls were still made.

### Who writes the paid-call counts

`_spent` only reads. Each count is written by the job that makes the calls, as it goes, so
a job that failed, was cancelled or was interrupted by a restart still counts in full:

- **Rewrite**: `start_rewrite_job` writes `result_data["calls"]` after every batch
  (`docs/dev/comfyui/run-loop.md` § The rewrite stage).
- **Caption**: the caption job writes `done_items = i + 1` after every call, in
  `start_caption_job`'s loop (`docs/dev/captioning.md` § Captioning job execution). It is
  written whatever the call returned (a caption, an empty reply, a timeout, any error),
  because the provider was asked each time. It is written before the caption is saved, so
  a save that fails the whole job keeps the call. The queue's terminal writes and
  `mark_interrupted_jobs` never touch the column.

Until M9 stage 10 no caption job wrote `done_items`, so the caption half was always 0 and
a resume after a stopped caption job could bill the whole tail again (PM-037).

Two known inexactnesses, both accepted (the user's ruling, 2026-11-10):
- **A call in flight when the process dies is not counted**, nor one still running when
  shutdown's grace runs out: at most one per stop, for either job. A Stop from the UI is
  not such a case: it lands between images, after the call in flight has been counted.
- **A caption call that never left the machine is counted** (a file that will not open
  fails before the request). That errs towards stopping early.

**Billed** is `schemas/openai_provider.provider_is_billed`: the provider's `billed`
setting, or whether its URL is remote when that is NULL (`docs/dev/llm-providers.md`).
Ollama and the local captioners are never billed.

### When they are checked

- **Before `prompts`, `rewrite` and `generate`** (`_budget_stop`): time and images.
- **At `next_pass`** (`docs/dev/comfyui/run-loop.md` § `next_pass`): the pass cap, then time and images.
- **At the start** (`_preflight_paid_cap`): a billed rewrite or caption provider needs
  `max_paid_calls`. When captioning is billed, the cap must be at least `max_images`, since
  each kept image costs one caption call; that reserves the tail. When both are billed,
  something must be left over for rewriting.
- **The rewrite's `max_requests`** is the cap, less the calls spent, less that reserve. The
  job therefore cannot overspend by its call budget's 25% slack. The reserve is
  `max_images` less the caption calls already made (`_Spent` counts rewrite and caption
  calls apart): a run carried on past a cap has captioned some, and reserving all of
  `max_images` again would stop its rewrite at once.
- **Before the caption tail**: the kept images against the calls left. The reserve
  covers `max_images`, but a workflow that saves several images a row can make more on
  its first pass (the clamp plans at one a row until a row has completed), and a resumed
  caption stage has already spent calls. So the tail **captions what the cap allows**, in
  order, and its note says how many were left out. It never fails a loop whose GPU work
  is done.

**A caption stage captions only what is left** (the user's ruling, 2026-11-10), billed or
not. `_stage_images` drops every image whose `Image.captioned_by` is already the stage's
model (`_not_captioned_by`) before it applies the trim above. The caption job stamps its
model id there when it saves, and a ComfyUI prompt caption stamps `"comfyui"`, so on a
first start this rule drops nothing. On a resume after a failed, cancelled or interrupted job,
what that job saved is skipped; an image whose call failed, or whose reply was stripped to
nothing, was never saved and is sent again. Without the skip the trim would spend the calls
left on the first images in order, which are the ones already captioned, and the rest
would go without. Nothing left skips the stage with a note, never `[]`.

**A caption written by hand is never replaced.** The caption editor stamps `"manual"`
(`routers/captions.py`, `run_service.HAND_CAPTIONED`), and `_not_captioned_by` drops
those images too, on a first start as on a resume. The run made these images, so a
hand-written caption on one was written during this run, most often to fix what the model
said before pressing Resume; without the rule the resume sent that image again and
overwrote the edit. A bulk edit (find and replace, tag consolidation) leaves the stamp
alone, so a model caption edited that way is already skipped. An image captioned by hand
costs no call and is not in the trim's count.

The mark is `captioned_by`, not the caption, and a snapshot restore puts `caption_text`
back but not `captioned_by`, which is not mirrored (`NOT_MIRRORED`). So restoring a
snapshot taken before the caption job, mid-run, leaves images that read as captioned and
are skipped.

A check alone cannot stop a generate stage partway, which is why the image budget is also
**the clamp** in `prompts` (`docs/dev/comfyui/run-loop.md` § What each body stage does in a
loop).

Running out ends the loop through `_stop_loop`: the pass's remaining body entries are
`skipped` with "Stopped: <reason>", `context.loop` becomes `short`, and the tail still runs.
A handoff whose partner already ran this pass is kept (`_UNDOES`): `handoff_to_local`
after `handoff_to_comfy`, `free_rewrite_model` after `rewrite`. Otherwise a loop stopped
at `generate` would leave ComfyUI holding the GPU through the caption tail.

## Raising a cap and carrying on

M6 §6: `POST /runs/{id}/extend` (`run_service.extend_run`) takes new `LoopSettings` for a
run that stopped on a cap. `can_extend` (also `RunOut.can_extend`) allows it only for a
`completed` run whose loop ended `short` with a recorded `cap` and with a folder still
`open`. A run that gave up on all its folders has nothing a cap would change, and one
from before §6 recorded no cap. `extend_run` then refuses a cap that goes down, a
stopping cap not raised (both 400), a paid-call cap `_preflight_paid_cap` rejects, a
deleted plan, and another live run on the dataset (409, under `_dataset_lock`).

It **appends** the next pass's body entries, copied from the last pass, and a fresh tail
after every entry the run has. Every started stage keeps its index, so `stage_tag` stays
unique, and `next_pass`'s `leaves_pass` guard recounts for the new pass. `context.loop` is
cleared and the recipe's `loop` replaced; abandoned folders stay abandoned. Spending is
still read from the child jobs, so the raised caps count what the run has used.

**The new tail captions only the new passes.** `_tail_rows(run, idx)` gives a caption
entry the rows of the passes between the previous `caption` entry and itself; the first
tail still takes `row_ids`. It is read from the stage list, so it survives a resume and
the restart. Without it the second tail, `overwrite` on, would caption every image again
and bill a paid provider twice, silently. No rows in scope skips the stage, never `[]`.
The export simply runs again.

## The seed check

`check_seeds(plan)` runs at the start of a looping run. Every workflow input named `seed`
or `noise_seed` that holds a value (not a link) must have a pin with `int_mode: random`.
An unpinned seed keeps the template's value on every row, and `increment` restarts at its
base each run, so a later pass would repeat the last one's pictures, and the duplicate
scan would reject them all. The 400 names each node.

## Testing

`backend/tests/test_run_extend.py` (M6 §6) stops a loop on its image cap, which needs no
score or gate, so it runs without cv2. It then carries the run on. It covers the
refusals: a cap gone down, the stopping cap not raised, another live run, and a run that
ended met. It also covers a second tail captioning only the new pass's images (two caption
jobs, disjoint ids), `can_extend`'s cases, and the rewrite reserve after a tail, read by
the real `_spent` from real child rows. The e2e journey raises the image cap from the
past run's dialog.

`backend/tests/test_run_paid_calls.py` (M9 stage 10) covers the paid-call count with
nothing stubbed but the provider call. The caption job's `done_items` after a clean job,
a job whose every call failed, a job cancelled part-way and a job that failed after a
call. `_spent` over hand-built rows in every end state, an interrupted one included. One
run with a billed captioner and a cap of five, across a failed job, a job cancelled from
outside and two resumes, making exactly five calls. And a resumed stage skipping what is
captioned, being skipped when nothing is left, and leaving alone a caption written by
hand, whether it fixed the model's or came before it. **A test of a count must run its
writer** (PM-037): this file is the only place the caption job's own write is run under
a cap. The reserve test's rows are built by hand, and its formula cancels the caption
count out while that count is at most `max_images`, so it proves the reserve's
arithmetic and nothing about the count.

## Not yet

- A run that gave up on its folders cannot carry on; start another run, which picks up
  where the folders stand, because targets are absolute.
- A first tail whose caption the paid-call cap trimmed is not made up by a later tail,
  which captions only its own passes.
- A paid call in flight when the process dies is not counted (§ Who writes the paid-call
  counts).
- **The rewrite's reserve reads caption calls as captions made.** Failed calls count, so
  after a tail with failed calls `caption_calls` can pass the number of images captioned,
  even `max_images`, and the reserve shrinks to less than the next tail needs. A run
  carried on past a cap can then spend those calls on rewriting, and its new tail trims
  and says so. No call goes over the cap. Counting captioned images
  (`captioned_by`) instead of calls would fix it.
- **The trim runs before the caption job's own filters.** `start_caption_job` still
  applies `min_aesthetic_score`, `exclude_flags` and `overwrite: False`. An image the
  trim kept and a filter then drops uses none of the calls left, while a later image the
  trim cut goes without.
- Time is checked at stage starts, so a long generate stage can overrun `max_minutes` by
  its own length.
- `sweep_old_jobs` deletes finished jobs after 30 days. A run paused that long loses the
  spend history its caps read.
