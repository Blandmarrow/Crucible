# ComfyUI — the goal loop

An automated run (M6 §4, `docs/dev/comfyui/automation.md`) can repeat: pass after pass it
fills the blueprint, optionally rewrites the new prompts, generates, scores and gates,
until every leaf folder holds its target of images that pass the gate, within hard caps.
This file covers the loop's shape and the `next_pass` stage, what a pass's stages do
differently from §4, the rewrite stage, and the Runs page's sections for the loop. The gate
is `docs/dev/comfyui/run-gate.md`; the budgets, carrying on past a cap and the seed check
are `docs/dev/comfyui/run-budgets.md`. The kept rule the fill and the loop share is
`docs/dev/comfyui/structured.md` § Fill accounting. The user guide is `docs/runs.md`, with
`docs/repeating-runs.md`.

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
  cap is what lets the run carry on (`docs/dev/comfyui/run-budgets.md` § Raising a cap and
carrying on).

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
4. Otherwise the caps (`docs/dev/comfyui/run-budgets.md` § Budgets: recomputed, never
   accumulated). Past one, the loop ends `short` with the reason. A pass
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
- **`gate`**: `docs/dev/comfyui/run-gate.md` § The gate.

The tail's **caption** scope is every run image that passes the kept rule
(`in_dataset(..., kept_only=True)`), whenever the run loops or gates. A plain §4 run still captions all its
images. The **export** adds `gate_rejected` to its exclusions whenever the gate is on, in
case the form was filled from an Export page whose saved filter predates the flag.


## The rewrite stage

`comfy.start_rewrite_job` is lifted out of the rewrite route, which keeps its own checks
and its confirm-budget 409 and calls it. A run calls it with:
- `scenarios`: the ones ticked on the start form. That tick is the review §3 requires.
  `_preflight_rewrite` refuses a scenario whose rewrite is off in the saved blueprint.
- `row_ids`: exactly this pass's (clamped) rows, through `select_targets`' new `row_ids`
  filter, so a row the run then leaves out is never paid for.
- `max_requests`: the paid calls left, when the provider is billed (`docs/dev/comfyui/run-budgets.md` § Budgets: recomputed, never
  accumulated).

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


## Manual Apply and Top up during a run

`comfy._refuse_live_run` answers 409 to Apply and Top up while a run on the plan is
running or paused. Rows added between passes would be counted as the run's, or filed
outside its budget. A rewrite stays allowed, because a run paused on waiting rows sends
the user to run one.

## The Runs page

`components/runs/RunLoopFields.tsx` holds three sections, and `runLoopForms.ts` their
forms and converters (kept apart so the component file exports components only). The Gate
section is `docs/dev/comfyui/run-gate.md` § The Runs page's Gate section; the other two:
- **Repeat**: the switch and the caps. The paid-call cap shows only when a billed
  provider rewrites or captions, judged from the providers' `is_billed`.
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
with Score and the gate off, ending with every folder full. `backend/tests/test_run_extend.py`
is `docs/dev/comfyui/run-budgets.md` § Testing.


## Not yet

- The image cap can be overrun on the first pass by a workflow that saves several images
  a row, since the clamp has no completed row to learn the count from.
