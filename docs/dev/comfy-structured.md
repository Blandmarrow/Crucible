# ComfyUI structured generation (blueprints)

**Status: living.** How a plan's blueprint turns one description into a foldered prompt
queue: the blueprint and leaf tables, the spec, the pure expansion and its template walk,
fill accounting, and Apply's guard rails. The queue itself (plans, pins, rows and the
row's `subfolder`/`leaf_id` columns) is `docs/dev/comfyui.md`; how a run files each row
is `docs/dev/comfy-run.md`; the page is `docs/dev/comfy-page.md`. The arc's remaining
stages are in `docs/dev/comfy-automation-plan/README.md`, and the rulings behind it are
decision 8 (`docs/dev/decisions/08-templates-first-filed-by-folder.md`).

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
  naming it, as provenance (`docs/dev/comfyui.md` § Concepts & data model).
- `cursor` is the next index of the leaf's template walk. It is a cursor and not a count:
  nothing in fill accounting reads it, so it cannot make a shortfall wrong.

## The spec (`backend/schemas/comfy_blueprint.py`)

`BlueprintSpec` holds:
- `axes`: name → entities `{id, label, descriptor}`
- `variations`: name → pieces
- `scenarios`: each with `name`, `combine`, `mode` (`full` | `sample:K`), `count`, `path`
  and `template`

A `combine` item is a bare axis name (take one) or `{axis, take}`.

The Pydantic model checks **structure and bounds only**. Every list and string is bounded,
because the spec is stored as JSON and returned on every read. Everything that needs the
spec as a whole is the expansion's job, so a draft with mistakes can still be saved and
shown with its errors.

## Expansion (`backend/services/blueprint_expand.py`)

**A pure service: no DB, no HTTP, no `HTTPException`.** `expand(spec, seed)` returns
`Expansion(leaves, errors, warnings)`, and errors are collected rather than raised, so the
review panel shows all of them at once. Any error discards every leaf. One function backs
both the preview and Apply, which is what makes the preview trustworthy.

- **Combinations.** A scenario is `itertools.product` over each item's
  `itertools.combinations(entities, take)`: unordered and distinct. "Alone and together" is
  `take`, not a separate mode.
- **The whole blueprint is counted before anything is enumerated.** `_prepare` validates and
  sizes every scenario with `math.comb`. Then two budgets apply across the blueprint:
  `MAX_COMBINATIONS` (100,000) combinations, and `HARD_ROW_CAP` folders. Only then does
  `_build` enumerate. Either budget exceeded is an error, never a truncation.
  - The budgets are blueprint-wide, not per scenario, because the preview re-expands on
    every debounced edit: 50 scenarios each under a per-scenario limit froze the server for
    seconds.
  - Expansion also runs in an executor (`_compute`), so it never holds the event loop.
- **Errors are capped at `MAX_ERRORS` (50)** plus one "…and N more" line. A half-typed path
  like `{a}` on the way to `{a}/{b}` makes every leaf a duplicate, which once meant 39,800
  error strings in one response.
- **`sample:K` ranks each combination by a hash of its entity ids** and keeps the K
  smallest, in enumeration order.
  - It deliberately does not call `random.sample`: that algorithm may change between Python
    versions.
  - A hash of the ids keeps most of a sample stable when an entity is added.
- **Every derived seed comes from `sub_seed`** (sha256 of the blueprint seed and a
  context), **never `hash()`**. Python randomizes `hash()` per process, so a seed built
  from it would re-expand differently after every restart. A same-process test cannot catch
  this, so `test_sample_is_stable_across_processes` asserts a hard-coded path list instead.
- **No seed includes the scenario's name.** The sample rank is keyed by the path *pattern*
  and the entity ids, and the walk by the rendered folder. Renaming a scenario is cosmetic,
  and must neither re-draw a sample (orphaning folders) nor re-seed a walk past its stored
  `cursor` (repeating prompts). A new path pattern renames every folder anyway, so it
  re-drawing costs nothing extra.
- **Paths.** `{axis}` renders the entity label's `slugify_filename` slug, and a multi-take
  axis joins slugs with `_and_` (`iron_knight_and_salt_witch`). Only combined axes may
  appear.
  - **The slug trap:** `slugify_filename` answers `"image"` for a label with no letter or
    digit. As a folder name that is silently wrong, so such a label is an error. The test is
    `[^\W_]`, not `\w`: `"_"` and `"-_-"` pass `\w` and still slug to `"image"`.
  - Two labels on one axis that slug alike are an error, and so is a path two leaves share.
  - So is an empty segment, `.` or `..`, and a path over 512 characters.
