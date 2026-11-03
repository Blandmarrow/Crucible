# ComfyUI structured generation (blueprints)

**Status: living.** How a plan's blueprint turns one description into a foldered prompt
queue: the blueprint and leaf tables, fill accounting, orphans, Apply's guard rails and
Top up. The spec and the pure expansion that turns it into leaves (combinations, paths,
templates, the template walk) are `docs/dev/comfyui/structured-expansion.md`; the outline
call that drafts a spec with an LLM is `docs/dev/comfyui/structured-llm.md`. The queue
itself (plans, pins, rows and the row's `subfolder`/`leaf_id` columns) is
`docs/dev/comfyui/queue.md`; how a run files each row is `docs/dev/comfyui/run.md`; the
page is `docs/dev/comfyui/page.md`. The arc's remaining stages are in
`docs/dev/comfy-automation-plan/README.md`, and the rulings behind it are decision 8
(`docs/dev/decisions/08-templates-first-filed-by-folder.md`).

## The idea

**The user (or the LLM) invents entities; code does the combinatorics and writes every
prompt.** Asking a model to enumerate a 55-leaf cross product gets you about 50 leaves,
with the same character described differently in each. Asking for 10 characters and 5
biomes, then multiplying in Python, gets all 55, and each character's descriptor is
pasted verbatim into every prompt that names them.

## Tables (`backend/models/comfy.py`, migration `b7d2f4a9c1e3`)

- `comfy_blueprints`: **one per plan** (unique index `uq_comfy_blueprints_plan_id`,
  CASCADE from the plan). It holds `spec` (the dumped `BlueprintSpec`), `seed`, and the
  outline call's `brief` and `draft`. A second structure for the same dataset is a second
  plan.
- `comfy_leaves`: one row per folder the blueprint expands to. It holds `scenario`,
  `path`, `target`, `entity_ids` (`{axis: [ids]}`), `cursor` and `sort_order`. It has a
  unique index on `(blueprint_id, path)` and CASCADEs from the blueprint.
- **A leaf is its folder.** Leaf identity is the rendered path. Renaming an entity's label
  renders a new path, so it makes a new leaf. The old leaf is deleted, and its rows become
  orphans (see § Orphans). Keying leaves by entity ids instead would keep a renamed leaf's
  count, but its images would then sit in two folders.
- **A new leaf adopts the orphaned blueprint rows already filed in its folder**, matched by
  `row.subfolder`, and starts its `cursor` past them. Without that, undoing an edit (rename a
  label, then rename it back) re-makes the folder under a new leaf id with nothing counted,
  and Apply queues a second full target into a folder that is already full. Rows with no
  `leaf_id` (typed or pasted by hand) are never adopted.
- `comfy_rows.leaf_id` has no foreign key on purpose. A row whose leaf was deleted keeps
  naming it, as provenance (`docs/dev/comfyui/queue.md` § Concepts & data model).
- `cursor` is the next index of the leaf's template walk
  (`docs/dev/comfyui/structured-expansion.md` § Expansion). It is a cursor and not a count:
  nothing in fill accounting reads it, so it cannot make a shortfall wrong.

## Fill accounting (`backend/services/blueprint_service.py`)

`preview` and `apply` share `_compute`. **Every number is recomputed from rows and images
on each call**, never kept in a counter: dedupe and gallery deletes remove images, and a
stored count would quietly go wrong.

- **A target counts images.** `kept` is the number of a leaf's rows' `image_ids` that still
  exist as `Image` rows in the plan's dataset. That check runs through `utils.chunked`,
  because of the bind limit.
  - `row.image_ids` is written only by the run and never pruned, so its length is what a
    row produced, whatever has been deleted since.
  - Deleting an image in the gallery makes its leaf short again. That is the intent.
- **Open rows count as future images.** Pending, running and failed rows are each worth
  `yield` images. `yield` is the leaf's images per completed row, falling back to the
  plan-wide mean, then to 1.
- **The shortfall formula.** `plan_fill(target, kept, open, yield)` =
  `ceil(max(0, target − kept − open·yield) / yield)`.
  - Because targets are absolute, a second Apply adds nothing.
  - Neither does pressing it before a run, since the new rows are open.
- These rules are the user's (2026-11-03): targets count images, rows needed are the
  shortfall divided by yield, and pending, failed and running rows count.

## Orphans

An **orphan** is a row whose `leaf_id` names a leaf the current expansion no longer
makes, and which no new leaf adopts. The preview reports orphans by status, and the images
they hold. Orphans count toward no target.

**A spec with errors reports no orphans.** It expands to nothing, so every existing row
would read as orphaned. That is what a user halfway through typing a new entity's label
would see.

Apply takes `delete_orphan_pending`, which deletes only their **pending** rows. Completed
rows and their images are never touched, and the user removes them by hand if wanted.
This was the user's ruling on the plan's open question about edit-then-top-up.

## Endpoints and Apply's guard rails (`backend/routers/comfy.py`)

- `GET /comfy/plans/{id}/blueprint` → `{value: {spec, seed, brief, draft, updated_at} | null}`.
- `PUT …/blueprint` `{spec, seed}` saves the spec as the user edits it, creating the
  blueprint on first save. It writes no rows. The client chooses the seed for a new one.
- `POST …/blueprint/preview` `{spec, seed, allow_over_cap}` reads the spec **from the body**,
  so the live preview follows unsaved edits. It returns leaves (at most 500, and samples
  for only the first 20), `leaf_count`, `rows_to_add`, `row_cap`, `over_cap`, `errors`,
  `warnings` and `orphans`. Its three `rewrite_*` figures belong to § Top up.
- `POST …/blueprint/apply` adds `confirm_total` and `delete_orphan_pending`, and returns
  `{created, leaves, deleted_rows}`. In order:
  - **400:** no prompt pin (`NO_PROMPT_PIN_MSG`).
  - **409:** a `comfy_generate`, `comfy_prompts` or `comfy_rewrite` job is pending or
    running for the plan (`_plan_job_active` over `_ROW_JOB_TYPES`). A run loaded its rows
    at start and must not lose pending ones to the orphan delete. A prompt job seeds its
    dedupe set once. A rewrite is changing rows this would count. This comes before the spec is
    looked at, so an invalid spec during a run is a 409, not a 400.
  - **400:** any expansion error. Then `rows_to_add` over the cap: 2,000 by default, or
    20,000 with `allow_over_cap`. The total is refused, never truncated.
  - **409 when `rows_to_add ≠ confirm_total`**, so an edit that lands between review and
    Apply cannot turn a reviewed 300 rows into 30,000.
  - Then, in one commit: it saves the spec, upserts leaves by path, deletes the leaves the
    expansion no longer makes, optionally deletes orphaned pending rows, and inserts each
    leaf's rows. Each row gets `subfolder = leaf.path`, `leaf_id`, the prompt in the prompt
    pin's alias, `prompt_origin = 'template'`, and `sort_order` continuing the plan's. Each leaf's `cursor` advances.
- **Expansion runs inline, never as a job.** It is pure CPU plus one bulk insert under the
  cap. (The plan allowed a job for a large expansion; the cap makes every expansion
  small.)
- **One per-plan lock, `_plan_lock`, covers every writer from its 409 check to its
  commit:** Apply, the blueprint PUT, `run_plan`, `generate_prompts_job` and the rewrite
  route (`docs/dev/comfyui/structured-llm.md` § The rewrite pass). One process
  serves the app, so an in-process `asyncio.Lock` is enough. Each holder closes one race:
  - Two double-clicked Applies would both pass the guards and give each leaf its rows twice.
    Now the second waits, sees zero rows to add, and gets the `confirm_total` 409.
  - A run created between an Apply's check and its commit would load pending rows that the
    Apply then deletes.
  - A plan's first PUT and an Apply would both create the blueprint and hit its unique index.
- **No `declare_subfolders`.** Apply writes row folders only. A run declares its whole
  tree once, before its loop (`docs/dev/comfyui/run.md`).

## Top up

`POST …/blueprint/top-up` fills every folder that came up short in one press. Its body
is Apply's (`spec`, `seed`, `confirm_total`, `allow_over_cap`, `delete_orphan_pending`)
plus the run's `subfolder`, `set_caption` and `label`. It returns `{created, reset,
deleted_rows, job_id, total, awaiting_rewrite}`. In order:

1. **Before anything is written:** the prompt pin 400, the run's pre-flight
   (`_run_preflight`: workflow present, ComfyUI URL set, dataset exists), and
   `normalize_subfolder` on the base folder (a `..` is a 400). Otherwise a missing URL or a
   bad folder would add rows for a run that is then refused.
2. **Under the plan lock, all the way to the run's commit:** the 409 for a live run,
   prompt or rewrite job, then Apply itself, with the same `confirm_total` rule, so what
   runs is what the preview showed.
   - **Then it stops if any row waits for the rewrite** (M6 §3): a `template` row, not
     completed, in a scenario with `rewrite` on (`blueprint_rewrite.count_awaiting`). It
     returns `awaiting_rewrite: N` with `job_id: null`, having reset and run nothing, so
     switching rewrite on always means prose. The user's choice, 2026-11-03; §4 will chain
     Apply → rewrite → run. A plain *Run* is unchanged: it runs what the user queued.
3. **`blueprint_service.prepare_top_up`:** reset the current leaves' failed rows to pending
   (clearing `error_msg` and `prompt_id`, as `rows/reset` does), then list their pending
   rows in queue order.
   - Only rows of current leaves, so Top up never re-runs an orphan or a row typed by hand.
   - **The leaf ids are queried, never read off `bp.leaves`.** This runs in Apply's
     session, and with `expire_on_commit=False` an already-loaded collection is not
     reloaded: it still listed the leaves Apply had just deleted, so an edit followed by
     Top up ran the orphans instead of the new folders.
   - The failed rows were already counted as future images by the fill, and resetting
     them is what keeps that count honest.
   - **So are rows left `running`.** Under the lock, with no live run, nothing is running
     them: a hard kill left them. Skipped, a short folder would report "every folder is
     full".
4. **`_start_run(…, hold_lock=False)`** queues an ordinary `comfy_generate` run over those
   explicit row ids. `_start_run` is `POST /comfy/run`'s body, factored out. It normally
   takes the plan lock itself, and Top up already holds it, which `asyncio.Lock` (not
   re-entrant) would deadlock on.
   - The lock is held through the run's commit for a reason. Released in between, a second
     press queued on it would pass the 409 (no job yet), add nothing, and then hit the
     run's 409: an error over a run that did start.

**Nothing pending means nothing queued: `job_id: null`.** Because targets are absolute,
pressing Top up again once every folder is full adds no rows and starts no run.

It does no `declare_subfolders`: the run declares its tree once, before its loop
(`docs/dev/comfyui/run.md`).

The preview carries what Top up needs to show:
- per leaf, `short` = target − kept (images, not rows)
- in total, `images_short`, `rows_pending` and `rows_failed`, summed over every leaf rather
  than the 500 listed
- `rewrite_rows`, `rewrite_calls` and `rewrite_call_budget`: what the rewrite would face
  once this spec is applied. That is the rows still holding template text plus the rows
  Apply would add, in the scenarios with `rewrite` on, at the plan's saved batch size. The
  dialog's own figures come from `GET …/blueprint/rewrite`, which counts only rows that
  exist.

`backend/tests/test_blueprint_top_up_http.py` runs it against `FakeComfy`:
- the first press fills the folders, and a second does nothing
- a deleted image is refilled in its own folder only
- a failed row, or one left `running`, is reset and re-run rather than duplicated
- after an edit, the new folders run and the orphans do not
- a missing ComfyUI URL or a `..` base folder writes nothing

## Testing

The pure expansion's tests are `docs/dev/comfyui/structured-expansion.md` § Testing, and
the outline call's are `docs/dev/comfyui/structured-llm.md`.
`backend/tests/test_blueprint_http.py` runs the endpoints over `api_env`:
- the preview equals what Apply writes
- the 409 and 400 guard rails, and a double-clicked Apply adding its rows once
- `kept` drops when an image is deleted
- yield from multi-image rows
- orphans and their pending-only delete, no orphans for a spec with errors, and a re-made
  folder adopting its old rows
- Apply's rows continuing the plan's `sort_order`
- plan delete cascading to the blueprint and its leaves
