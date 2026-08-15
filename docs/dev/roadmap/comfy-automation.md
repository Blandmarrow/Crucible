# Roadmap — ComfyUI structured generation and automated dataset runs

Scoped in a design session on 2026-08-15. Two asks that turned out to share
a spine: generating a *foldered, categorized* prompt set from one sentence, and running a whole
dataset end to end without babysitting it. The first is independently shippable and useful; the
second depends on it and is where all the architectural risk lives. §1 has shipped; everything
else here is still a sketch.

**Lifecycle**: this file is transient, like the video arc's `roadmap.md` (retired in `dd53b13`)
and the detection/SAM 3 roadmap (retired in `f31a9dc`). When a stage lands, move its durable
rationale into the `docs/dev/` topic file named in § Documentation this arc owes and delete the
stage from here; delete the file when everything is done. It sits in a subfolder deliberately —
`scripts/check_docs.py` globs `docs/dev/*.md` non-recursively, so like `docs/dev/postmortems/`,
files here carry no Documentation Map row and no word budget. They are also not path- or
link-checked, so inline-code paths here need verifying by hand.

Nothing here is decided by the code yet. Read `CLAUDE.md`, `docs/dev/comfyui.md` and
`docs/dev/comfy-prompts.md` before implementing, and prefer the repo's conventions over the
sketches below.

## What this arc is

A plan holds a flat list of prompt rows. §1 has shipped, so each row now carries its own
destination folder and a run files its outputs into a tree — but the rows themselves are still
written or generated one flat batch at a time, so the *structure* is whatever you typed.

After §2 you describe a dataset once — *"a medieval set, N characters, each in M biomes
alone and together, K images per scenario"* — review the taxonomy the model proposes, edit it, and
get a foldered dataset whose structure you chose. After §4 and §5 that same description drives a
run that generates, scores, dedupes, gates and captions on its own, and keeps going until each
folder holds K images that pass your bar.

The load-bearing idea in the first half is that **the LLM invents entities and code does the
combinatorics**. Asking a model to enumerate 55 leaves of a cross product gets you 50 of them,
with the character described differently in each. Asking it for 10 characters and 5 biomes, then
multiplying in Python, gets you all 55 and the same character every time.

## Sequencing

**Section numbers are stable identities, not an order.** Everything below refers to a piece of
work as §N and those numbers never move; the build order is this table alone.

**§1 (per-row subfolder + `leaf_id`) shipped**, ahead of everything below; its durable rationale
now lives in `docs/dev/comfyui.md` and its section here is deleted per this file's lifecycle rule.
The `comfy_leaves` table it was scoped to leave for later is still §2's to build — `leaf_id` is a
column with no writer.

| Order | Stage | Depends on | Notes |
|---|---|---|---|
| 1 | Structured generation (§2) | §1 (shipped) | Outline → review → expand → generate. |
| 2 | LLM call reliability (§3) | — | Folded into §2's job where practical; valuable on its own. |
| 3 | Mock ComfyUI server (§7) | — | Pulled forward on purpose — see below. |
| 4 | Orchestrator queue, linear chain (§4) | §2, §7 | The risky one. |
| 5 | Goal loop, gates, budgets (§5) | §4 | |
| 6 | Dry run + recipe library (§6) | §4 | |

**§7 leads the second half rather than closing the arc.** `docs/dev/comfyui.md` § Gotchas records
that no mock ComfyUI server is checked in, so the entire `comfy_generate` run body — import,
provenance stamping, per-row rollback, the three-connect-errors abort — has no end-to-end
coverage at all. §1 edited that loop with only request-level coverage to catch it, and §4 builds
its whole architecture on top of it. Writing the
mock first converts the arc's least-tested surface into its best-tested one, and it is the only
route to a GPU-free, ComfyUI-free Playwright run of the whole feature.

**If the arc has to be cut short, cut after §3.** Foldered datasets plus reliable prompt
generation is most of the value, and a manual "run these stages in order" habit covers the rest.

## Decisions

Recorded so an older draft cannot reintroduce them.

- **Axes, not leaves.** The outline call returns entities and combination rules; expansion is
  deterministic code. See §2.
- **"Individually and together" is `take: N` on an axis**, expanded with `itertools.combinations`,
  not a separate scenario mode. One mechanism, not two.
- **Per-leaf targets are absolute**, never "N more" — the property the existing
  `comfy_prompts` job already has, applied per leaf. It is what makes a re-run top up only what is
  missing, and it is the foundation of both resume (§4) and the goal loop (§5).
