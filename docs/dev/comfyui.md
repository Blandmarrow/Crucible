# ComfyUI — the queue, runs, prompts and blueprints

`ComfyPage` drives a ComfyUI server the user runs: a per-dataset **plan** (a workflow
template plus pinned params) holds a queue of prompt **rows**, a run sends each row to
ComfyUI and imports what comes back into the dataset, an LLM can write the prompts, and a
**blueprint** expands one described dataset into foldered rows, and an automated **run**
steps from filling a blueprint through generating, scoring, captioning and exporting,
pass after pass until every folder is full, its settings kept as recipes. This file is the family's
entry point: the tables below route from a code path to whichever file in
`docs/dev/comfyui/` owns it. LLM providers themselves are `docs/dev/llm-providers.md`; the
user-facing guides are `docs/comfyui.md`, `docs/comfy-queue.md`, `docs/comfy-structured.md`, `docs/runs.md`,
`docs/run-setup.md` and `docs/repeating-runs.md`. M6's plan is
`docs/dev/comfy-automation-plan/README.md`.

## The family

| File | Covers |
|---|---|
| [queue.md](comfyui/queue.md) | The data model (plans, pins, rows, the per-row folder, the prompt library), `comfy_service.py`, ComfyClient, the `comfy` router's queue endpoints, and the gotchas shared by all of it |
| [run.md](comfyui/run.md) | The `comfy_generate` job behind `POST /comfy/run`: per-row folders and file stems, per-row `refresh_stats`, removing our prompt from ComfyUI's queue, and the fake ComfyUI it is tested against |
| [page.md](comfyui/page.md) | `ComfyPage` and `frontend/src/components/comfy/`: the plan rail, the folder tree, the grouped rows table and the Folder cell's lock, `activeRuns`, the remembered plan and tab, the layout (scroller, sticky header, the Rows tab's rail) |
| [library-dnd.md](comfyui/library-dnd.md) | The Rows tab's library side panel (`ComfyLibraryPanel`: the category tree, prompt edits, *Other plans*), Copy to plan, the row menu, and drag and drop with the button route behind every drag |
| [blueprint-tab.md](comfyui/blueprint-tab.md) | `BlueprintPanel`: saving, the seed, the editors, the outline draft and its review, `BlueprintPreview`, Apply and Top up, the rewrite button, their e2e, and the tab's two columns, jump bar and collapsible sections |
| [prompts.md](comfyui/prompts.md) | Writing queue prompts with an LLM: the one-shot endpoint, the `comfy_prompts` job, `parse_prompts`, `GeneratePromptsModal` |
| [structured.md](comfyui/structured.md) | Blueprints and leaves in the DB, fill accounting, orphans and adoption, Apply's guard rails and the plan lock, Top up |
| [structured-expansion.md](comfyui/structured-expansion.md) | `BlueprintSpec` and the pure `blueprint_expand.py`: combinations, budgets, sampling and sha256 seeds, folder paths, templates and the template walk |
| [structured-llm.md](comfyui/structured-llm.md) | The LLM calls of structured generation: the outline call (`comfy_outline`, `generate_outline`, `parse_outline`), the order of standing instructions in every call, and the optional rewrite pass (`comfy_rewrite`, `RewriteSession`, `blueprint_rewrite.py`) |
| [automation.md](comfyui/automation.md) | Automated runs: the `Run` row, `advance_run` and the worker's post-terminal hook, cancel (and the cancel that waits for a snapshot), pause, resume and restart |
| [run-stages.md](comfyui/run-stages.md) | What each stage of a run does: the skip-on-empty rule, the snapshot stage, the VRAM handoffs, chunked id lists, rows waiting for a rewrite, unloading the caption model |
| [run-start.md](comfyui/run-start.md) | The shared start check and the dry run (`plan_run`), and the Runs page: the start form, the stage list, past runs |
| [run-loop.md](comfyui/run-loop.md) | The goal loop (M6 §5): the head, the body and the tail, `pass_no` and `next_pass`, abandoning a folder, the rewrite stage, the Runs page's Repeat and Rewrite sections |
| [run-gate.md](comfyui/run-gate.md) | The run gate: `run_gate.py` and its job, `rejects/`, `gate_rejected` and its clearing (`gate_flags.py`), the Gate section |
| [run-budgets.md](comfyui/run-budgets.md) | The budgets and the paid-call cap, carrying on past a cap (`extend_run`, `_tail_rows`), the seed check |
| [recipes.md](comfyui/recipes.md) | The run recipe library (`run_recipes`, `/run-recipes`, the export document and what never travels), `RunFormState` and the form ↔ recipe converters, the recipe bar, the remembered start form (`run_form`) |
| [sync.md](comfyui/sync.md) | Workflow sync: "Sync from canvas", `GET /comfy/canvas-workflow`, the bridge extension and ComfyUI's API constraints |