- **Templates.**
  - `{axis}` → descriptor. Several entities join with ", " and a final " and ", and an
    empty descriptor falls back to the label.
  - `{axis.label}` → label. `{variation}` → one piece.
  - A missing template joins the combined descriptors. An unknown name, or an axis the
    scenario does not combine, is an error.
- **The template walk.** The variation sets the template names make a product of P
  distinct prompts, indexed in mixed radix. A leaf's prompt *i* uses index
  `(a·i + b) mod P`, with `a` coprime to P and `a`, `b` drawn from the leaf's sub-seed.
  - It is a bijection per cycle, so a leaf's prompts differ from each other.
  - Prompt *i* never changes when more are drawn, which is what lets Top up continue a
    walk rather than repeat it.
  - A scenario whose `count` exceeds P is a **warning** (prompts repeat), not an error:
    repeated prompts still give different images under different seeds.

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
  `warnings` and `orphans`.
- `POST …/blueprint/apply` adds `confirm_total` and `delete_orphan_pending`, and returns
  `{created, leaves, deleted_rows}`. In order:
  - **400:** no prompt pin (`NO_PROMPT_PIN_MSG`).
  - **409:** a `comfy_generate` or `comfy_prompts` job is pending or running for the plan
    (`_plan_job_active`). A run loaded its rows at start and must not lose pending ones to
    the orphan delete. A prompt job seeds its dedupe set once. This comes before the spec is
    looked at, so an invalid spec during a run is a 409, not a 400.
  - **400:** any expansion error. Then `rows_to_add` over the cap: 2,000 by default, or
    20,000 with `allow_over_cap`. The total is refused, never truncated.
  - **409 when `rows_to_add ≠ confirm_total`**, so an edit that lands between review and
    Apply cannot turn a reviewed 300 rows into 30,000.
  - Then, in one commit: it saves the spec, upserts leaves by path, deletes the leaves the
    expansion no longer makes, optionally deletes orphaned pending rows, and inserts each
    leaf's rows. Each row gets `subfolder = leaf.path`, `leaf_id`, the prompt in the prompt
    pin's alias, and `sort_order` continuing the plan's. Each leaf's `cursor` advances.
- **Expansion runs inline, never as a job.** It is pure CPU plus one bulk insert under the
  cap. (The plan allowed a job for a large expansion; the cap makes every expansion
  small.)
- **One per-plan lock, `_plan_lock`, covers every writer from its 409 check to its
  commit:** Apply, the blueprint PUT, `run_plan` and `generate_prompts_job`. One process
  serves the app, so an in-process `asyncio.Lock` is enough. Each holder closes one race:
  - Two double-clicked Applies would both pass the guards and give each leaf its rows twice.
    Now the second waits, sees zero rows to add, and gets the `confirm_total` 409.
  - A run created between an Apply's check and its commit would load pending rows that the
    Apply then deletes.
  - A plan's first PUT and an Apply would both create the blueprint and hit its unique index.
- **No `declare_subfolders`.** Apply writes row folders only. A run declares its whole
  tree once, before its loop (`docs/dev/comfy-run.md`).

## Top up

