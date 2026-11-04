# ComfyUI — the goal loop, the gate and budgets

An automated run (M6 §4, `docs/dev/comfyui/automation.md`) can repeat: pass after pass it
fills the blueprint, optionally rewrites the new prompts, generates, scores and gates,
until every leaf folder holds its target of images that pass the gate, within hard caps.
This file covers the loop's shape and the `next_pass` stage, what a pass's stages do
differently from §4, the gate, the rewrite stage, the budgets, the seed check, and the
Runs page's sections for them. The kept rule the fill and the loop share is
`docs/dev/comfyui/structured.md` § Fill accounting. The user guide is `docs/runs.md`.

## The shape: a body that repeats, a tail that runs once

`STAGE_ORDER` (`schemas/run.py`) is split in three:
- **The head**, `HEAD_STAGES`: `snapshot` alone (M6 §6). It runs once, before the first
  pass, and carries no `pass_no`, which is what keeps `next_pass` from copying it.
- **The body**, `BODY_STAGES`: `prompts`, `rewrite`, `free_rewrite_model`,
  `handoff_to_comfy`, `generate`, `handoff_to_local`, `score`, `gate`, `next_pass`.
- **The tail**, `TAIL_STAGES`: `caption`, `free_caption_model`, `export`. It runs once,
  after the last pass. Run per pass, caption would re-caption every earlier pass's images
  (`overwrite` is on by default) and bill a paid provider again.

A recipe with `loop` settings (`LoopSettings`) gets a `next_pass` entry; nobody switches
it on, and `StageKey` leaves it out. Without `loop`, a run is §4's single pass, gate
included if chosen.

**Every stage entry carries `pass_no`** (1, 2, …), or None for a head or tail stage. A pass that
must follow is **inserted** by `next_pass`, right after itself and before the tail.
Insertion always lands after the current index and before entries that have not started,
so every started stage keeps its index. `stage_tag`'s `"<index>:<key>"` therefore stays
unique across passes, and §4's stale guard needed no change. The tail's indices shift,
but no tail job exists yet when they do. `pass_no` survives resume and the restart
reconcile, which only reset a stage's status and numbers.

**`context`** gains:
- `passes`: the row ids each pass generates. `prompts` (or `generate`, with no blueprint)
  *replaces* its own pass's slot, so a resumed pass does not append twice. `row_ids`
  stays the de-duplicated union, which `RunOut.row_count` and the caption tail read. A
  run started before §5 has `row_ids` and no `passes`; `_pass_rows` reads that as pass 1.
- `leaves`: per leaf `{path, target, kept: [history], state, reason, stalls}`, with
  `state` one of `open`, `met`, `abandoned`. `kept[0]` is the baseline pass 1's
  `prompts` records after its fill; each `next_pass` appends one (the last 12 kept).
- `leaves_pass`: the pass whose counts `leaves` holds (the idempotency guard below).
- `loop`: `{outcome: "met" | "short", reason}` once the loop has ended, plus `cap` on a
  short one: `passes`, `minutes`, `images` or `paid_calls` for the cap that stopped it
  (`_Stop`, which `_budget_stop` returns), or None when it gave up on its folders. The
  cap is what lets the run carry on (§ Raising a cap and carrying on).

`RunOut` exposes `outcome` and a capped `leaves` list (short folders first, at most
`LEAF_LIST_LIMIT` = 200) with `leaf_count` and `met_count`, both over every folder, so a
page never counts full folders from the capped list. There is no separate endpoint.

## `next_pass`

An inline stage: no job, nothing inside it commits. In order:
1. Unless `leaves_pass` already equals this pass, recount: `blueprint_service.leaf_progress`
   reads each current leaf's target and kept count with no expansion (none may run inside
   the worker's hook), and each leaf's entry is updated.
2. A leaf whose kept count reaches its target is `met`. One that gains nothing in **two
   consecutive passes in which it had rows** is `abandoned` ("no gain in two passes").
   "Had rows" means a row of this pass's `passes` slot belongs to the leaf, which
   includes rows whose rewrite failed. A leaf the image cap's round-robin left out gained
   nothing through no fault of its own, so that pass never counts against it.