- **Batches of ~5 per leaf, not one call per leaf.** The second call sees the first's output as
  diverge-from context, so intra-leaf variety is better. The extra requests are the price.
- **A second `JobQueue` instance for the orchestrator** (§4), with the cancel set shared at module
  level.
- **The orchestrator gets its own dataset-scoped routed page**, not a third `ComfyPage` tab. The
  run spans captioning, scoring, gating and export; only its first two stages are about ComfyUI.
  The counter-argument — that a run is intrinsically dataset+plan scoped, and a tab would dodge the
  routed-page checklist in `docs/dev/panes-routing.md` — was heard and rejected. Do not silently
  flip this back; it costs the six-site checklist plus `PaneHeader`'s `PAGE_OPTIONS`.
- **The gate moves failures to a `rejects/` subfolder. It does not delete.** Reversible,
  inspectable, and it composes with the per-row folder structure §1 shipped.

### Rejected

- **A `pair:K` scenario mode.** Subsumed by `take: N`. Two mechanisms for one concept is how the
  expander grows a bug.
- **A new job type for structured prompt generation.** Reuse `comfy_prompts` — see §2.
- **Fanning `request_cancel` across a queue registry.** A shared module-level set is strictly
  better — see §4.
- **Inlining every stage into one giant job.** Would require lifting each stage's `_run` closure
  out of its router into a service, including the comfy run body. High regression risk on
  untested code, and it throws away every existing preflight check.
- **A bare `asyncio.create_task` orchestrator outside the job queue.** Works, but then nothing sets
  its status, emits its SSE or handles its cancel — hand-rolling what the worker already does, and
  producing the one job type `TopBar` and `mark_interrupted_jobs` do not understand.
- **Making the main queue multi-worker.** Cheapest diff, worst idea: the single worker is what
  serializes GPU work. Two workers lets captioning and a ComfyUI run hit the same card.
- **A snapshot stage on by default.** It is gated on versioning being enabled (most installs have
  it off), and snapshot jobs are deliberately non-cancellable — so as a default it puts an
  uncancellable segment at the head of every run for a feature most users do not have on. Opt-in,
  and it *skips with a recorded note* rather than failing when versioning is off.
- **Dedupe-with-delete on by default.** `resolve_duplicates` deletes rows and unlinks files. An
  unattended loop does not get that by default; `flag` is the default and `resolve` is a choice.

## 2. Structured generation — axes, not leaves

Read `docs/dev/comfy-prompts.md`.

### Phase A — the outline

One LLM call turns a brief into a blueprint:

```jsonc
{"axes": {"character": [{"id": "knight", "label": "Iron Knight",
                         "descriptor": "a tall knight in blackened plate, red sash, scarred jaw"}],
          "biome":     [{"id": "fen", "label": "Salt Fen",
                         "descriptor": "a brackish fen, reed banks, low fog"}]},
 "scenarios": [{"name": "solo", "combine": ["character", "biome"], "mode": "full",
                "count": 10, "path": "{biome}/{character}", "vary": ["time of day", "camera angle"]},
               {"name": "pairs", "combine": [{"axis": "character", "take": 2}, "biome"],
                "mode": "sample:20", "count": 10, "path": "{biome}/pairs/{character}"}]}
```

- A `combine` item is a bare axis name (take 1) or `{axis, take: N}`. `take > 1` is
  `itertools.combinations` — unordered, distinct. That is "individually and together" with one
  mechanism.
- `mode` is `full` or `sample:K`, seeded off the blueprint so re-expansion is stable.
- `path` renders from slugs of the combined entities' labels.
- **`descriptor` is the point.** It is injected verbatim into every prompt naming that entity,
  which is what keeps one character recognisable across every leaf they appear in. Without it a
  character-and-biome dataset is a pile of unrelated medieval people.

The blueprint is persisted as a draft and **returned to the UI for review and edit before any
bulk generation is paid for**. Phase A is one call; the taxonomy is exactly the thing worth
correcting by hand before spending hundreds of calls on it.

**Descriptors go in the user message**, prefixed with an instruction to use them verbatim — never
in `system_instructions`, which the user owns and persists per plan in localStorage.

### Expansion

Expansion belongs in a **pure service**: no DB, no HTTP, no `HTTPException`. It returns leaves,
a total, and warnings; the router decides what is a 400. That makes the combinatorics directly
unit-testable and lets the same function back both the preview and the real expansion — which is
what makes the preview trustworthy.

