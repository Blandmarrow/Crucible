# ComfyUI — automated runs

An automated run (M6 §4) takes one press from a ComfyUI plan to a scored, captioned,
exported dataset: it fills the plan's blueprint, frees the GPU, generates, frees it the
other way, scores, captions and exports. This file covers the `Run` row, how it is stepped,
each stage, cancel, pause, resume and restart, and the Runs page. The ruling is
[decision 9](../decisions/09-runs-step-forward-on-job-completion.md); the stages still to
come (the gate and goal loop, the recipe library) are in
`docs/dev/comfy-automation-plan/README.md`. The user guide is `docs/runs.md`.

## The shape: a row stepped forward, never a waiting job

The job queue has one worker, and it runs one job to the end before taking the next
(`docs/dev/backend-infrastructure.md` § Job cancellation), so nothing on that queue may wait for
another job on it. A run therefore **is not a job**. It is a `Run` row
(`backend/models/run.py`, table `runs`):

- `recipe`: the `schemas/run.RunRecipe` it was started with. That means which stages are
  on, plus each one's settings.
- `stages`: one entry per stage, in `STAGE_ORDER`, each `{key, status, job_id, note,
  summary, started_at, finished_at}`. Status is `pending`, `running`, `done`, `skipped`,
  `failed` or `cancelled`. The list is copied, edited and reassigned, never mutated in
  place (CLAUDE.md § Key invariants).
- `context`: `{"row_ids": [...]}`, the plan rows this run generates. These are **row ids,
  not image ids**. The run's images are a query over those rows' `image_ids`
  (`run_service.run_image_ids`). A JSON list of thousands of uuids would ride on every
  poll of the run.
- `status` (`running`, `paused`, `completed`, `failed`, `cancelled`), `pause_requested`,
  `current_job_id` and `error_msg`. `current_job_id` has no FK, because finished job rows
  are swept after 30 days.

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

1. **The stale guard.** It acts only if the run is `running` and the job's `run_stage` tag
   (`"<index>:<key>"`, from `stage_tag`) names the stage that is `running` now. A job the
   run has moved past, such as a child cancelled with the run that finishes late, changes
   nothing.
2. It records the stage's summary from the job's `result_data` (`_summary`). The outcome
   follows from the job's status:
   - `completed` → `done`;
   - `failed` → the stage and the run are `failed`, with the job's error;
   - `cancelled` while the run was running (a `TopBar` cancel) → the stage is `cancelled`,
     and the run is `failed` "cancelled outside the run".

   A `comfy_generate` with some rows failed still **completes**. The failed rows are in the
   summary, and the next run's Top up resets and retries them.
3. If a pause was asked for, the run becomes `paused`. Otherwise it calls `_start_next`.

`_start_next` walks the stages from the first `pending` one. Each stage's start returns
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
is not re-entrant, so they call `_start_run(..., hold_lock=False)` inside it.

## The stages

| Stage | What it does |
|---|---|
| `prompts` | Top up's fill on the **stored** blueprint, through `routers/comfy.fill_blueprint` under the plan lock (Apply, then `prepare_top_up`). `confirm_total=None`, since the lock holds from Apply's count to its write. Stores the pending row ids in `context`. With no blueprint it is skipped. Over the row cap it fails with Apply's message, because a run never passes `allow_over_cap`. |
| `handoff_to_comfy` | A `run_handoff` job: `model_manager.evict_all()`. The note counts the models that were resident (`evict_all` returns every registered id) and names any still leased. `evict_all` ends with a torch cache flush that raises on a torch-free install; that is caught, because nothing torch-backed was loaded there. |
| `generate` | The run's rows that are not completed yet: `failed` and `running` ones go back to pending, under the plan lock. Then `_start_run` with those exact row ids. With no `context.row_ids` (no blueprint, or `prompts` off) it takes the plan's pending rows when it first starts, and stores them. Completed rows are never run twice. |
| `handoff_to_local` | A `run_handoff` job: `ComfyClient.free()` (`POST /free`), best effort. Any error is a note. It is built through `backend.routers.comfy.ComfyClient`, the name the tests patch onto the fake. |
| `score` | `start_score_job` over the run's images, technical scoring always on (it flags duplicates through `_flag_duplicates`, dataset-wide) and aesthetic optional. |
| `caption` | `start_caption_job` over the run's images, with `overwrite` on by default. `comfy_generate` with `set_caption` has already written each prompt as the caption, so `overwrite=False` would find nothing. |
| `export` | `start_export_job` with `image_ids=None`: the whole dataset through the form's filters, duplicates left out by default (`exclude_flags="is_duplicate"`). |