## Where things live

Each row names the file that **owns** that code. A `+` clause is a second genuine owner,
not a see-also, and says which part of the code it owns.

### Backend

| Editing… | Read |
|---|---|
| `backend/models/comfy.py` (`ComfyPlan`, `ComfyRow`, `ComfyLibraryPrompt`) | `docs/dev/comfyui/queue.md` § Concepts & data model, including `prompt_origin` and its writers; + `docs/dev/comfyui/structured.md` § Tables for the blueprint and leaf tables, and the `leaf_id` column |
| `backend/services/comfy_service.py` (`ComfyClient`, `patch_workflow`, `extract_output_images`, `workflow_format`) | `docs/dev/comfyui/queue.md` § Backend; + `docs/dev/comfyui/run.md` § Removing our prompt for `cancel_prompt`; + `docs/dev/comfyui/sync.md` § Backend for `bridge_snapshot`, `last_history_entry` and `history_entry_workflow` |
| `backend/routers/comfy.py` (plans, rows, library, bulk-edit, `/ping`, workflow-file scan) | `docs/dev/comfyui/queue.md` § Backend; + `docs/dev/comfyui/sync.md` § Backend for `GET /comfy/canvas-workflow` |
| `backend/services/comfy_library.py` (`normalize_category`, `plan_category_move`), migration `e7d1c5a9b3f2` | `docs/dev/comfyui/queue.md` § Backend, the prompt library's nested categories |
| `backend/services/comfy_rows.py` (`place_rows`) | `docs/dev/comfyui/queue.md` § Backend (`rows/place`) |
| `backend/routers/comfy.py` (`POST /comfy/run`, `_run_preflight`, `_start_run`, `_run_with_stats`) | `docs/dev/comfyui/run.md` § The `comfy_generate` job; + `docs/dev/comfyui/structured.md` § Top up for `_start_run`'s second caller; + `docs/dev/comfyui/run-stages.md` § The stages for its run keywords |
| `backend/routers/comfy.py` (`generate-prompts` and the `comfy_prompts` job) | `docs/dev/comfyui/prompts.md` § Generating and § The `comfy_prompts` job |
| `backend/routers/comfy.py` (blueprint GET/PUT/preview/apply/top-up, `fill_blueprint`, `_plan_lock`, `_plan_job_active`) | `docs/dev/comfyui/structured.md` § Endpoints and Apply's guard rails and § Top up; + `docs/dev/comfyui/run.md` § The `comfy_generate` job for the `_plan_lock` that `run_plan` shares |
| `backend/routers/comfy.py` (`…/blueprint/outline`, `DELETE …/blueprint/draft`, the `comfy_outline` job) | `docs/dev/comfyui/structured-llm.md` § The outline call |
| `backend/routers/comfy.py` (`GET`/`POST …/blueprint/rewrite`, the `comfy_rewrite` job, `_ROW_JOB_TYPES`, `_mark_manual_if_prompt_changed`) | `docs/dev/comfyui/structured-llm.md` § The rewrite pass; + `docs/dev/comfyui/structured.md` § Top up for Top up's refusal |
| `backend/ml/prompt_generator.py` | `docs/dev/comfyui/prompts.md` § Generating for `generate_prompts` and `parse_prompts`; + `docs/dev/comfyui/structured-llm.md` § The outline call for `generate_outline`, `parse_outline`, `_strip_reasoning` and `_with_standing`, and § The rewrite pass for `RewriteSession`; outside this family: `docs/dev/llm-providers.md` for the provider row and client settings |
| `backend/schemas/comfy_blueprint.py` | `docs/dev/comfyui/structured-expansion.md` § The spec |
| `backend/services/blueprint_expand.py` | `docs/dev/comfyui/structured-expansion.md` § Expansion; + `docs/dev/comfyui/structured.md` § Fill accounting for `plan_fill`'s rules |
| `backend/services/blueprint_service.py` | `docs/dev/comfyui/structured.md` § Fill accounting and § Orphans |
| `backend/services/blueprint_rewrite.py` | `docs/dev/comfyui/structured-llm.md` § The rewrite pass |
| `backend/models/run.py`, `backend/schemas/run.py`, `backend/services/run_service.py`, `backend/routers/runs.py` | `docs/dev/comfyui/automation.md`; + `docs/dev/comfyui/run-stages.md` for the stage functions and `docs/dev/comfyui/run-start.md` for `plan_run`; + `docs/dev/comfyui/run-loop.md` for `LoopSettings`, `RewriteSettings`, `HEAD_STAGES`/`BODY_STAGES`, `next_pass` and `RunOut.outcome`/`leaves`; + `docs/dev/comfyui/run-gate.md` for `GateSettings`; + `docs/dev/comfyui/run-budgets.md` for `_spent`/`_budget_stop`, `extend_run`, `check_seeds` and `RunOut.can_extend`; + `docs/dev/comfyui/recipes.md` for `RunRecipeDraft` and the `Recipe*` schemas; outside this family: `docs/dev/backend-infrastructure.md` § After a job ends: the run hook for `_after_terminal` |
| `backend/services/run_gate.py` | `docs/dev/comfyui/run-gate.md` § The gate |
| `backend/services/llm_server.py`, `ThresholdSettings.llm_gpu_handoff`, migration `a3c7e9b1d5f2` | `docs/dev/comfyui/gpu-handoff.md` |
| `backend/models/run_recipe.py`, `backend/services/run_recipe_service.py`, `backend/routers/run_recipes.py` | `docs/dev/comfyui/recipes.md` |
| `backend/routers/versioning.py` (`start_snapshot_job`) | `docs/dev/comfyui/run-stages.md` § The stages; outside this family: `docs/dev/versioning.md` for the snapshot itself |
| `backend/services/blueprint_service.py` (`fill_plan`) | `docs/dev/comfyui/run-start.md` § Starting, and the checks it runs first |
| `backend/routers/comfy.py` (`start_rewrite_job`, `_refuse_live_run`) | `docs/dev/comfyui/run-loop.md` § The rewrite stage and § Manual Apply and Top up during a run |
| `extras/ComfyUI-CrucibleBridge/__init__.py`, `extras/ComfyUI-CrucibleBridge/js/crucible_bridge.js` | `docs/dev/comfyui/sync.md` § `extras/ComfyUI-CrucibleBridge/` |
| `backend/utils.py` (`join_subfolder`, `normalize_subfolder`) | outside this family: `docs/dev/shared-utilities.md`; the ComfyUI-specific rule is `docs/dev/comfyui/queue.md` § Gotchas |