Guard rails, all of which exist because the cross product is multiplicative and a four-axis
blueprint written in one sentence can mean 4,800 rows:

- The preview shows total rows, leaf count and estimated LLM calls **before** approval.
- A hard row cap with an explicit override, refused rather than truncated silently.
- A unique index on (blueprint, path) so a path collision is an error, not a silent merge.
- Expand takes a `confirm_total` and **409s on mismatch**, so "reviewed 300, enqueued 30,000"
  cannot happen after an edit lands between review and approval.

Empty rendered path segments and unknown axis names are validation errors at review time.
`slugify_filename` returns `"image"` for an all-punctuation label — as a *path segment* that is a
silently wrong folder, so treat it as a validation error rather than a fallback.

### Phase B — generation

Per leaf, the existing batch loop, writing rows with `leaf_id` and `subfolder`. Per-leaf
`target_count` is absolute, so re-running tops up only short leaves.

**Reuse `job_type="comfy_prompts"`; do not mint a new type for this.** That reuse is load-bearing:
the existing 409 guard matches on job type *and* `config["plan_id"]`, and it is exactly the mutual
exclusion needed here — flat and structured generation both seed a dedupe set once at start and
both insert rows, so they must never run concurrently on one plan. A second type silently permits
that, and costs a duplicate `TopBar` branch and a duplicate modal re-attach. Carry
`blueprint_id` / `leaf_index` / `leaf_total` on the progress events instead. Phase A's single call
is short and creates no rows, so it is a separate job type of its own.

### Frontend

`ComfyRowsTable`'s grid template is built once and reused by the header and every body row, so a
folder column is one insertion point plus header and body cells; grouping by leaf is the larger
change, since the virtualizer counts rows flat. The review surface is a new panel — axes as
editable entity lists, scenarios as editable rows, and a debounced live expansion preview.

**The review panel must be reachable without ever having run an outline job.** There is no LLM
provider in CI, so a Playwright spec has to reach it by hand-authoring a blueprint. That is also
better product behaviour — a blank blueprint is a legitimate starting point.

## 3. LLM call reliability

Read `docs/dev/comfy-prompts.md`, `backend/ml/prompt_generator.py`.

The current loop was tuned for one flat run of up to 200 prompts. Applied per leaf, dozens of
times, it behaves badly. Measured facts first, because they are not obvious from the code:

- **One "call" is up to two HTTP requests.** The `json_schema` attempt falls back to a plain call
  whenever it errors *or* parses to zero prompts. On LM Studio with a thinking model the schema
  attempt returns empty content, so the fallback is the normal path and every leaf pays double.
- **The per-leaf cap is a blank cheque.** `max(12, ceil(total/batch)*3)` with a leaf of 10 is 12
  calls — sensible for a 200-prompt run, absurd 55 times over.
- **There are no retries anywhere**, and a provider error with any prompts already created aborts
  the whole run.
- **The timeout and token floor are constants** (120 s, 8192), tuned before descriptors made long
  prompts the normal case.

Changes:

- **Probe the schema path once per run**, then stick with whatever worked. On a 60-leaf run this
  alone removes 60 wasted requests.
- **Per-leaf cap `ceil(target/batch) + 2`**, plus a run-level call budget shown at review as part
  of the expansion preview and enforced as a hard stop.
- **Bounded retry with jittered backoff** on transient failures — timeout, connection reset, 5xx,
  429. Never retry a schema rejection; that is the probe's job. **Poll the cancel flag inside the
  backoff and before each attempt**, or Stop takes three timeouts plus sleeps to be noticed.
- **Scale `max_tokens` and the timeout** with batch size and injected descriptor length.
- **Cap the anti-similarity context by characters, not count.** Forty prompts carrying descriptors
  can be 20k characters and crowd out the instruction itself.
- **`no_new` needs two consecutive barren calls.** One is too eager when a leaf is ten prompts.
- **Per-leaf failure isolation** with a run-level threshold (abort past roughly a quarter of
  leaves failed), keeping the existing outcome discipline at run level: nothing created fails the
  job, a shortfall completes loudly.

**Do not widen `ParsedPrompts` to carry probe state.**
`docs/dev/postmortems/PM-005-tuple-return-widened-broke-caller.md` is literally that mistake. Probe
state belongs on a caller object that owns the connection for the run; keep the existing
module-level function as a thin wrapper so the one-shot endpoint is untouched.

