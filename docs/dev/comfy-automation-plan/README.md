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
`docs/dev/comfy-run.md` and `docs/dev/comfy-prompts.md` before implementing, and prefer the repo's conventions over the
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
§2a, §2b, Top up (§2c), §3, a rebase after M8 stages 2–3, §4, §5, §6, then the merge.

**§1 (per-row `subfolder` + `leaf_id`) is ported**, commit 8dee5de on the branch; its rationale
lives in `docs/dev/comfyui.md` (the columns and endpoints) and `docs/dev/comfy-run.md` (how a run
files each row). The `comfy_leaves` table it left for later is §2a's to build:
`leaf_id` is a column with no writer.

**§7 (the mock ComfyUI server) has landed**, so every later stage tests against it:
`backend/tests/fake_comfy.py`, documented in `docs/dev/comfy-run.md` § Testing the run. pytest
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
- **A new job type for template expansion or the rewrite pass.** Expansion runs inline (§2a);
  the rewrite pass reuses `comfy_prompts`.
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

Read `docs/dev/comfy-prompts.md`.

### §2a — Blueprints, expansion and templates (landed)

Built on 2026-11-03. Everything durable — the tables, the spec, the pure expansion and its
walk, fill accounting, orphans, and Apply's guard rails — now lives in
`docs/dev/comfy-structured.md`.

Two departures from the sketch that stood here:
- **Expansion runs inline and is never a job.** Its row cap makes every expansion small. The
  mutual exclusion the sketch got from reusing `comfy_prompts` comes from Apply's 409 on a
  live `comfy_generate` or `comfy_prompts` job, plus a per-plan lock.
- **`sample:K` ranks combinations by a hash of their entity ids** rather than seeding
  `random.sample`, so a Python upgrade cannot re-sample a blueprint.

§3's rewrite pass is still the slow part, and it still reuses `comfy_prompts`.

### §2b — The outline call and the review panel (landed)

Built on 2026-11-03. The review panel arrived with §2a, and it is reachable without an LLM,
which is how the e2e journey reaches it. The outline call is the `comfy_outline` job; it saves
a draft on the blueprint for *Use it* / *Discard*. Both are documented in
`docs/dev/comfy-structured.md` § The outline call and `docs/dev/comfy-page.md`.

Grouping rows by leaf in `ComfyRowsTable` was optional and is not built: the Folder column
already shows each row's leaf path.

### §2c — Top up

Per leaf, **shortfall = target − completed images filed under that leaf**, counted from the DB
(images reached through the leaf's rows' `image_ids`), never from a stored counter. Top up shows
the shortfall per leaf and in total, adds the missing rows from the same seeded template walk
(continuing past the prompts already used), and runs only the pending rows. Because targets are
absolute, pressing it twice does nothing the second time. §5's loop is this, repeated with gates.

Rows whose leaf a blueprint edit deleted were settled with §2a (the user's ruling,
2026-11-03): they count toward nothing, the preview reports them, and Apply can delete their
pending rows only. Top up reuses Apply, so it inherits that. Shortfall counts images; pending,
running and failed rows count as future images; and Top up resets failed rows to pending
before running (`docs/dev/comfy-structured.md` § Fill accounting).

## 3. The optional LLM rewrite pass

Read `docs/dev/comfy-prompts.md`, `docs/dev/llm-providers.md`, `backend/ml/prompt_generator.py`.

Templates make this pass optional (decision 8): it rewrites a leaf's composed prompts into prose
for natural-language models, sending the composed prompts and the descriptors as input. Off by
default per scenario. The current generation loop was tuned for one flat run of up to 200 prompts
and behaves badly applied per leaf. Facts first, checked on 2026-11-03:

- **One "call" is up to two HTTP requests.** The `json_schema` attempt falls back to a plain
  call whenever it errors *or* parses to zero prompts. On LM Studio with a thinking model the
  schema attempt returns empty content, so the fallback is the normal path.
- **The call cap is `max(12, ceil(total/batch)*3)`** (`backend/routers/comfy.py`), sensible for a
  200-prompt run and absurd per 10-prompt leaf.
- **There are no retries** — `max_retries=0` on the client, on purpose, so the provider's own
  timeout is the real ceiling (`docs/dev/llm-providers.md`). The timeout is per provider now
  (4b44072), no longer a 120 s constant; `max_tokens` is floored at 8192.

Changes:

