# ComfyUI — automated runs

An automated run (M6 §4) takes one press from a ComfyUI plan to a scored, captioned,
exported dataset: it fills the plan's blueprint, frees the GPU, generates, frees it the
other way, scores, captions and exports. This file covers the `Run` row, how it is stepped,
each stage, cancel, pause, resume and restart, and the Runs page. The ruling is
[decision 9](../decisions/09-runs-step-forward-on-job-completion.md). The goal loop that
repeats a run's body, the gate and the budgets (M6 §5) are `docs/dev/comfyui/run-loop.md`;
the stage still to come (the recipe library) is in
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
  poll of the run. Since §5 it also holds the rows of each pass (`passes`), the loop's
  per-folder counts (`leaves`) and its ending (`loop`): `docs/dev/comfyui/run-loop.md`.
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
is not re-entrant, so they call `_start_run(..., hold_lock=False)` inside it. A third,
`_dataset_lock`, holds "one live run per dataset" from its check to the commit that makes a
run live, in both start and resume. Without it, two presses in two panes both passed the
check. The order is always run lock, then dataset or plan lock, never the reverse.

## The stages

| Stage | What it does |
|---|---|
| `prompts` | Top up's fill on the **stored** blueprint, through `routers/comfy.fill_blueprint` under the plan lock (Apply, then `prepare_top_up`). `confirm_total=None`, since the lock holds from Apply's count to its write. Stores the pending row ids in `context`. With no blueprint it is skipped. Over the row cap it fails with Apply's message, because a run never passes `allow_over_cap`. |
| `handoff_to_comfy` | A `run_handoff` job: `model_manager.evict_all()`. The note counts the models that were resident (`evict_all` returns every registered id) and names any still leased. `evict_all` ends with a torch cache flush that raises on a torch-free install; that is caught, because nothing torch-backed was loaded there. |
| `generate` | The run's rows that are not completed yet: `failed` and `running` ones go back to pending, under the plan lock. Then `_start_run` with those exact row ids. With no `context.row_ids` (no blueprint, or `prompts` off) it takes the plan's pending rows when it first starts, and stores them. Completed rows are never run twice. |
| `handoff_to_local` | A `run_handoff` job: `ComfyClient.free()` (`POST /free`), best effort. Any error is a note. It is built through `backend.routers.comfy.ComfyClient`, the name the tests patch onto the fake. |
| `score` | `start_score_job` over the run's images, technical scoring always on (it flags duplicates through `_flag_duplicates`, dataset-wide) and aesthetic optional. |
| `caption` | `start_caption_job` over the run's images. `CaptionSettings` is named field for field after `CaptionJobRequest` and passed through whole (`_caption_body`), so the request's own validators judge it, and a field the request gains later only needs adding to the settings. `overwrite` is on by default: `comfy_generate` with `set_caption` has already written each prompt as the caption, so `False` would find nothing. |
| `free_caption_model` | A `run_handoff` job (direction `after_caption`): `_free_caption_model` unloads whatever captioned, best effort. Skipped with a note when the recipe has no caption stage, and the page only sends it when Caption is on. See below. |
| `export` | `start_export_job` with `image_ids=None`: the whole dataset through the form's filters. `ExportSettings` is named after the export requests, with `exclude_flags` a list joined for them (`_export_body`); duplicates are left out by default. Loss masks, the license and label filters and the subfolder limit are not in it yet. |

**A stage with nothing in scope is skipped, never started with `[]`.** Score, caption and
export read `image_ids: []` as the whole dataset (`if body.image_ids:`; the HTTP routes keep
that). So the image stages skip when the run produced no images, and treat a start
function's `job_id: None` as a skip too. Export takes no id list at all.
`test_a_stage_with_no_images_is_skipped_and_never_started_with_an_empty_list` holds it,
with a bystander image in the dataset that a `[]` would sweep in.