3. With no leaf open, the loop ends: `met`, or `short` naming the abandoned folders.
4. Otherwise the caps (below). Past one, the loop ends `short` with the reason. A pass
   that **made no rows** ends it too (`short`, reason "the pass made no rows", no cap to
   raise): it cannot move any folder, and the next pass would plan the same nothing.
5. Otherwise the next pass's entries are inserted, **unless entries for that pass
   already exist**.

Steps 1 and 5 are what make it idempotent: a restart or a resume that runs it again for
the same pass neither recounts the history (which would count a stall twice) nor inserts
the pass twice. `test_the_pass_cap_stops_the_loop_and_next_pass_is_idempotent` holds both.

## What each body stage does in a loop

- **`prompts`** passes `exclude_leaf_ids` and `run_rewrites` (the recipe's rewrite
  scenarios, whose waiting rows then do not pause the run) to `fill_blueprint`. The
  excluded leaves are the abandoned ones **and the full ones**: rows a clamp held back stay
  pending, and once their folder fills they would otherwise run past its target in a
  later pass. **From pass 2** a looping fill passes `allow_over_cap`, because the yield
  floor can ask for 4× a folder's shortfall and push the plan past the default row cap;
  the clamp is what bounds what runs. Pass 1 keeps the cap a manual Apply has (the user's
  ruling, 2026-11-04): it is the fill Preview showed, so a blueprint too big for it is a
  mistake to refuse at the start, not growth the run made. Rows a run leaves pending are
  never deleted (the user's ruling, the same day); a manual **Run pending** can still run
  them. It records pass 1's baseline, checks the time and image caps, then
  **clamps** the pass: `_clamp_rows` takes the pending rows round-robin across leaves
  until their expected images (`_images_per_row`, produced per completed blueprint row)
  reach the images left. Rows left out stay pending, count as open in the next fill, and
  come back in a later pass. The summary says so: "… (the image cap leaves room for 3 of 4)".
- **`rewrite`**, below.
- **`free_rewrite_model`**: a `run_handoff` job, direction `after_rewrite`. It unloads the
  rewrite model through `_free_llm` (§4's `_free_caption_model`, generalised; the old
  name stays as an alias). The handoff to ComfyUI leaves an LLM alone, so without this a
  local LLM would sit in VRAM through generating.
- **`generate`** runs this pass's rows only, and **drops rows still waiting for a rewrite**
  first, before its reset loop. It uses `select_targets` with no scenario filter: no row
  of any rewrite-enabled scenario may run as template text. The list is checked again for
  emptiness, because `_start_run` widens `[]` to every pending row.
- **`score`** scores this pass's images. Every image scope (score, gate, caption) goes
  through `in_dataset`: a row's `image_ids` still names an image the user moved to another
  dataset while the run was paused. When the gate tests `has_watermark` or
  `is_nsfw`, the score stage switches on `run_watermark` or `run_nsfw` itself.
- **`gate`**, below.

The tail's **caption** scope is every run image that passes the kept rule
(`in_dataset(..., kept_only=True)`), whenever the run loops or gates. A plain §4 run still captions all its
images. The **export** adds `gate_rejected` to its exclusions whenever the gate is on, in
case the form was filled from an Export page whose saved filter predates the flag.

## The gate (`services/run_gate.py`, job type `run_gate`)

`gate_reasons(img, gate)` is pure:
- a truthy value of any flag in `exclude_flags` fails;
- an `aesthetic_score` below `aesthetic_min` fails, and so does a NULL one;
- `scores_stale` always fails: the pixels changed since they were scored.

`GateSettings.exclude_flags` may only name `GATE_FLAGS`, the flags a run's score stage can
set. `has_ai_artifacts` is set by nothing a run does, and the score stage never writes
`style_similarity_score`, so neither is offered. The recipe also refuses a gate without
the score stage, and an aesthetic minimum without aesthetic scoring.

`apply_gate` judges the images of the pass's completed rows, skipping any already
rejected. For each failure it copies `quality_flags`, sets `gate_rejected: true` and
`gate_reasons` (a payload key, not allowlisted, like `duplicate_of`), reassigns the dict,
and files the image under `join_subfolder(base, "rejects", row.subfolder)`. That is one
tree for every reject (the user's ruling, 2026-11-04), with the leaf's path kept. The
move goes through `services/image_move.move_to_subfolder(..., rename_on_move=False,
commit=False)`. Subfolders are DB labels, so no file moves, and the flags, the moves and
the job's numbers commit once.

**The flag, not the folder, is what keeps a reject out** of its leaf's kept count, the
caption tail and the export. **It goes when the image leaves `rejects/`** (the user's
ruling, 2026-11-04), so an image moved back by hand counts again.
`services/gate_flags.py` holds `REJECTS_FOLDER` (`run_gate` re-exports it) and
`clear_gate_flags_outside_rejects(db, dataset_id)`. The helper copies and reassigns
`quality_flags` without `gate_rejected` and `gate_reasons` for every flagged image of the
dataset whose subfolder has no `rejects` segment, and it does not commit. Every writer of
`Image.subfolder` calls it after the write: `image_move.move_to_subfolder` (both
branches; the rename branch does it as its own step after the loop, so nothing fallible
sits between a rename and its commit, PM-013), `dataset_service.repath_subfolder` and
`delete_subfolder`, and `images.py`'s `batch_move_dataset` and `batch_copy_dataset` for
the destination dataset. **The gate itself passes `clear_gate_flags=False`**: it flags
every reject before its first move, and the later targets' flags would otherwise be
cleared while still outside `rejects/`. Version restore deliberately does not clear:
it restores flags and subfolder together. `test_gate_flag_clearing.py` has a test per
writer. A new writer of `Image.subfolder` belongs in that list.

**The gate job honours a cancel.** `_stage_gate`'s job checks `raise_if_cancelled` before
the work and again before its one commit (rolling back first), so a cancel commits nothing.
**The stage needs no plan**: only `rewrite` and `next_pass` refuse a deleted plan.

**A known edge:** the duplicate scan does not know about the gate. A group's root can be
a rejected image, which leaves that group with nothing kept. That is accepted.

## The rewrite stage

`comfy.start_rewrite_job` is lifted out of the rewrite route, which keeps its own checks
and its confirm-budget 409 and calls it. A run calls it with:
- `scenarios`: the ones ticked on the start form. That tick is the review §3 requires.
  `_preflight_rewrite` refuses a scenario whose rewrite is off in the saved blueprint.
- `row_ids`: exactly this pass's (clamped) rows, through `select_targets`' new `row_ids`
  filter, so a row the run then leaves out is never paid for.
- `max_requests`: the paid calls left, when the provider is billed (below).

In a **looping** run (`sit_out_failures`), the job **completes** when folders fail, even
every folder. It raises only on a provider or write error, or a cancel. Failed folders
keep their template rows, which `generate` then drops, so those folders sit the pass out
and the next pass's rewrite retries them. A run that does not loop has no next pass, so
PM-004's rule holds there as it does outside a run: nothing rewritten is a failure, and
the run fails rather than ending green with nothing made.

The job writes the calls made so far to its row after every batch, not only at the end,
so a job killed mid-pass still counts against the paid-call cap. The recipe refuses a
rewrite stage without the fill: it rewrites the rows the fill adds.

Rows of a scenario with rewrite on but not ticked still pause the run at `prompts`, as in
§4 (`fill_blueprint`'s `run_rewrites` exempts only the ticked ones). That covers a
scenario switched on while the run is going.

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
  provider is billed;
- **images**: every image the run's rows produced (`run_image_ids` over `row_ids`).

Stage entries are not the record: resume and the restart reconcile wipe a failed stage's
numbers, and a failed rewrite's calls were still made.

**Billed** is `schemas/openai_provider.provider_is_billed`: the provider's `billed`
setting, or whether its URL is remote when that is NULL (`docs/dev/llm-providers.md`).
Ollama and the local captioners are never billed.

When they are checked:
- **Before `prompts`, `rewrite` and `generate`** (`_budget_stop`): time and images.
- **At `next_pass`**: the pass cap, then time and images.
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

A check alone cannot stop a generate stage partway, which is why the image budget is also
**the clamp** in `prompts`.

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

## Manual Apply and Top up during a run

`comfy._refuse_live_run` answers 409 to Apply and Top up while a run on the plan is
running or paused. Rows added between passes would be counted as the run's, or filed
outside its budget. A rewrite stays allowed, because a run paused on waiting rows sends
the user to run one.

## The Runs page

`components/runs/RunLoopFields.tsx` holds three sections, and `runLoopForms.ts` their
forms and converters (kept apart so the component file exports components only):
- **Repeat**: the switch and the caps. The paid-call cap shows only when a billed
  provider rewrites or captions, judged from the providers' `is_billed`.
- **Gate**: the `GATE_FLAGS` checkboxes (from `FLAG_OPTIONS`) and the aesthetic minimum,
  disabled while aesthetic scoring is off.
- **Rewrite**: the blueprint's rewrite-on scenarios as checkboxes, the provider and the
  `ModelPicker`.

`NEEDS_STAGE` (in `runRecipeForm.ts`) makes the gate depend on Score, `rewrite` on
Fill the blueprint (`prompts`) and `free_rewrite_model` on Rewrite, as
`free_caption_model` depends on Caption. `blockers` gains one reason for each section.
`StageList` puts a "Before the first pass" heading before the head stage, a "Pass N"
heading before each pass, and "After the last pass" before the tail, and `LoopOutcome` shows the ending and up to 20 folders still short.

## Testing

`backend/tests/test_run_loop.py` drives looping runs against `FakeComfy(varied=True)`,
which draws each picture from the workflow's seed, so different seeds do not read as
duplicates (`docs/dev/comfyui/run.md` § Testing the run). A descriptor carrying the fake's
blurry marker makes a folder whose images always fail the blur test. Its cases: refilling
rejects until a stuck folder is abandoned (rejects filed and left out of the export); one
pass when everything passes; the pass cap and `next_pass`'s idempotency; the image cap's
round-robin clamp; the seed refusals; the paid-call checks; Apply and Top up refusing
during a run; the rewrite stage with one folder failing, and failing a run that does not
loop when nothing was rewritten; the row cap holding on pass 1 and lifting from pass 2; a
pass that made no rows stopping the loop; `_stop_loop` keeping a handoff and `_spent` ignoring an
interrupted job's downtime; and a billed caption tail captioning what the cap allows. The gate's blur tests need
cv2 (`needs_cv2`). `frontend/e2e/runs.spec.ts` runs one repeating journey from the form,
with Score and the gate off, ending with every folder full.

`backend/tests/test_run_extend.py` (M6 §6) stops a loop on its image cap, which needs no
score or gate, so it runs without cv2. It then carries the run on. It covers the
refusals: a cap gone down, the stopping cap not raised, another live run, and a run that
ended met. It also covers a second tail captioning only the new pass's images (two caption
jobs, disjoint ids), `can_extend`'s cases, and the rewrite reserve after a tail. The
e2e journey raises the image cap from the past run's dialog.

## Not yet

- A run that gave up on its folders cannot carry on; start another run, which picks up
  where the folders stand, because targets are absolute.
- A first tail whose caption the paid-call cap trimmed is not made up by a later tail,
  which captions only its own passes.
- Time is checked at stage starts, so a long generate stage can overrun `max_minutes` by
  its own length.
- The image cap can be overrun on the first pass by a workflow that saves several images
  a row, since the clamp has no completed row to learn the count from.
- `sweep_old_jobs` deletes finished jobs after 30 days. A run paused that long loses the
  spend history its caps read.
- The gate's `run_gate` job is in `TopBar`'s `IMAGE_MODIFYING_JOB_TYPES` and the Stats
  page's live set, so the gallery and the `gate_rejected` count refresh when it ends.