- **Probe the schema path once per run**, then stick with whatever worked.
- **Per-leaf cap `ceil(target/batch) + 2`**, plus a run-level call budget shown in the preview and
  enforced as a hard stop.
- **Bounded retry with jittered backoff** on timeout, connection reset, 5xx and 429, in the pass's
  own loop (the client keeps `max_retries=0`). Never retry a schema rejection. **Poll the cancel
  flag inside the backoff and before each attempt.**
- **Per-leaf failure isolation** with a run-level threshold (abort past roughly a quarter of
  leaves failed). A failed leaf keeps its template prompts, so the pass can only improve rows,
  never lose them.

**Do not widen `ParsedPrompts` to carry probe state.**
`docs/dev/postmortems/PM-005-tuple-return-widened-broke-caller.md` is that mistake. Probe state
belongs on a caller object that owns the connection for the run; keep the module-level function
as a thin wrapper so the one-shot endpoint is untouched.

## 4. The stepping run

Read `docs/dev/backend-infrastructure.md`, `docs/dev/frontend-jobs.md`,
`docs/dev/panes-routing.md`, `docs/dev/model-residency.md`. Decision 9 is the ruling.

**Prerequisite:** M8 stages 2 (one helper for a job's image scope) and 3 (one shared loop for
upscale and LUT) on `main`, and this branch rebased onto them. They move each job's preflight
and enqueue into service functions the stages can call.

### The shape

`backend/workers/job_queue.py`'s worker runs one job to completion before taking the next, so
nothing on that queue may wait for another job on it. Instead of waiting, a run **steps**:

- A `Run` row holds the dataset, the plan, the recipe (its stage list and settings), the index of
  the current stage, a status (`running`, `paused`, `completed`, `failed`, `cancelled`) and a short
  per-stage log.
- Starting a run enqueues the first stage's job with `config["run_id"]`.
- When any job carrying a `run_id` reaches a terminal state, the worker calls
  `advance_run(run_id, job)` **after the job's own commit**, in its own session, inside a
  `try/except` that logs and can never change the job's outcome — CLAUDE.md's "nothing fallible
  after the commit" rule (PM-013). It reads the job's status and `result_data`, records the
  stage's outcome, and either enqueues the next stage, or marks the run `completed`, `failed`,
  `cancelled` or `paused`.
- A stage that does its work directly (a gate query, a VRAM handoff) runs inside
  `advance_run()`'s step, or as a small job of its own if it can be slow.
- **A stage whose service answers "nothing in scope" is skipped**, not waited on. Write that test
  first.

### Cancellation, pause and resume

- **Cancel** cancels the current child through the normal job cancel and marks the run
  `cancelled`; `advance_run()` sees the cancelled child and does not enqueue. `TopBar`'s
  Cancel-all reaches the child directly, and the run reads that as a stage failure.
- **Pause** marks the run so `advance_run()` stops at the next stage boundary; the running child
  finishes.
- **Restart:** `mark_interrupted_jobs()` fails every running job at startup. A run whose current
  child is failed that way is set to `paused` with a resume hint — reconcile in the lifespan,
  right after it.
- **Resume** is explicit, never automatic, and calls `advance_run()` from the first stage not
  done. Per-stage idempotence comes from the absolute per-leaf targets and the pending-rows-only
  run. The exceptions: a re-run `snapshot` makes a second snapshot, and `export` rewrites its
  output.

### Stages

| Stage | Shape |
|---|---|
| `snapshot` | direct; opt-in, skips with a note when versioning is off |
| `prompts` | job — §2a expansion / Top up, and §3 if on |
| `vram_handoff` | direct; both directions, can appear twice |
| `generate` | job (`comfy_generate`) |
| `score` | job, scoped by image ids |
| `dedupe` | job + policy; default `flag`, never `resolve` |
| `gate` | direct; a query plus moves to `rejects/` |
| `caption` | job, scoped by image ids |
| `export` | job |

### The VRAM handoff

On a single-GPU box the run alternates between ComfyUI and local torch models, so both directions:

- **Crucible's models:** `model_manager.evict_all()` frees every idle model and skips leased ones
  (`docs/dev/model-residency.md`). Between stages no job holds a lease, so it frees everything; if
  `leased_ids()` is not empty, record it in the stage log rather than failing.
- **ComfyUI's models:** a new best-effort `ComfyClient` call to ComfyUI's `/free`, before scoring.
  `ComfyClient` has no such method yet. Verify the endpoint on the target ComfyUI build, and treat
  any error as a no-op with a note — a missing `/free` must never fail a run.
