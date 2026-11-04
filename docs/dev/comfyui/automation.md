# ComfyUI — automated runs

An automated run (M6 §4) takes one press from a ComfyUI plan to a scored, captioned,
exported dataset: it fills the plan's blueprint, frees the GPU, generates, frees it the
other way, scores, captions and exports. This file covers the `Run` row, how it is stepped,
cancel, pause, resume and restart. The ruling is
[decision 9](../decisions/09-runs-step-forward-on-job-completion.md). Each stage is
`docs/dev/comfyui/run-stages.md`; the start check, the dry run and the Runs page are
`docs/dev/comfyui/run-start.md`. The goal loop that repeats a run's body is
`docs/dev/comfyui/run-loop.md`, with the gate in `docs/dev/comfyui/run-gate.md` and the
budgets and carrying on past a cap in `docs/dev/comfyui/run-budgets.md` (M6 §5, §6); the
recipe library and the remembered start form (M6 §6) are `docs/dev/comfyui/recipes.md`. The
user guide is `docs/runs.md`, with `docs/run-setup.md` and `docs/repeating-runs.md`.

## The shape: a row stepped forward, never a waiting job

The job queue has one worker, and it runs one job to the end before taking the next
(`docs/dev/backend-infrastructure.md` § Job cancellation), so nothing on that queue may wait for
another job on it. A run therefore **is not a job**. It is a `Run` row
(`backend/models/run.py`, table `runs`):

- `recipe`: the `schemas/run.RunRecipe` it was started with. That means which stages are
  on, plus each one's settings.
- `stages`: one entry per stage, in `STAGE_ORDER`, each `{key, status, job_id, note,
  summary, started_at, finished_at, pass_no}`. Status is `pending`, `running`, `done`, `skipped`,
  `failed` or `cancelled`. The list is copied, edited and reassigned, never mutated in
  place (CLAUDE.md § Key invariants).
- `context`: `{"row_ids": [...]}`, the plan rows this run generates. These are **row ids,
  not image ids**. The run's images are a query over those rows' `image_ids`
  (`run_service.run_image_ids`). A JSON list of thousands of uuids would ride on every
  poll of the run. Since §5 it also holds the rows of each pass (`passes`), the loop's
  per-folder counts (`leaves`) and its ending (`loop`): `docs/dev/comfyui/run-loop.md`.
- `status` (`running`, `paused`, `completed`, `failed`, `cancelled`), `pause_requested`,
  `cancel_requested` (§ Cancel, pause, resume, restart), `current_job_id` and `error_msg`.
  `current_job_id` has no FK, because finished job rows are swept after 30 days.

Each stage that does work queues an **ordinary child job** through a start function, and
the child keeps its own job type: `comfy_generate`, `quality_score`, `caption`, `export`,
or `run_handoff` for the VRAM handoff. `job_queue.run_job_config` adds `run_id` and
`run_stage` to its config, and `run_job_label` puts `Run "<name>" · <stage> — ` before its
label. That prefix is how `TopBar` tags a child without any per-type code. With no run,
both helpers return the config and label untouched, so a job started over HTTP stores
exactly what it always did.

## Stepping: `_after_terminal` and `advance_run`

`JobQueue._finish` commits a job's terminal status, emits its event, then calls
`_after_terminal(job)`. When the job's config carries a `run_id`, the hook calls
`run_service.advance_run(run_id, job_id)` in its own session. It logs and swallows any
exception, because the job's outcome is already committed and nothing after the commit may
change it (PM-013). The hook is also called on the **cancelled-while-queued** path in
`_run_item`, which never reaches `_finish`. Without that call, a child cancelled from
`TopBar` before it started would leave its run `running` with nothing in flight. The hook
is skipped while the queue is stopping; see § Cancel, pause, resume, restart.

The hook runs **after** the emit, so a client can see a child finish before the run has
moved. Tests wait on the run row (`wait_for_run` in `test_run_stepping.py`), not on the
job, and the Runs page polls.

`advance_run`, under the run's lock:

1. **The stale guard.** It acts only if the run is `running` and a stage is `running`
   whose tag (`"<index>:<key>"`, from `stage_tag`) matches the job's `run_stage` **and**
   whose recorded `job_id` is this job. A job the run has moved past, such as a child
   cancelled with the run that finishes late, changes nothing. The tag alone is not
   enough: a stage resumed under the same tag has a new job, and the old one finishing
   late must not step it.