**A stage with nothing in scope is skipped, never started with `[]`.** Score, caption and
export read `image_ids: []` as the whole dataset (`if body.image_ids:`; the HTTP routes keep
that). So the image stages skip when the run produced no images, and treat a start
function's `job_id: None` as a skip too. Export takes no id list at all.
`test_a_stage_with_no_images_is_skipped_and_never_started_with_an_empty_list` holds it,
with a bystander image in the dataset that a `[]` would sweep in.

**Rows waiting for a §3 rewrite pause the run** at `prompts`, with "N rows wait for a
rewrite. Run it on the ComfyUI page, then resume." Top up refuses them for the same reason
(`docs/dev/comfyui/structured.md` § Top up). An unattended run cannot review which scenarios
to rewrite, so it does not start the rewrite job itself.

The VRAM handoff does nothing for an LLM. LM Studio has no unload over its OpenAI-compatible
API, and §4's stages make no LLM call of their own. A caption model on an LLM provider is
the user's to give a TTL.

## Starting, and the checks it runs first

`POST /runs/` (`run_service.start_run`) refuses before writing anything:
- 404 for an unknown plan, and 400 for a plan of another dataset;
- 409 when the dataset already has a live run (`running` or `paused`; one per dataset);
- `_preflight`: the ComfyUI URL and workflow when a ComfyUI stage is on, the base folder,
  the prompt pin when the blueprint will be filled, the caption model (by building the
  `CaptionJobRequest`, so a bad id is a 400 now and not at stage six), and
  `export.check_export_request`, which gives a 400 for a relative folder or a 507 for a
  full disk.

The recipe's own validator 422s a caption or export stage with no settings, and puts the
stages into `STAGE_ORDER` whatever order they arrive in.

## Cancel, pause, resume, restart

- **Cancel** (`POST /runs/{id}/cancel`, running or paused) marks the running stage and the
  run `cancelled`, then cancels the child the way `DELETE /jobs/{id}` does: a pending child
  is written `cancelled`, and a running one gets `request_cancel`. When the child then ends,
  `advance_run` sees a run that is not `running` and stops.
- **Pause** sets `pause_requested`. The running child finishes, and `advance_run` stops at
  the boundary. Resume before the boundary just clears the flag.
- **Resume** (paused or failed) resets `running`, `failed` and `cancelled` stages to
  `pending` and calls `_start_next`. It is always explicit. Re-running `prompts` is safe,
  because per-leaf targets are absolute. Re-running `generate` runs only rows not
  completed. A re-run `export` rewrites its output.
- **Restart.** `main.py`'s lifespan calls `reconcile_interrupted_runs()` right after
  `mark_interrupted_jobs()`. Every `running` run becomes `paused`, and its running stage
  goes back to `pending` with a note. This also covers a child that ended while the queue
  was stopping, whose hook was skipped.
- **Delete** (`DELETE /runs/{id}`) refuses a live run with a 409.

## The Runs page

`frontend/src/pages/RunsPage.tsx`, routed at `/datasets/:datasetId/runs` (page type
`runs`, pickable from a pane, sidebar entry "Runs"), with `frontend/src/api/runs.ts`. It
is **not keyed** by dataset id in `PageRenderer`. The form re-seeds through a
`prevDatasetId` effect, as `ComfyPage` does.

- With no live run it shows the start form: the plan, the stage switches, the base folder,
  the aesthetic model, a caption model from `captionModelOptions` (a provider takes a
  model name), and the export kind, folder and fields. Start is disabled with the first
  reason (`blockers`) until the form is complete.
- With a live run it shows the stage list with each status, summary and note, the child's
  progress through `useJobSSE(current_job_id)`, and Pause, Resume and Cancel.
- The run list refetches every 1.5 s while a run is `running`, and again on the child's
  terminal event, which also invalidates the dataset's images, subfolders and the plan's
  rows. The children keep their own job types, so `TopBar`'s `LIVE_IMAGE_JOB_TYPES` and
  `IMAGE_MODIFYING_JOB_TYPES` fire their usual invalidations; `run_handoff` needs no entry.
- Past runs are listed below, with Resume for a failed one, and Delete.

The form is not saved anywhere. §6's recipe library is where a run's settings will be kept.

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
- the restart reconcile, the rewrite pause, the start-time checks, and the stale guard.

Its `_block_worker` holds the single worker on a gate job, which keeps a child queued
deterministically. `backend/tests/test_caption_start_scope.py` pins what
`start_caption_job` puts in scope, including the `[]` widening. `frontend/e2e/runs.spec.ts`
runs a blueprint through prompts, both handoffs, generate and a plain export from the page.