- **The LLM:** an Ollama provider unloads with `keep_alive: 0`. **LM Studio does not expose unload
  over its OpenAI-compatible API**, so that case is a TTL the user sets in LM Studio, not
  something this stage can do over HTTP.

### Run context

Hold **row ids per iteration, not image ids**. The images an iteration produced are a query over
the rows' `image_ids`; storing thousands of uuids in a JSON column returned on every poll is a
large response. The generate job's `created_image_ids` is a cross-check, not the source of truth.

### Frontend

A new dataset-scoped routed page (see § Decisions): the six-site checklist in
`docs/dev/panes-routing.md` plus `PaneHeader`'s `PAGE_OPTIONS` and `NEEDS_DATASET`. It reads the
`Run` row and its current child's progress through the existing job stream.

**The run itself is not a job, and its children stay in their own job-type sets** —
`LIVE_IMAGE_JOB_TYPES` and `IMAGE_MODIFYING_JOB_TYPES` already fire every invalidation for them.
Tag each child with its run in `TopBar` rather than hiding it.

## 5. Goal loop, gates, budgets

The loop: compute each leaf's shortfall (§2c) → top up → generate → score → dedupe → gate →
recompute → repeat. Each iteration is a pass through §4's stages; the loop is `advance_run()`
returning to the `prompts` stage rather than finishing.

- **Hard caps are required fields, not options**: max iterations, max images, max LLM calls, max
  wall-clock. Each iteration burns GPU time, and a stop condition can fail to converge.
- **Check the budget before each expensive stage, never after.** Checking after is how a run
  overspends by a whole generate stage.
- **Recompute "kept" from the DB every iteration; never accumulate it.** A resolve-mode dedupe
  deletes rows and `scores_stale` can invalidate a score.
- **Abandon a leaf that gains nothing across two consecutive iterations.**
- The gate is a pure predicate over `Image` columns, reusing the quality router's flag-key
  vocabulary. It **moves** failures to `rejects/`.
- **Forbid in-place pixel ops in a recipe for this phase.** `utils.record_in_place` marks scores
  stale, and a gate reading those scores would judge deleted pixels. If they are ever allowed, the
  gate must treat a stale score as not-kept.

## 6. Dry run and the recipe library

**The dry run calls the same planning function the real run calls** — the expansion service and
the stage list — so its estimate cannot drift. Preflight everything reachable while it is cheap:
ComfyUI ping, provider and model, the plan's prompt pin, export directory writability, disk
headroom for N images.

Recipes are **global — no dataset or plan foreign key** — for the same reason
`comfy_library_prompts` is: a recipe describes a process, not a dataset. JSON export and import.

**A recipe must never persist a provider API key, an absolute path from another machine, or a
plan id.** Validate that on load as well as on save; a recipe is a document that can arrive from
elsewhere.

## Documentation this arc owes

`docs/dev/comfyui.md` went over budget at ~4,440 words after §1 and was split on 2026-11-03:
the run body is now `docs/dev/comfy-run.md` and the page `docs/dev/comfy-page.md`. Later stages
that edit the run body document it in comfy-run.md. Two new topic
files are owed beyond that, each with a Map row:

- comfy-structured.md — blueprints, axes, variations and templates, expansion and its guard
  rails, Top up, and §3's rewrite pass. §1's two columns stay documented beside the row model;
  cross-reference them rather than moving them.
- comfy-automation.md — the `Run` row, `advance_run()`, stages, cancel, pause and resume, the VRAM
  handoff, the goal loop, budgets and recipes.

`docs/dev/backend-infrastructure.md` (~2,000 words) gains a paragraph on the post-terminal
`advance_run()` hook. User docs are owed in the same change as each feature — `docs/comfyui.md`
or a new page, plus a row in `docs/features.md` and whatever `README.md` needs. Run
`python scripts/check_docs.py` and `python scripts/check_migrations.py` on every stage.

## Still open

- Whether leaf-level LLM concurrency in §3 is worth exposing. It buys nothing against a
  single-GPU local server and real time against a hosted API.
- Whether an entity filter (built on `comfy_leaves`, scoped to a blueprint) is wanted in the
  gallery. Not built until someone misses it (decision 8).

Settled 2026-11-03 by the user: per-leaf targets are edited per scenario in the UI and stored
per leaf in the DB; a deleted leaf's rows count toward nothing (see §2c).

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

Every one of these fails quietly. None produces an exception or a visibly wrong number.