2. It records the stage's summary from the job's `result_data` (`_summary`). The outcome
   follows from the job's status:
   - `completed` → `done`, unless the job did nothing (below);
   - `failed` → the stage and the run are `failed`, with the job's error;
   - `cancelled` while the run was running (a `TopBar` cancel) → the stage is `cancelled`,
     and the run is `failed` "cancelled outside the run".

   A `comfy_generate` with some rows failed still **completes**. The failed rows are in the
   summary, and the next run's Top up resets and retries them.

   **A job that failed every item fails the stage and the run** (the user's ruling,
   2026-11-04), through `_every_item_failed`. A `comfy_generate` that completed with
   `completed == 0` and `failed > 0` ends with "Generate: every row failed (N of N):
   <the first failed row's `error_msg`>". A `caption` job with `failed_count >=
   total_items` ends with "every image failed (N of N)". Partial failures still complete.
   A stage that made nothing would otherwise read green and hand an empty set to the next
   one. **Resume** retries it: `_stage_generate` resets failed rows to pending, and a
   caption stage runs again.
3. If a pause was asked for, the run becomes `paused`. Otherwise it calls `_start_next`.

`_start_next` walks the stages from the first `pending` one (what each stage does is
`docs/dev/comfyui/run-stages.md`). Each stage's start returns
one of four outcomes:
- a queued job → record it and return;
- `done` inline → record the summary and go on;
- `skip` → record the note and go on;
- `pause` → leave the stage `pending` with a note, and pause the run.

A start that raises (`HTTPException` from a start function's checks, or anything else)
rolls back, **refreshes the run**, and fails the stage and the run with the reason. The
refresh matters: a rollback expires every loaded attribute, and the next read would
lazy-load on the async session. With no stage left pending, the run is `completed`.

**One `asyncio.Lock` per run** (`_run_lock`) covers start, resume, cancel, pause and
`advance_run`. A start or resume holds it while it queues the child and commits the run row,
so a child the worker finishes at once cannot step a row the API has not written yet.
Stages that touch the plan's rows also take ComfyUI's plan lock (`_plan_lock`). That lock
is not re-entrant, so they call `_start_run(..., hold_lock=False)` inside it. A third,
`_dataset_lock`, holds "one live run per dataset" from its check to the commit that makes a
run live, in start, resume and extend. Without it, two presses in two panes both passed the
check. The order is always run lock, then dataset or plan lock, never the reverse.

## Cancel, pause, resume, restart

- **Cancel** (`POST /runs/{id}/cancel`, running or paused) marks the running stage and the
  run `cancelled`, then cancels the child the way `DELETE /jobs/{id}` does: a pending child
  is written `cancelled`, and a running one gets `request_cancel`. When the child then ends,
  `advance_run` sees a run that is not `running` and stops.
  **A running snapshot is the exception** (M6 §6). Snapshot jobs never poll the cancel flag
  (`create_snapshot` commits once, at its end), so the run would read cancelled while the
  job still wrote. `cancel_run` sets `Run.cancel_requested` instead and leaves the run
  `running`; `advance_run` records what the snapshot did and then ends the run
  `cancelled`. Pause and resume 409 meanwhile, and the restart reconcile turns such a
  run `cancelled` rather than paused. A snapshot still queued is cancelled like any child.
  **A second Cancel finishes a stuck wait.** If the snapshot job is gone or already
  finished (its hook never came, since it raised or was skipped), `cancel_run` ends the
  run `cancelled` at once instead of returning early. While the snapshot is pending or
  running it still returns the run unchanged.
- **Pause** sets `pause_requested`. The running child finishes, and `advance_run` stops at
  the boundary. Resume before the boundary just clears the flag. A pause asked for during
  the last stage has no boundary left, so the run completes, and a run that fails drops
  the request.
- **Resume** (paused or failed) resets `running`, `failed` and `cancelled` stages to
  `pending`, clearing their note and summary, and calls `_start_next`. `_start_next`
  clears them too whenever a stage starts, so a stage that failed and then succeeds never
  still shows the old error. It is always explicit. Re-running `prompts` is safe,
  because per-leaf targets are absolute. Re-running `generate` runs only rows not
  completed. A re-run `export` rewrites its output.
- **Restart.** `main.py`'s lifespan calls `reconcile_interrupted_runs()` right after
  `mark_interrupted_jobs()`. Every `running` run becomes `paused`, and its running stage
  goes back to `pending` with a note. **The exception is a stage whose child completed.**
  A child that ends inside `stop()`'s grace period commits `completed`, but its hook is
  skipped while the queue is stopping, so the run never heard. Resetting that stage would
  make a resume export again, or caption again on a paid provider. So it is recorded `done`
  with its summary, and a run with no stage left is `completed` rather than paused. A
  completed child that failed every item (`_every_item_failed`) is recorded `failed`
  instead, and the run ends `failed`, as `advance_run` would have ended it.
- **Delete** (`DELETE /runs/{id}`) refuses a live run with a 409.

## Testing

`backend/tests/test_run_stepping.py` drives runs against `FakeComfy` through `api_env`,
torch-free (technical scoring, aesthetic off, captioning through a stubbed openai-compat
provider). It covers:
- the skip on empty, and a full blueprint run through every stage, with three duplicates
  left out of the export because the fake returns one picture per row;
- best-effort handoffs, and `advance_run` raising (the child job is unchanged and the
  worker goes on);
- cancel while queued, a cancel from outside the run (queued and running), and pause then
  resume;
- the restart reconcile, including a stage whose child completed during the shutdown;
  a resumed stage dropping its old note; a pause during the last stage;
- the rewrite pause, the start-time checks, and the stale guard (including a stale job
  that matches the tag but not the stage's `job_id`);
- a generate job that failed every row, or a caption job that failed every image, failing
  the run, with Resume retrying the rows; a gate cancelled before its commit committing
  nothing; the gate running after its plan was deleted; and a second Cancel ending a run
  whose snapshot is gone.

Its `_block_worker` holds the single worker on a gate job, which keeps a child queued
deterministically. `backend/tests/test_caption_start_scope.py` pins what
`start_caption_job` puts in scope, including the `[]` widening. `frontend/e2e/runs.spec.ts`
runs a blueprint through prompts, both handoffs, generate and a plain export from the page.

M6 §6 added one test file here; the dry run's tests are in
`docs/dev/comfyui/run-start.md` § Testing:
- `backend/tests/test_run_snapshot.py`: the snapshot skipped with versioning off, taken
  once in a two-pass loop, a cancel that waits for it, a queued one cancelled at once, the
  reconcile of a waiting cancel, and no second snapshot on resume. `_hold_snapshots`
  patches `version_service.create_snapshot` to wait on a gate once running.

**Every test's LLM-server calls are refused** by the autouse `_no_real_llm_server`
fixture in `backend/tests/conftest.py`, which points `run_service._LLM_TRANSPORT` at a
transport that raises `ConnectError`. The tests' providers use `127.0.0.1:1234`, LM
Studio's real port, so before it a run test could list, or unload, a developer's own
models. A test that wants a server sets its own transport.
