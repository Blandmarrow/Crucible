# M6 plan — ComfyUI structured generation and automated dataset runs

**Status: living.** Scoped in a design session on 2026-08-15 and revised on 2026-11-03, when
the per-row folder work (§1) was ported to `main`'s layout on `feat/m6-comfy-automation`. Two
asks share one spine: generating a *foldered* prompt set from one sentence, and running a whole
dataset end to end without babysitting it. The first is useful on its own; the second depends
on it and holds the architectural risk.

**The whole build stays on one branch and merges once**
([decision 7](../decisions/07-m6-built-whole-on-one-branch.md)). The order of work is
[roadmap.md § M6](../roadmap.md#m6--comfyui-per-row-folders-and-automation); this file owns the
*what* of each stage, and the 2026-11-03 revisions are
[decision 8](../decisions/08-templates-first-filed-by-folder.md) (templates, folders, Top up) and
[decision 9](../decisions/09-runs-step-forward-on-job-completion.md) (the stepping run).

**Lifecycle:** this folder is transient (`TRANSIENT_DIRS` in `scripts/check_docs.py`), so it has
no Map row and no word budget. When a stage lands, move its durable rationale into the topic file
named in § Documentation this arc owes and delete the stage from here; delete the folder when
everything is done.

Nothing below §1 is decided by the code yet. Read `CLAUDE.md`, `docs/dev/comfyui.md`,
`docs/dev/comfyui/run.md` and `docs/dev/comfyui/prompts.md` before implementing, and prefer the repo's conventions over the
sketches here.

## What this arc is

A plan holds a list of prompt rows, and since §1 each row carries its own destination folder, so
a run files its outputs into a tree. But the rows are still written or generated one flat batch
at a time, so the *structure* is whatever you typed.

After §2 you describe a dataset once — *"a medieval set, N characters, each in M biomes alone
and together, K images per scenario"* — review the taxonomy the model proposes, edit it, and get
a foldered dataset whose structure you chose. Top up then fills any folder that came up short.
After §4 and §5 the same description drives a run that generates, scores, dedupes, gates and
captions on its own, and keeps going until each folder holds K images that pass your bar.

The load-bearing idea is that **the LLM invents entities and code does the combinatorics**.
Asking a model to enumerate 55 leaves of a cross product gets you 50 of them, with the character
described differently in each. Asking it for 10 characters and 5 biomes, then multiplying in
Python, gets you all 55 and the same character every time. Decision 8 takes that one step
further: **code also writes the prompts**, from a template, so the LLM is called once per
blueprint rather than once per leaf.

## Sections

**Section numbers are stable identities, not an order.** The build order is the roadmap's M6
checklist alone. In short: the `docs/dev/comfyui.md` split and §7 (both done 2026-11-03), then
§2a, §2b, Top up (§2c), §3, a rebase after M8 stages 2–3, §4 (all landed), then §5, §6
and the merge.

**§1 (per-row `subfolder` + `leaf_id`) is ported**, commit 8dee5de on the branch; its rationale
lives in `docs/dev/comfyui/queue.md` (the columns and endpoints) and `docs/dev/comfyui/run.md` (how a run
files each row). The `comfy_leaves` table it left for later is §2a's to build:
`leaf_id` is a column with no writer.

**§7 (the mock ComfyUI server) has landed**, so every later stage tests against it:
`backend/tests/fake_comfy.py`, documented in `docs/dev/comfyui/run.md` § Testing the run. pytest
reaches it in-process; Playwright reaches it on a real port. Each stage that adds a journey
extends `frontend/e2e/comfy-run.spec.ts` until the whole arc runs there GPU-free, ComfyUI-free
and LLM-free: hand-authored blueprint → expand → run → foldered gallery → Top up. `POST /free`
is already served and recorded, for §4's VRAM handoff.

**If the arc has to be cut short, cut after Top up.** Foldered datasets that refill themselves
on request are most of the value; a manual "run these stages in order" habit covers the rest.

## Decisions

Recorded so an older draft cannot reintroduce them.

- **Axes, not leaves.** The outline call returns entities and combination rules; expansion is
  deterministic code. See §2a.
- **Templates, not per-leaf calls** (decision 8). The outline also returns variation pieces, and
  code composes every prompt. A per-leaf LLM rewrite is §3, and optional.
- **Folders, not labels** (decision 8). A leaf is filed by its rendered path; `comfy_leaves`
  records which entities it combines, so "which character is this image" stays answerable
  through image → row (`image_ids`) → leaf (`leaf_id`).
- **"Individually and together" is `take: N` on an axis**, expanded with `itertools.combinations`,
  not a separate scenario mode. One mechanism, not two.
- **Per-leaf targets are absolute**, never "N more" — the property the existing `comfy_prompts`
  job already has, applied per leaf. It is what makes Top up fill only what is missing, and it is
  the foundation of resume (§4) and the goal loop (§5).
- **Top up before the goal loop** (decision 8).
- **An automated run is a `Run` row stepped forward on job completion** (decision 9), with stages
  calling service functions.
- **The automated run gets its own dataset-scoped routed page**, not a third `ComfyPage` tab. The
  run spans captioning, scoring, gating and export; only its first stages are about ComfyUI. The
  counter-argument — that a tab would dodge the routed-page checklist in
  `docs/dev/panes-routing.md` — was heard and rejected. It costs that checklist plus
  `PaneHeader`'s `PAGE_OPTIONS`.
- **The gate moves failures to a `rejects/` subfolder. It does not delete.** Reversible,
  inspectable, and it composes with the per-row folders.

### Rejected

- **An LLM call per leaf by default**, and **labels per axis value** — decision 8.
- **A second `JobQueue` with a parent job that polls its children**, and **calling router
  handlers in-process** — decision 9. The traps the old design carried (a long-lived session
  that never sees the child finish, the test harness's loop-bound second queue, a parent
  cancelled while its child runs) went with it.
- **A `pair:K` scenario mode.** Subsumed by `take: N`.
- **A new job type for template expansion.** Expansion runs inline (§2a).
- **The rewrite pass reusing `comfy_prompts`** (overturned by the user, 2026-11-03). Reuse would
  have kept the 409 guards for free, but that job appends rows and reports "N generated", while
  the rewrite updates rows and reports different numbers. The job, `TopBar` and
  `jobReconcile.ts` would all have branched on a mode flag. It is `comfy_rewrite`, added to the
  four guards by hand (§3).
- **Inlining every stage into one giant job.** It would lift each stage's `_run` closure out of
  its router with high regression risk, and throw away every existing preflight check.
- **Making the main queue multi-worker.** The single worker is what serializes GPU work. Two
  workers let captioning and a ComfyUI run hit the same card.
- **A snapshot stage on by default.** It is gated on versioning, which most installs have off,
  and snapshot jobs are deliberately non-cancellable. Opt-in, and it *skips with a recorded note*
  when versioning is off.
- **Dedupe-with-delete on by default.** `resolve_duplicates` deletes rows and unlinks files; an
  unattended loop gets `flag` by default and `resolve` only as a choice.

## 2. Structured generation — axes, templates, Top up

Read `docs/dev/comfyui/prompts.md`.

### §2a — Blueprints, expansion and templates (landed)

Built on 2026-11-03. Everything durable — the tables, the spec, the pure expansion and its
walk, fill accounting, orphans, and Apply's guard rails — now lives in
`docs/dev/comfyui/structured.md`.

Two departures from the sketch that stood here:
- **Expansion runs inline and is never a job.** Its row cap makes every expansion small. The
  mutual exclusion the sketch got from reusing `comfy_prompts` comes from Apply's 409 on a
  live `comfy_generate` or `comfy_prompts` job, plus a per-plan lock.
- **`sample:K` ranks combinations by a hash of their entity ids** rather than seeding
  `random.sample`, so a Python upgrade cannot re-sample a blueprint.

§3's rewrite pass is the slow part, so it is a job of its own, `comfy_rewrite`.

### §2b — The outline call and the review panel (landed)

Built on 2026-11-03. The review panel arrived with §2a, and it is reachable without an LLM,
which is how the e2e journey reaches it. The outline call is the `comfy_outline` job; it saves
a draft on the blueprint for *Use it* / *Discard*. Both are documented in
`docs/dev/comfyui/structured-llm.md` § The outline call and `docs/dev/comfyui/page.md`.

Grouping rows by leaf in `ComfyRowsTable` was optional and is not built: the Folder column
already shows each row's leaf path.

### §2c — Top up (landed)

Built on 2026-11-03: `POST …/blueprint/top-up` is Apply, then reset the blueprint's failed
rows, then run its pending ones through `_start_run`. It is documented in
`docs/dev/comfyui/structured.md` § Top up. §5's loop is this, repeated with gates; §4 calls the
same service functions (`blueprint_service.apply`, `prepare_top_up`).

Rows whose leaf a blueprint edit deleted were settled with §2a (the user's ruling,
2026-11-03): they count toward nothing, the preview reports them, and Apply can delete their
pending rows only. Top up reuses Apply, so it inherits that. Shortfall counts images; pending,
running and failed rows count as future images; and Top up resets failed rows to pending
before running (`docs/dev/comfyui/structured.md` § Fill accounting).

## 3. The optional LLM rewrite pass (landed)

Built on 2026-11-03, with the outline's instruction-order fix. Everything durable is in
`docs/dev/comfyui/structured-llm.md` § The rewrite pass; `prompt_origin` is in
`docs/dev/comfyui/queue.md`, and Top up's refusal in `docs/dev/comfyui/structured.md` § Top up.

The user settled four points that day: the pass is its own job type (`comfy_rewrite`), Top
up refuses to run rows still waiting for it, hand edits are protected by `prompt_origin`,
and leaves are rewritten one at a time.

Departures from the sketch that stood here, all from the review before it merged:
- **A strict parser of its own**, `parse_rewrite`, instead of `parse_prompts`. That parser's
  line-splitting fallback accepted a reply cut off at the token limit as one prompt.
- **Any 4xx other than 401, 403 or 404 fails one leaf rather than the run**, and a refused
  schema never fixes the mode by itself.
- **The row routes that write `values` 409 while a rewrite is live.** The table PATCHes a
  whole cached row, so an edit during the pass undid it.
- **The job rewrites only the scenarios the user reviewed**, stored in its config.

## 4. The stepping run (landed)

Built on 2026-11-04. Everything durable — the `Run` row, `advance_run` and the worker's
`_after_terminal` hook, the stages, cancel, pause, resume and restart, the VRAM handoff and
the Runs page — is in `docs/dev/comfyui/automation.md`. The start functions §4 needed
(`start_score_job`, `start_caption_job`, `start_export_job`, `fill_blueprint`) sit beside
their job closures in their routers, as `_start_run` already did.

The user settled three points before it was built (2026-11-04): the **gate** moves to §5,
with the loop; the **snapshot** stage moves to §6, with the recipe library; and a run's
settings come from a **compact form** on the Runs page, stored on the row as its recipe.

Departures from the sketch that stood here:
- **No separate dedupe stage.** Duplicate flagging is already the tail of a technical score
  (`_flag_duplicates`, dataset-wide), so the score stage always runs technical. `resolve`
  is never run.
- **Rows waiting for a rewrite pause the run** rather than starting a `comfy_rewrite` job:
  the job rewrites only the scenarios the user reviewed, and an unattended run cannot
  review them.
- **Every stage that does work is a job**, the VRAM handoff included (`run_handoff`), so
  `advance_run` itself only records and enqueues, and nothing slow runs inside the hook.
- **The hook also runs on the cancelled-while-queued path**, which skips `_finish`.
- **An LLM is unloaded after captioning, by a stage of its own** (`free_caption_model`, the
  user's request after the first hand runs, 2026-11-04). LM Studio's OpenAI-compatible API has
  no unload, but its native REST API (0.4 and later) does. The handoffs before generating
  still leave an LLM alone.
- **Export takes no id list**: it exports the dataset through its filters, duplicates left
  out by default, which keeps it clear of the `[]` trap below.

## 5. Goal loop, gates, budgets

The loop: compute each leaf's shortfall (§2c) → top up → generate → score (which flags
duplicates) → gate → recompute → repeat. Each iteration is a pass through §4's stages; the
loop is `advance_run()` returning to the `prompts` stage rather than finishing. §4's
`stage_tag` already carries the stage's index, so a job from an earlier pass of the same
stage key reads as stale. `context.row_ids` then needs to hold row ids per iteration, not
one flat list.

- **Hard caps are required fields, not options**: max iterations, max images, max LLM calls, max
  wall-clock. Each iteration burns GPU time, and a stop condition can fail to converge.
- **Check the budget before each expensive stage, never after.** Checking after is how a run
  overspends by a whole generate stage.
- **Recompute "kept" from the DB every iteration; never accumulate it.** A resolve-mode dedupe
  deletes rows and `scores_stale` can invalidate a score.
- **Abandon a leaf that gains nothing across two consecutive iterations.**
- The gate is a pure predicate over `Image` columns, reusing the quality router's flag-key
  vocabulary. It **moves** failures to `rejects/`. A new stage key `gate`, between `score` and
  `caption` in `STAGE_ORDER`, run as a small job of its own like `run_handoff`. The only
  move-to-subfolder code today is `images.py`'s `batch_move_subfolder` handler, so lift a
  start function out of it as §4 did for score and caption, called with
  `rename_on_move=False` (a pure `Image.subfolder` update) and never with an empty id list,
  which it 404s.
- **Forbid in-place pixel ops in a recipe for this phase.** `utils.record_in_place` marks scores
  stale, and a gate reading those scores would judge deleted pixels. If they are ever allowed, the
  gate must treat a stale score as not-kept.

## 6. Dry run and the recipe library

**The dry run calls the same planning function the real run calls** — the expansion service and
the stage list — so its estimate cannot drift. Preflight everything reachable while it is cheap:
ComfyUI ping, provider and model, the plan's prompt pin, export directory writability, disk
headroom for N images.

**The snapshot stage lands here** (moved from §4 by the user, 2026-11-04): opt-in, first in
`STAGE_ORDER`, and it *skips with a recorded note* when versioning is off. Snapshot jobs are
deliberately non-cancellable, so a run's Cancel during it must wait for the snapshot rather
than leave the run marked cancelled while the job still writes; and a resumed run that
re-runs it makes a second snapshot.

§4's start form is the recipe's first shape: `schemas/run.RunRecipe`, stored on `Run.recipe`.
Recipes are **global — no dataset or plan foreign key** — for the same reason
`comfy_library_prompts` is: a recipe describes a process, not a dataset. JSON export and import.

**A recipe must never persist a provider API key, an absolute path from another machine, or a
plan id.** Validate that on load as well as on save; a recipe is a document that can arrive from
elsewhere.

## Documentation this arc owes

`docs/dev/comfyui.md` went over budget at ~4,440 words after §1 and was split on 2026-11-03:
the run body is now `docs/dev/comfyui/run.md` and the page `docs/dev/comfyui/page.md`. Later stages
that edit the run body document it in `docs/dev/comfyui/run.md`. Since 2026-11-03 the
family sits behind the hub `docs/dev/comfyui.md`, so each new file goes in
`docs/dev/comfyui/` with a row in the hub's tables, not a Map row:

- Structured generation, written and then split three ways on 2026-11-03:
  `docs/dev/comfyui/structured.md` (blueprints, fill accounting, orphans, Apply, Top up),
  `docs/dev/comfyui/structured-expansion.md` (the spec, expansion and its guard rails) and
  `docs/dev/comfyui/structured-llm.md` (the outline call, and §3's rewrite pass since
  2026-11-03). §1's two columns stay documented beside the row model;
  cross-reference them rather than moving them.
- `docs/dev/comfyui/automation.md`, written with §4 (2026-11-04). §5's goal loop, gate and
  budgets, and §6's dry run and recipes, extend it.

`docs/dev/backend-infrastructure.md` § After a job ends: the run hook, written with §4. User docs are owed in the same change as each feature — `docs/comfyui.md`
or a new page, plus a row in `docs/features.md` and whatever `README.md` needs. Run
`python scripts/check_docs.py` and `python scripts/check_migrations.py` on every stage.

## Still open

- Whether an entity filter (built on `comfy_leaves`, scoped to a blueprint) is wanted in the
  gallery. Not built until someone misses it (decision 8).

Settled 2026-11-03 by the user: per-leaf targets are edited per scenario in the UI and stored
per leaf in the DB; a deleted leaf's rows count toward nothing (see §2c). Also settled that day:
§3 rewrites one leaf at a time, with no concurrency setting.

## Traps worth restating

- **`declare_subfolders` commits.** Called inside the import loop it commits a half-written row
  and breaks the per-row rollback. §1 hoists it above the loop; §2c and §4 are the next places
  that could reintroduce it.
- **`advance_run()` raising into the worker** (§4). It runs after the job's commit; an exception
  there must be logged and swallowed, or a finished job looks failed.
- **An accumulated counter** (§2c, §5). Shortfall and "kept" are recomputed from the DB, because
  dedupe deletes rows and stale scores stop counting.
- **Checking a budget after the stage it was meant to prevent** (§5).
- **A silently wrong path segment from a slug fallback** (§2a). `slugify_filename` answers
  `"image"` for an all-punctuation label.
- **An LLM asked to enumerate a cross product** (§2a). It returns most of it, and describes the
  same entity differently each time.

- **An empty id list that widens to the whole dataset** (§4). `score_quality`, style
  similarity, `run_captioning`, `run_pipeline` and the exports test `if body.image_ids:`, so a
  stage that passes `[]` scores or captions every image in the dataset. A stage with no ids is
  skipped, never sent `[]` (the cleanup plan's § Found during stages 2 and 3 records it).

Every one of these fails quietly. None produces an exception or a visibly wrong number.
