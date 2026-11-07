# ComfyUI — starting a run and the Runs page

Before a run exists, one check function decides whether it may start, and the dry run shows
that same answer without writing anything. This file covers that shared start check
(`plan_run`) and the Runs page: the start form, the dry run's report, the live stage list
and the list of past runs. What the stages do once the run is going is
`docs/dev/comfyui/run-stages.md`; how a run is stepped, cancelled and resumed is
`docs/dev/comfyui/automation.md`; the loop's own form sections are
`docs/dev/comfyui/run-loop.md` (the gate's in `docs/dev/comfyui/run-gate.md`), and the recipe bar is `docs/dev/comfyui/recipes.md`. The user
guide is `docs/runs.md`, with the form in `docs/run-setup.md`.

## Starting, and the checks it runs first

**One check function, `run_service.plan_run`, is behind both the start and the dry run**
(M6 §6), so what the dry run reports is what the start does. It writes nothing. Each
check that refuses raises `HTTPException`; `_Checks.run` records it as a `RunCheck`
(`key`, `level`, `message`, `status_code`) instead of raising, so the dry run can show
every problem at once. `POST /runs/` (`start_run`) raises the first error with its own
status before writing a run; `POST /runs/dry-run` returns the whole `RunPlanReport` with
200. Only a missing plan (404) or a plan of another dataset (400) raises at once in both.

The checks run in a fixed order, the pre-§6 ones first, so a start refuses with the status
it always did:
- the ComfyUI URL and workflow when a ComfyUI stage is on, the base folder, the prompt pin
  when the blueprint will be filled, the caption settings (by building the
  `CaptionJobRequest`), `export.check_export_request` (400 for a relative folder, 507 for
  a full disk), the rewrite's provider and scenarios, and a loop's blueprint, seeds and
  paid-call cap;
- **ComfyUI answers** (`comfy_router.ComfyClient(url).ping()`, called through the router
  module so the tests' fake applies);
- **the fill** (`blueprint_service.fill_plan`): `_compute` on the stored blueprint, the
  function `apply` runs, so an invalid blueprint or a first pass over the row cap is
  refused before the run exists. Rows a pass would generate in a folder are its new rows
  plus its open ones, which `prepare_top_up` resets and lists;
- **the caption and rewrite models**: the caption provider must exist (404; the request
  model checks only the id's prefix), and a model its server lists without the chosen
  one is a 400. A server that cannot be asked is a warning. The listing goes through
  `_LLM_TRANSPORT` (`docs/dev/comfyui/run-stages.md` § The stages);
- **the disk**, only when Generate is on: below the floor is a 507; short of the estimate
  (the mean size of the plan's images, else the dataset's, times the planned images, or
  times `loop.max_images` on a loop) is a warning;
- the live-run 409, last. `start_run` checks it again under `_dataset_lock`.

Warnings never block: versioning off with Snapshot on, rows that will wait for a rewrite,
the image cap clamping the first pass, an export folder `os.access` calls unwritable, a
disk short of the estimate, and "Could not list the models of <provider>, so the model was
not checked" (also worded for Ollama) when a server cannot be asked.

The report's first-pass rows use `round_robin_counts`, the split `_clamp_rows` itself
uses, so the clamp the dry run shows is the one the run applies. The per-folder split of
the last round can differ by one where a folder's open rows sort before another's new
ones; the total cannot.

The recipe's own validator 422s a caption or export stage with no settings, and puts the
stages into `STAGE_ORDER` whatever order they arrive in.

## The Runs page

`frontend/src/pages/RunsPage.tsx`, routed at `/datasets/:datasetId/runs` (page type
`runs`, pickable from a pane, sidebar entry "Runs"), with `frontend/src/api/runs.ts`. It
is **not keyed** by dataset id in `PageRenderer`. Its plan and base folder are kept per
dataset, so a dataset switch shows that dataset's own; a `prevDatasetId` effect, as on
`ComfyPage`, clears only the rewrite's ticked scenarios and the dry-run report.

- The start form's state is one `RunFormState` (`components/runs/runRecipeForm.ts`).
  `formToRecipe` builds the recipe that Start, Dry run and saving a recipe all send;
  `recipeToForm` reverses it for a loaded recipe or a past run's **Use these settings**,
  through the inverse converters, which all live in `runRecipeForm.ts`
  (`fromCaptionSettings`, `fromExportSettings` and the rest). The forward ones are in
  `runForms.ts` and `runLoopForms.ts`. The plan and base folder are
  kept per dataset, outside `RunFormState` proper, and a plan id this dataset does not
  have falls back to its first plan. The recipe bar, the remembered form and the inverses'
  traps are `docs/dev/comfyui/recipes.md`.
- **Dry run** shows `DryRunReport` under the form, marked out of date once the request
  the form would send no longer matches the one it reported on.
- With no live run it shows the start form: the plan, the stage switches, the base folder
  and the aesthetic model, then the caption stage's fields (`components/runs/RunCaptionFields.tsx`)
  and the export stage's (`RunExportFields.tsx`). They are the Captioning and Export pages'
  own fields, minus scope, and reuse their controls: `DelimiterControls`,
  `ResolutionPicker`, the provider `ModelPicker`, the saved prompt presets and
  `FLAG_OPTIONS`. The form state uses those pages' names, and `components/runs/runForms.ts`
  converts it to the run's settings. The custom export resize is a `NumberField` bounded
  1–16384 (the schema's bounds). The field and the start read the stored string through
  one parser, `customResizeValue`, so a blank or out-of-range value (one a recipe or the
  Export page's form carries in) shows and is sent as the 1024 default, never as a 422.
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
  rows. **It also refetches whenever any job of its dataset changes status**, read from the
  global job store (a key of job ids and statuses, so progress ticks do not count). The
  poll alone keys off the list's own last fetch, so a run started or stepped elsewhere —
  another tab or device, the API, another pane — stayed invisible, its TopBar cancel too,
  until the page was reopened (found on the box, 2026-11-07; `runs.spec.ts` "a run started
  elsewhere shows up on an open Runs page without a reload"). The children keep their own job types, so `TopBar`'s `LIVE_IMAGE_JOB_TYPES` and
  `IMAGE_MODIFYING_JOB_TYPES` fire their usual invalidations; `run_handoff` needs no entry.
- **The TopBar's paused-run chip.** `GET /runs/` takes an optional `dataset_id` (omitted: every
  dataset's runs) and an optional `status`. `TopBar` lists `status=paused` under `PAUSED_RUNS_KEY`
  (`["runs","paused-chip"]`, deliberately not under `["runs", datasetId]`), on a 30 s poll and on
  any job status change; `RunsPage` invalidates it when its own paused set changes, since a
  paused run's cancel emits no job event. The chip ("Run paused" / "N runs paused", `title` naming
  the runs) links to the Runs page of the newest paused run's dataset. A restart pauses every
  running run, which is what it is for (`runs.spec.ts` "a paused run shows an amber dot").
- Past runs are listed below, with Resume for a failed one, Delete, **Use these
  settings**, and **Raise caps and continue** where `RunOut.can_extend`
  (`docs/dev/comfyui/run-budgets.md` § Raising a cap and carrying on). The last two are not
  offered while a run is live, since no start form shows then.
- While a cancel waits for a snapshot, the run reads "cancelling after the snapshot", and
  its Pause and Resume are not offered. Cancel stays enabled, with a tooltip and a
  "Cancel again?" confirm, because a second press ends the run if the snapshot is gone.

## Testing

The dry run and the start check have their own tests. The stepping tests are
`docs/dev/comfyui/automation.md` § Testing. M6 §6 added:

- `backend/tests/test_run_dry_run.py`: nothing written, ComfyUI unreachable, a missing
  caption provider, the model list (listed, absent, unreachable), warnings not blocking,
  and the dry run's first-pass count equal to what the run generates, clamp included.
- `frontend/e2e/runs.spec.ts`'s dry-run journey.