### Frontend

| Editing… | Read |
|---|---|
| `frontend/src/pages/ComfyPage.tsx` | `docs/dev/comfyui/page.md` § Frontend for routing, the header, the plan rail and `activeRuns`; + `docs/dev/comfyui/prompts.md` § Frontend for the generate-prompts start mutation, which passes `planId` through its variables |
| `frontend/src/api/comfy.ts` | `docs/dev/comfyui/page.md` § Frontend |
| `frontend/src/components/comfy/ComfyRowsTable.tsx`, `ComfyRowsSelectionBar.tsx`, `ComfyDefaultsStrip.tsx`, `ComfyRunBar.tsx`, `SetRowFolderModal.tsx`, `BulkEditRowsModal.tsx` | `docs/dev/comfyui/page.md` § Frontend |
| `frontend/src/components/comfy/ComfyPlanRail.tsx`, `ComfyFolderTree.tsx`, `folderTree.ts`, `rowsView.ts`, `DuplicatePlanModal.tsx`, `FolderNameModal.tsx` | `docs/dev/comfyui/page.md` § The plan rail, the folder tree and the grouped table; the endpoints behind them are `docs/dev/comfyui/queue.md` § Backend |
| `frontend/src/hooks/useStickyOffsets.ts`, the `.comfy-head` and `.comfy-rail*`/`.comfy-rows-*` rules in `frontend/src/index.css` | `docs/dev/comfyui/page.md` § The layout |
| `frontend/src/components/comfy/BlueprintSection.tsx`, the `.bp-*` rules in `frontend/src/index.css` | `docs/dev/comfyui/blueprint-tab.md` § The layout |
| `frontend/src/components/comfy/WorkflowPinPanel.tsx`, `WorkflowScanModal.tsx` | `docs/dev/comfyui/page.md` § Frontend; + `docs/dev/comfyui/sync.md` § Frontend for the *Sync from canvas* button |
| `frontend/src/components/comfy/SyncCanvasModal.tsx` | `docs/dev/comfyui/sync.md` § Frontend |
| `frontend/src/components/comfy/ComfyLibraryPanel.tsx`, `CopyRowsToPlanModal.tsx`, `copyRows.ts`, `dnd.ts`, `DndRow.tsx`, `LinkedAskDialog.tsx`, the row menu and drop targets in `ComfyRowsTable.tsx`, `frontend/e2e/comfy-library.spec.ts`, `comfy-dnd.spec.ts` | `docs/dev/comfyui/library-dnd.md`; the endpoints they call are `docs/dev/comfyui/queue.md` § Backend |
| `frontend/src/components/comfy/SaveToLibraryModal.tsx` | `docs/dev/comfyui/page.md` § Frontend |
| `frontend/src/components/comfy/GeneratePromptsModal.tsx` | `docs/dev/comfyui/prompts.md` § Frontend |
| `frontend/src/components/comfy/BlueprintPanel.tsx`, `BlueprintPreview.tsx`, `BlueprintAxesEditor.tsx`, `BlueprintVariationsEditor.tsx`, `BlueprintScenariosEditor.tsx`, `BlueprintNameField.tsx`, `blueprintKeys.ts` | `docs/dev/comfyui/blueprint-tab.md` § The panel; + `docs/dev/comfyui/structured.md` § Endpoints and Apply's guard rails for what Apply and Top up send and the server guards behind the buttons; + `docs/dev/comfyui/structured-expansion.md` § Expansion for the `defaultPath`/`defaultTemplate` mirrors |
| `frontend/src/components/comfy/OutlineModal.tsx`, `DraftReview.tsx` | `docs/dev/comfyui/structured-llm.md` § The outline call for the job; + `docs/dev/comfyui/blueprint-tab.md` § The panel for the modal, the draft review, the draft query and *Use it* |
| `frontend/src/components/comfy/RewriteModal.tsx` | `docs/dev/comfyui/blueprint-tab.md` § The panel for the modal and the estimate query; + `docs/dev/comfyui/structured-llm.md` § The rewrite pass for the job and `confirm_budget` |
| `frontend/src/pages/RunsPage.tsx`, `frontend/src/api/runs.ts`, `frontend/src/components/runs/RunCaptionFields.tsx`, `RunExportFields.tsx`, `runForms.ts`, `DryRunReport.tsx` | `docs/dev/comfyui/run-start.md` § The Runs page; + `docs/dev/comfyui/run-loop.md` § The Runs page for the pass headings and `LoopOutcome`; + `docs/dev/comfyui/run-gate.md` § The Runs page's Gate section; + `docs/dev/comfyui/recipes.md` for the form state, `recipesApi` and the remembered form |
| `frontend/src/components/runs/runRecipeForm.ts`, `RecipeBar.tsx` | `docs/dev/comfyui/recipes.md` |
| `frontend/src/components/runs/ExtendRunDialog.tsx` | `docs/dev/comfyui/run-budgets.md` § Raising a cap and carrying on |
| `frontend/src/components/runs/RunLoopFields.tsx`, `runLoopForms.ts` | `docs/dev/comfyui/run-loop.md` § The Runs page; + `docs/dev/comfyui/run-gate.md` § The Runs page's Gate section |
| `frontend/src/components/layout/TopBar.tsx` (the `comfy_prompts`, `comfy_outline` and `comfy_rewrite` branches, and `comfy_generate` in `LIVE_IMAGE_JOB_TYPES`) | `docs/dev/comfyui/prompts.md` § Frontend for the `comfy_prompts` branch; + `docs/dev/comfyui/page.md` § Frontend for the `comfy_generate` live refresh and the `comfy_outline` and `comfy_rewrite` branches; outside this family: `docs/dev/job-invalidation.md` |