`POST …/blueprint/top-up` fills every folder that came up short in one press. Its body
is Apply's (`spec`, `seed`, `confirm_total`, `allow_over_cap`, `delete_orphan_pending`)
plus the run's `subfolder`, `set_caption` and `label`. It returns `{created, reset,
deleted_rows, job_id, total}`. In order:

1. **Before anything is written:** the prompt pin 400, the run's pre-flight
   (`_run_preflight`: workflow present, ComfyUI URL set, dataset exists), and
   `normalize_subfolder` on the base folder (a `..` is a 400). Otherwise a missing URL or a
   bad folder would add rows for a run that is then refused.
2. **Under the plan lock, all the way to the run's commit:** the 409 for a live run or
   prompt job, then Apply itself, with the same `confirm_total` rule, so what runs is what
   the preview showed.
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
(`docs/dev/comfy-run.md`).

The preview carries what Top up needs to show:
- per leaf, `short` = target − kept (images, not rows)
- in total, `images_short`, `rows_pending` and `rows_failed`, summed over every leaf rather
  than the 500 listed

`backend/tests/test_blueprint_top_up_http.py` runs it against `FakeComfy`:
- the first press fills the folders, and a second does nothing
- a deleted image is refilled in its own folder only
- a failed row, or one left `running`, is reset and re-run rather than duplicated
- after an edit, the new folders run and the orphans do not
- a missing ComfyUI URL or a `..` base folder writes nothing

## The outline call

**One LLM call turns a brief into a draft blueprint**: `POST …/blueprint/outline`
(`{provider_id, model_name, brief, temperature, label}`, all bounded) queues a
`comfy_outline` job.

**Its own job type,** because it writes no rows. It never conflicts with a run or a prompt
job on the same plan, only with a second outline job: the 409 is
`_plan_job_active(…, ("comfy_outline",))`. Pre-flight matches `generate_prompts_job`:
404 for the provider, 400 for the model. `_run` re-checks both on dequeue and raises
rather than returns, since a silent return reads as success. One emit goes out before the
call, carrying `plan_id`, so TopBar can tie the job to its plan.

**The LLM function** is `ml/prompt_generator.generate_outline`. It makes a `json_schema`
attempt with the mandatory plain fallback, the same rule and reason as `generate_prompts`
(`docs/dev/comfy-prompts.md`).
- The schema asks for axes and variations as **lists of named entries**, because a dict
  with free-form keys is awkward under a strict schema. `parse_outline` turns them into
  the spec's dicts. It also accepts the dict form, a `{"blueprint": …}` wrapper, `<think>`
  blocks, fences and surrounding prose.
  - **Prose is scanned with `raw_decode` at each `{`**, never cut from the first `{` to
    the last `}`. Prose in this domain is full of `{axis}` placeholders, which make that
    slice invalid JSON.
  - A `null` section reads as empty. Two entries with one name merge rather than the
    second replacing the first, which would silently halve an axis.
  - **An outline with no axes and no scenarios is None**, so the plain fallback runs and
    the job then fails (PM-004). Counted as a draft, it would report "draft ready" and let
    *Use it* replace the user's spec with nothing.
- The `<think>`/fence stripping is `_strip_reasoning`, shared with `parse_prompts`.
  **`ParsedPrompts` is untouched** (PM-005).

**The prompt layout.** The **brief goes in the user message**, with the instruction to
write descriptors that can be pasted verbatim. The plan's saved "Generate prompts"
instructions (`gen_settings.instructions`) are passed as standing rules for the templates.
They are **read, never written**: the user owns them.

**What is saved.** The result is validated as a `BlueprintSpec` **structurally only**.
- A draft the schema cannot hold fails the job. The message names the deepest error
  location with Pydantic's union-branch tags removed (`scenarios.0.combine.0.take`, not
  `…constrained-str`).
  - The tags are an exact set, `_UNION_BRANCH_TAGS`. A pattern such as "starts
    uppercase" would also drop a user's invalid key (an axis named `Character`), the very
    thing the message must name.
- A draft with semantic errors is saved, and the review panel shows them.
- **The brief is saved when the job is queued**, creating the blueprint (empty spec,
  random seed) if needed, so a failed draft never costs a retype.
- The draft goes to `blueprint.draft`. **The spec is never touched.**
- `result_data` is `{plan_id, axes, scenarios}`.
- **Cancellation is polled once, after the call**, the only slow step. A Stop pressed
  during it saves no draft and ends the job `cancelled` (`raise_if_cancelled`). The call
  itself is not aborted; as with prompt generation, it runs to the provider's timeout.

**Using or discarding the draft.** `DELETE …/blueprint/draft` clears it (idempotent) for
both *Use it* and *Discard*. *Use it* clears it only after the spec holding it is saved
(`docs/dev/comfy-page.md`), so a failed save never loses the draft.

There is no LLM in CI. `backend/tests/test_blueprint_outline_http.py` replaces
`generate_outline` (imported lazily in `_run`, so patching the module attribute reaches
it), and `backend/tests/test_parse_outline.py` covers the parser. A real provider is
verified by hand.

## Testing

`backend/tests/test_blueprint_expand.py` covers the pure expansion: combinations, paths,
templates, every validation error, the walk's bijection and stable prefix, the
cross-process sample, and the `plan_fill` table. `backend/tests/test_blueprint_http.py`
runs the endpoints over `api_env`:
- the preview equals what Apply writes
- the 409 and 400 guard rails, and a double-clicked Apply adding its rows once
- `kept` drops when an image is deleted
- yield from multi-image rows
- orphans and their pending-only delete, no orphans for a spec with errors, and a re-made
  folder adopting its old rows
- Apply's rows continuing the plan's `sort_order`
- plan delete cascading to the blueprint and its leaves