**The honest guarantee** — worth stating in the UI, not just the code: *no single call failure
loses the run, and re-running tops up only what is missing.* That is achievable. "All 110 calls
succeed" is not.

## 4. The orchestrator queue

Read `docs/dev/backend-infrastructure.md`, `docs/dev/frontend-jobs.md`, `docs/dev/panes-routing.md`.

`backend/workers/job_queue.py`'s worker is a single sequential loop: it takes one job, awaits its
function to completion, and only then returns to the queue. An orchestrator job on that queue that
enqueues a child and waits for it can never see the child start — the worker is still inside the
parent. Not a slowdown; a permanent wedge for every other job too. The requirement this produces
is narrow: **the coroutine that waits must not be the one that dispatches what it waits for.**

A second `JobQueue` instance satisfies it with one independent worker loop, and keeps every
existing job-lifecycle guarantee — status transitions, SSE, cancel, history, restart handling.
`JobQueue` is already generic; it is an instantiation plus a start/stop in
`backend/main.py`'s lifespan. See § Rejected for the three alternatives and why each loses.

### Cancellation

**Make the cancel set module-level, shared by every instance**, rather than teaching cancel to fan
out across a registry of queues. `backend/routers/jobs.py` then needs *zero* changes, and — the
part a registry gets wrong — every `cancel_requested` call site in `backend/ml/` keeps working
unmodified for a job on either queue. A registry fixes the router and leaves the scorers silently
uncancellable.

Propagation is the orchestrator's own job: on cancel, request the child's cancel, then **keep
polling until the child is terminal, and only then raise**. Raising while the child still runs
reports the orchestrator cancelled while the child still owns the ComfyUI connection and the
dataset's stats column. The reverse case matters too — `TopBar`'s Cancel-all can kill a child
directly, and the orchestrator must read that as a stage failure, not hang on it.

### Waiting on a child

Poll the `BackgroundJob` row. **Never the SSE stream** — `backend/workers/progress.py` drops
events on a full subscriber queue, so a terminal event can simply not arrive. The worker commits
status and `result_data` *before* emitting, which is precisely what makes polling the authority.
`backend/tests/conftest.py`'s `wait_for_job` is the shape to mirror, at a slower interval.

**A fresh session per poll.** `AsyncSessionLocal` sets `expire_on_commit=False`, so a long-lived
session's `get()` keeps serving the identity-mapped row it read first and the wait never ends.
This is the likeliest bug in the phase; the `conftest` one below is the likeliest to cost a day.

**`backend/tests/conftest.py` rebuilds the queue singleton's `asyncio.Queue` per test** because it
is loop-bound. The second instance needs identical treatment or every orchestrator test hangs
forever — there is no pytest timeout plugin, so that is an unbounded CI stall, not a failure.
**First task of the phase**, with a test that proves the second queue runs and cancels before
anything else exists.

### Stages

One async function per stage, behind a registry. A stage either does work directly or enqueues a
child on the main queue and waits.