**Known limit:** `start_score_job` and `start_caption_job` put the whole id list in one
`IN`, unchunked, so a run of more than about 32,000 images would hit SQLite's bound-variable
limit and fail its score stage. It is the class M8's follow-up 9 tracks
(`docs/dev/cleanup-plan/README.md` § Stage 1 follow-ups). No UI sent lists that long
before the run, and a run that long is unlikely.

**Rows waiting for a §3 rewrite pause the run** at `prompts`, with "N rows wait for a
rewrite. Run it on the ComfyUI page, then resume." Top up refuses them for the same reason
(`docs/dev/comfyui/structured.md` § Top up). An unattended run cannot review which scenarios
to rewrite, so it does not start the rewrite job itself.

**Unloading the caption model** (`free_caption_model`, `_free_caption_model`). The
OpenAI-compatible API has no unload, so each kind of model is freed its own way, and
every failure is a note:
- **An LM Studio provider** goes through LM Studio's native REST API (0.4 and later),
  which sits beside the provider's `/v1` base URL: `GET /api/v1/models` lists each model's
  `loaded_instances`, and `POST /api/v1/models/unload` takes one's `instance_id`. Every
  instance whose model key is the caption model's name, or whose id is that name or that
  name plus `:N`, is unloaded. A server without that API answers 404, which the note names.
- **A remote provider** (`schemas/openai_provider._is_remote`) is left alone: its models
  are not on this GPU.
- **Ollama** gets a `/api/generate` with `keep_alive: 0`, its documented unload-now.
- **A local captioner** (Florence-2, JoyCaption, PaliGemma, WD14) goes through `evict_all`,
  as `handoff_to_comfy` does.

All LLM-server calls go through `run_service._LLM_TRANSPORT`, which the tests set to an
`httpx.MockTransport`. The two VRAM handoffs before it still do nothing for an LLM: §4's
stages make no LLM call before captioning.

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
  with its summary, and a run with no stage left is `completed` rather than paused.
- **Delete** (`DELETE /runs/{id}`) refuses a live run with a 409.

## The Runs page

`frontend/src/pages/RunsPage.tsx`, routed at `/datasets/:datasetId/runs` (page type
`runs`, pickable from a pane, sidebar entry "Runs"), with `frontend/src/api/runs.ts`. It
is **not keyed** by dataset id in `PageRenderer`. The form re-seeds through a
`prevDatasetId` effect, as `ComfyPage` does.

- With no live run it shows the start form: the plan, the stage switches, the base folder
  and the aesthetic model, then the caption stage's fields (`components/runs/RunCaptionFields.tsx`)
  and the export stage's (`RunExportFields.tsx`). They are the Captioning and Export pages'
  own fields, minus scope, and reuse their controls: `DelimiterControls`,
  `ResolutionPicker`, the provider `ModelPicker`, the saved prompt presets and
  `FLAG_OPTIONS`. The form state uses those pages' names, and `components/runs/runForms.ts`
  converts it to the run's settings.
- **"Fill from the Captioning page"** copies that page's saved workflow
  (`appDataApi.getCaptioningWorkflow`), except its scope, which a run does not have.
  **"Fill from the Export page"** copies the Export page's browser-stored workflow and this
  dataset's filters; their shapes and defaults moved to `constants/exportWorkflow.ts` so
  the Runs page can read them without importing `ExportPage`. Both are optional: the form
  starts from defaults. Start is disabled with the first
  reason (`blockers`) until the form is complete. The start answers 200 even for a run
  whose first stage could not start, so the toast reads the returned status: a `failed`
  run shows its error, not "Run started".
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
- the restart reconcile, including a stage whose child completed during the shutdown;
  a resumed stage dropping its old note; a pause during the last stage;
- the rewrite pause, the start-time checks, and the stale guard.

Its `_block_worker` holds the single worker on a gate job, which keeps a child queued
deterministically. `backend/tests/test_caption_start_scope.py` pins what
`start_caption_job` puts in scope, including the `[]` widening. `frontend/e2e/runs.spec.ts`
runs a blueprint through prompts, both handoffs, generate and a plain export from the page.