### Tests

| Editing… | Read |
|---|---|
| `backend/tests/fake_comfy.py`, `backend/tests/test_comfy_run_body.py`, `backend/tests/test_comfy_queue_cleanup.py`, `frontend/e2e/serve-comfy.mjs` | `docs/dev/comfyui/run.md` § Testing the run |
| `backend/tests/test_comfy_cancel_stats.py`, `backend/tests/test_comfy_subfolder.py` | `docs/dev/comfyui/run.md` § The `comfy_generate` job (the per-row stats refresh; the per-row folder and stem) |
| `backend/tests/test_comfy_rows_place.py`, `test_comfy_rows_copy.py`, `test_comfy_plan_duplicate.py` | `docs/dev/comfyui/queue.md` § Backend (`rows/place`, `rows/copy`, plan duplicate, and the live run's locks) |
| `backend/tests/test_comfy_library_http.py`, `test_comfy_library_service.py`, `test_migration_nest_library_categories.py` | `docs/dev/comfyui/queue.md` § Backend (the prompt library) |
| `backend/tests/test_comfy_bulk_edit_regex_http.py` | `docs/dev/comfyui/queue.md` § Backend (the `rows/bulk-edit` regex handling) |
| `backend/tests/test_parse_prompts.py`, `backend/tests/test_conflict_paths_http.py` | `docs/dev/comfyui/prompts.md` § Generating (the parser) and § The `comfy_prompts` job (the per-plan 409s) |
| `backend/tests/test_blueprint_expand.py` | `docs/dev/comfyui/structured-expansion.md` § Testing |
| `backend/tests/test_blueprint_http.py`, `backend/tests/test_blueprint_top_up_http.py` | `docs/dev/comfyui/structured.md` § Testing and § Top up |
| `backend/tests/test_blueprint_outline_http.py`, `backend/tests/test_parse_outline.py` | `docs/dev/comfyui/structured-llm.md` § The outline call |
| `backend/tests/test_prompt_rewrite.py`, `backend/tests/test_blueprint_rewrite.py`, `backend/tests/test_blueprint_rewrite_http.py` | `docs/dev/comfyui/structured-llm.md` § The rewrite pass |
| `backend/tests/test_run_stepping.py`, `backend/tests/test_caption_start_scope.py`, `backend/tests/test_run_snapshot.py`, `backend/tests/test_run_dry_run.py`, `frontend/e2e/runs.spec.ts` | `docs/dev/comfyui/automation.md` § Testing; the dry run's tests: `docs/dev/comfyui/run-start.md` § Testing |
| `backend/tests/test_run_loop.py` | `docs/dev/comfyui/run-loop.md` § Testing |
| `backend/tests/test_run_extend.py` | `docs/dev/comfyui/run-budgets.md` § Testing |
| `backend/tests/test_run_recipes_http.py` | `docs/dev/comfyui/recipes.md` § Testing |
| `frontend/e2e/comfy.spec.ts` | `docs/dev/comfyui/page.md` § Frontend |
| `frontend/e2e/comfy-run.spec.ts` | `docs/dev/comfyui/page.md` § Frontend for the journeys and the blueprint spec; + `docs/dev/comfyui/run.md` § Testing the run for the fake ComfyUI it points at |