| Stage | Shape |
|---|---|
| `snapshot` | direct; opt-in, skips with a note when versioning is off |
| `prompts` | child (§2's job) |
| `vram_handoff` | direct; both directions, can appear twice |
| `generate` | child (`comfy_generate`) |
| `score` | child, scoped by image ids |
| `dedupe` | child + policy; default `flag`, never `resolve` |
| `gate` | direct; a query plus moves to `rejects/` |
| `caption` | child, scoped by image ids |
| `export` | child |

**No job in this repo has ever enqueued another, and no router handler has ever been called by
non-HTTP code.** State that plainly wherever this lands. Calling the handlers in-process reuses
every preflight check they already do — the 400s, the 409s, the disk preflight — which is most of
the argument for it. The risk is a handler later gaining a `Request` or `BackgroundTasks`
parameter and breaking the orchestrator silently, so it wants a structural guard test in the
spirit of `backend/tests/test_video_lineage_mirrors.py`: call each handler used, assert the
response shape.

A handler that answers `{"job_id": None}` because nothing was in scope must become a **skipped**
stage, not a wait for a job that will never exist. Write that test first.

### The VRAM handoff

Both directions, because on a single-GPU box the run alternates between ComfyUI and local torch
models: `model_manager.evict_all()` frees Crucible's, and a new best-effort `ComfyClient` call
against ComfyUI's `/free` frees ComfyUI's before the scoring stage. Without the second direction
`score` OOMs immediately after a generate. Verify the endpoint against the target ComfyUI build
and treat any error as a no-op with a recorded note — a missing `/free` must never fail a run.

For the LLM: an Ollama provider unloads cleanly with `keep_alive: 0`; **LM Studio does not expose
unload over its OpenAI-compatible API**, so that case is a TTL the user sets in LM Studio or a
configured local command, not something this stage can do over HTTP. Do not design as if it can.

### Run context

Hold **row ids per iteration, not image ids**. The images an iteration produced are a query over
the rows' `image_ids`; storing thousands of uuids in a JSON column that `JobOut` returns verbatim
is a large response on every poll. The generate stage's `created_image_ids` is a **cross-check**,
not the source of truth — after a restart the job row reads `failed` while the rows are intact,
which is exactly the case resume exists for.

### Restart and resume

`mark_interrupted_jobs()` fails every running row at startup, so an orchestrator row can be left
`running` with no job behind it. Reconcile in the lifespan, right after it: flip such runs to
`paused` with a resume hint. Cheap, and it is the difference between a resumable run and a mystery.

Resume is explicit — never automatic — and re-enqueues from the first non-completed stage. Per-stage
idempotence is what §2's absolute per-leaf targets and the run endpoint's pending-rows-only default
already buy. Note the exceptions rather than pretending they do not exist: a re-run `snapshot`
creates a second snapshot, and `export` rewrites its output.

### Frontend

A new dataset-scoped routed page (see § Decisions), which costs the six-site checklist in
`docs/dev/panes-routing.md` plus `PaneHeader`'s `PAGE_OPTIONS` and `NEEDS_DATASET`, since it is
pickable from a pane.

**The orchestrator job type goes in neither `LIVE_IMAGE_JOB_TYPES` nor
`IMAGE_MODIFYING_JOB_TYPES`.** Its children are already in those sets and already fire every
invalidation; adding the parent doubles every refetch for the run's whole duration. It gets its own
small branch, like the `comfy_prompts` branch already there. Worth a code comment — "the
orchestrator touches images so it belongs in the image sets" is the intuitive and wrong move.

## 5. Goal loop, gates, budgets

The loop: compute each leaf's shortfall → top up rows → generate → score → dedupe → gate →
recompute → repeat.

- **Hard caps are required fields, not options**: max iterations, max images, max LLM calls, max
  wall-clock. Each iteration burns real GPU time and a stop condition can fail to converge.
- **Check the budget before each expensive stage, never after.** Checking after is how a run
  overspends by a whole generate stage.
- **Recompute "kept" from the DB every iteration; never accumulate it.** A resolve-mode dedupe
  deletes rows and `scores_stale` can invalidate a score, so an accumulated counter believes in
  images that are gone and stops early.
- **Abandon a leaf that gains nothing across two consecutive iterations**, so a leaf the model
  cannot satisfy does not spin the run to its iteration cap.
- The gate is a pure predicate over `Image` columns, reusing the quality router's allowed flag-key
  vocabulary rather than inventing a parallel one. It **moves** failures to `rejects/`.
- **Forbid in-place pixel ops in a recipe for this phase.** `utils.record_in_place` marks scores
  stale, and a gate reading those scores would judge on measurements of deleted pixels. If they
  are ever allowed, the gate must treat a stale score as not-kept.
- `pause` (at a stage boundary, child kept) is a different verb from `cancel` (child killed). A
  pause that returned without draining the in-flight child would let iteration N+1 collide with the
  per-plan 409 guard.

## 6. Dry run and the recipe library

**The dry run must call the same planning function the real run calls.** A shared code path is
the only thing that makes an estimate trustworthy; a parallel estimator drifts and then lies.
Preflight everything reachable while it is cheap: ComfyUI ping, provider and model, the plan's
prompt pin, export directory writability, disk headroom for N images.

Recipes are **global — no dataset or plan foreign key** — for the same reason
`comfy_library_prompts` is: a recipe describes a process, not a dataset. JSON export and import.
This is the "specify which parts of Crucible to use" ask made durable.

**A recipe must never persist a provider API key, an absolute path from another machine, or a plan
id.** Validate that on load as well as on save; a recipe is a document that can arrive from
elsewhere.

## 7. The mock ComfyUI server

`docs/dev/comfyui.md` § Gotchas already specifies the shape, and it should be built as specified:
`system_stats`, `prompt`, `history`, `view` returning PNG bytes with an embedded workflow chunk,
`interrupt`, `free`, plus trigger prompts that force a 400 carrying `node_errors` and a
mid-execution failure.

**Bind a real ephemeral port and point the configured ComfyUI URL at it.** Do not try to inject an
httpx transport into `ComfyClient` — it builds a fresh client per method with no seam, and adding
one is a larger change than binding a port.

Two consumers, and the second is the reason this is worth doing early:

- **pytest** finally covers the `comfy_generate` run body — import, provenance stamping,
  `source_meta`, multi-output rows, the per-row rollback handler, the connect-error abort.
- **Playwright** gets a full GPU-free, ComfyUI-free, LLM-free journey: hand-authored blueprint →
  expand → stubbed prompts → run → foldered gallery. That is the strongest regression net this arc
  can have.

## Documentation this arc owes

`docs/dev/comfyui.md` is over budget at ~3,940 words (it absorbed §1) with a seam recorded in
`docs/dev/pending-splits.md`, and
`docs/dev/backend-infrastructure.md` is already over at ~3,900 with a seam of its own there.
**Neither absorbs the rest of this arc.** Two new topic files, each with a
Documentation Map row and a hand-written `Words` cell:

- comfy-structured.md — blueprints, axes and scenarios, expansion and its guard rails, and §3's
  reliability rules.

**Deviation, recorded for §2.** §1's docs went into `docs/dev/comfyui.md` rather than opening
comfy-structured.md early. A stub for a file chartered around blueprints — none of which existed
after §1 — would have cost a Documentation Map row and a WARN target for absent content, while
`ComfyRow.subfolder`/`leaf_id` genuinely belong beside the row model they extend. So
comfy-structured.md is still §2's to create, and the two columns stay documented where they are;
cross-reference them from the new file rather than moving them. Note that `docs/dev/comfyui.md`'s
recorded seam moves § Frontend out, not the columns.
- comfy-orchestrator.md — the second queue, the shared cancel set, stages, run context, the goal
  loop, budgets, resume, recipes.

`docs/dev/backend-infrastructure.md`'s `JobQueue.stop()` and job-cancellation sections both need
rewriting once §4 lands. User-facing docs are owed in the same change as the feature —
`docs/comfyui.md` or a new page, plus a row in `docs/features.md` and whatever `README.md` needs.
Run `python scripts/check_docs.py` and `python scripts/check_migrations.py` on every phase.

## Still open

- Whether per-leaf targets are editable individually in the review UI or only per scenario.
  Scenario-level is simpler and covers the stated use case; leaf-level is what §5 needs
  internally either way. Recommend scenario-level in the UI, leaf-level in the DB.
- What happens to rows whose leaf a blueprint edit deleted. A dangling `leaf_id` is the cheap
  answer and keeps the provenance, but re-expansion then mints new leaves and those rows stop
  counting toward the new targets — so an edit-then-regenerate can double the dataset. Surface it
  in the preview rather than solving it silently.
- Whether the orchestrator's child jobs appear in `TopBar`'s pending chips or are tagged and
  grouped. Visible is the honest default; tag them with the parent run either way.
- Whether leaf-level LLM concurrency is worth exposing. It buys nothing against a single-GPU local
  server, which serializes anyway, and real wall-clock against a hosted API.
- Whether `JobQueue.stop()`'s intermittent hang should be fixed properly as part of §4 rather than
  guarded with a timeout on both instances.

## Traps worth restating

- **`declare_subfolder` commits.** Called inside the import loop it commits a half-written row
  and breaks the per-row rollback the failure handler depends on. §1 shipped with the declaration
  hoisted above the loop; the rule now lives in `docs/dev/comfyui.md` § Gotchas, and §4's stages
  are the next thing that could reintroduce it.
- **A long-lived session serving a stale row forever** (§4). `expire_on_commit=False` means the
  poll never observes the child finishing.
- **The test harness's loop-bound queue singleton** (§4). Miss the second instance and the phase's
  whole suite hangs rather than fails.
- **Cancelling the parent while the child still runs** (§4). Reports a cancel that has not happened
  while the child still holds the ComfyUI connection.
- **An accumulated "kept" counter** (§5). Dedupe deletes rows underneath it, so the loop stops
  believing in images that no longer exist.
- **Checking a budget after the stage it was meant to prevent** (§5).
- **A silently wrong path segment from a slug fallback** (§2). `slugify_filename` answers `"image"`
  for an all-punctuation label, and as a folder name that is wrong rather than absent.
- **An LLM asked to enumerate a cross product** (§2). It returns most of it, and describes the same
  entity differently each time. This is the failure the whole axes model exists to prevent.

Every one of these fails quietly. None produces an exception or a visibly wrong number.
