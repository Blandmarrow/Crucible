# ComfyUI — the queue, runs, prompts and blueprints

`ComfyPage` drives a ComfyUI server the user runs: a per-dataset **plan** (a workflow
template plus pinned params) holds a queue of prompt **rows**, a run sends each row to
ComfyUI and imports what comes back into the dataset, an LLM can write the prompts, and a
**blueprint** expands one described dataset into foldered rows. This file is the family's
entry point: the tables below route from a code path to whichever file in
`docs/dev/comfyui/` owns it. LLM providers themselves are `docs/dev/llm-providers.md`; the
user-facing guides are `docs/comfyui.md` and `docs/comfy-structured.md`. M6's remaining
stages are in `docs/dev/comfy-automation-plan/README.md`.

## The family

| File | Covers |
|---|---|
| [queue.md](comfyui/queue.md) | The data model (plans, pins, rows, the per-row folder, the prompt library), `comfy_service.py`, ComfyClient, the `comfy` router's queue endpoints, and the gotchas shared by all of it |
| [run.md](comfyui/run.md) | The `comfy_generate` job behind `POST /comfy/run`: per-row folders and file stems, per-row `refresh_stats`, removing our prompt from ComfyUI's queue, and the fake ComfyUI it is tested against |
| [page.md](comfyui/page.md) | `ComfyPage` and `frontend/src/components/comfy/`: the Folder cell, `activeRuns`, the prompt library modals, the Blueprint tab and its outline review |
| [prompts.md](comfyui/prompts.md) | Writing queue prompts with an LLM: the one-shot endpoint, the `comfy_prompts` job, `parse_prompts`, `GeneratePromptsModal` |
| [structured.md](comfyui/structured.md) | Blueprints and leaves in the DB, fill accounting, orphans and adoption, Apply's guard rails and the plan lock, Top up |
| [structured-expansion.md](comfyui/structured-expansion.md) | `BlueprintSpec` and the pure `blueprint_expand.py`: combinations, budgets, sampling and sha256 seeds, folder paths, templates and the template walk |
| [structured-llm.md](comfyui/structured-llm.md) | The LLM calls of structured generation: the outline call (`comfy_outline`, `generate_outline`, `parse_outline`), the order of standing instructions in every call, and the optional rewrite pass (`comfy_rewrite`, `RewriteSession`, `blueprint_rewrite.py`) |
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
| `backend/routers/comfy.py` (`POST /comfy/run`, `_run_preflight`, `_start_run`, `_run_with_stats`) | `docs/dev/comfyui/run.md` § The `comfy_generate` job; + `docs/dev/comfyui/structured.md` § Top up for `_start_run`'s second caller |
| `backend/routers/comfy.py` (`generate-prompts` and the `comfy_prompts` job) | `docs/dev/comfyui/prompts.md` § Generating and § The `comfy_prompts` job |
| `backend/routers/comfy.py` (blueprint GET/PUT/preview/apply/top-up, `_plan_lock`, `_plan_job_active`) | `docs/dev/comfyui/structured.md` § Endpoints and Apply's guard rails and § Top up; + `docs/dev/comfyui/run.md` § The `comfy_generate` job for the `_plan_lock` that `run_plan` shares |
| `backend/routers/comfy.py` (`…/blueprint/outline`, `DELETE …/blueprint/draft`, the `comfy_outline` job) | `docs/dev/comfyui/structured-llm.md` § The outline call |
| `backend/routers/comfy.py` (`GET`/`POST …/blueprint/rewrite`, the `comfy_rewrite` job, `_ROW_JOB_TYPES`, `_mark_manual_if_prompt_changed`) | `docs/dev/comfyui/structured-llm.md` § The rewrite pass; + `docs/dev/comfyui/structured.md` § Top up for Top up's refusal |
| `backend/ml/prompt_generator.py` | `docs/dev/comfyui/prompts.md` § Generating for `generate_prompts` and `parse_prompts`; + `docs/dev/comfyui/structured-llm.md` § The outline call for `generate_outline`, `parse_outline`, `_strip_reasoning` and `_with_standing`, and § The rewrite pass for `RewriteSession`; outside this family: `docs/dev/llm-providers.md` for the provider row and client settings |
| `backend/schemas/comfy_blueprint.py` | `docs/dev/comfyui/structured-expansion.md` § The spec |
| `backend/services/blueprint_expand.py` | `docs/dev/comfyui/structured-expansion.md` § Expansion; + `docs/dev/comfyui/structured.md` § Fill accounting for `plan_fill`'s rules |
| `backend/services/blueprint_service.py` | `docs/dev/comfyui/structured.md` § Fill accounting and § Orphans |
| `backend/services/blueprint_rewrite.py` | `docs/dev/comfyui/structured-llm.md` § The rewrite pass |
| `extras/ComfyUI-CrucibleBridge/__init__.py`, `extras/ComfyUI-CrucibleBridge/js/crucible_bridge.js` | `docs/dev/comfyui/sync.md` § `extras/ComfyUI-CrucibleBridge/` |
| `backend/utils.py` (`join_subfolder`, `normalize_subfolder`) | outside this family: `docs/dev/shared-utilities.md`; the ComfyUI-specific rule is `docs/dev/comfyui/queue.md` § Gotchas |

### Frontend

| Editing… | Read |
|---|---|
| `frontend/src/pages/ComfyPage.tsx` | `docs/dev/comfyui/page.md` § Frontend for routing, the plan bar and `activeRuns`; + `docs/dev/comfyui/prompts.md` § Frontend for the generate-prompts start mutation, which passes `planId` through its variables |
| `frontend/src/api/comfy.ts` | `docs/dev/comfyui/page.md` § Frontend |
| `frontend/src/components/comfy/ComfyRowsTable.tsx`, `ComfyDefaultsStrip.tsx`, `ComfyRunBar.tsx`, `SetRowFolderModal.tsx`, `BulkEditRowsModal.tsx` | `docs/dev/comfyui/page.md` § Frontend |
| `frontend/src/components/comfy/WorkflowPinPanel.tsx`, `WorkflowScanModal.tsx` | `docs/dev/comfyui/page.md` § Frontend; + `docs/dev/comfyui/sync.md` § Frontend for the *Sync from canvas* button |
| `frontend/src/components/comfy/SyncCanvasModal.tsx` | `docs/dev/comfyui/sync.md` § Frontend |
| `frontend/src/components/comfy/PromptLibraryModal.tsx`, `SaveToLibraryModal.tsx` | `docs/dev/comfyui/page.md` § Frontend; the endpoints they call are `docs/dev/comfyui/queue.md` § Backend |
| `frontend/src/components/comfy/GeneratePromptsModal.tsx` | `docs/dev/comfyui/prompts.md` § Frontend |
| `frontend/src/components/comfy/BlueprintPanel.tsx`, `BlueprintPreview.tsx`, `BlueprintAxesEditor.tsx`, `BlueprintVariationsEditor.tsx`, `BlueprintScenariosEditor.tsx`, `BlueprintNameField.tsx`, `blueprintKeys.ts` | `docs/dev/comfyui/page.md` § Frontend (the Blueprint tab); + `docs/dev/comfyui/structured.md` § Endpoints and Apply's guard rails for what Apply and Top up send and the server guards behind the buttons; + `docs/dev/comfyui/structured-expansion.md` § Expansion for the `defaultPath`/`defaultTemplate` mirrors |
| `frontend/src/components/comfy/OutlineModal.tsx`, `DraftReview.tsx` | `docs/dev/comfyui/structured-llm.md` § The outline call for the job; + `docs/dev/comfyui/page.md` § Frontend for the modal, the draft review, the draft query and *Use it* |
| `frontend/src/components/comfy/RewriteModal.tsx` | `docs/dev/comfyui/page.md` § Frontend for the modal and the estimate query; + `docs/dev/comfyui/structured-llm.md` § The rewrite pass for the job and `confirm_budget` |
| `frontend/src/components/layout/TopBar.tsx` (the `comfy_prompts`, `comfy_outline` and `comfy_rewrite` branches, and `comfy_generate` in `LIVE_IMAGE_JOB_TYPES`) | `docs/dev/comfyui/prompts.md` § Frontend for the `comfy_prompts` branch; + `docs/dev/comfyui/page.md` § Frontend for the `comfy_generate` live refresh and the `comfy_outline` and `comfy_rewrite` branches; outside this family: `docs/dev/frontend-jobs.md` |

### Tests

| Editing… | Read |
|---|---|
| `backend/tests/fake_comfy.py`, `backend/tests/test_comfy_run_body.py`, `backend/tests/test_comfy_queue_cleanup.py`, `frontend/e2e/serve-comfy.mjs` | `docs/dev/comfyui/run.md` § Testing the run |
| `backend/tests/test_comfy_cancel_stats.py`, `backend/tests/test_comfy_subfolder.py` | `docs/dev/comfyui/run.md` § The `comfy_generate` job (the per-row stats refresh; the per-row folder and stem) |
| `backend/tests/test_comfy_bulk_edit_regex_http.py` | `docs/dev/comfyui/queue.md` § Backend (the `rows/bulk-edit` regex handling) |
| `backend/tests/test_parse_prompts.py`, `backend/tests/test_conflict_paths_http.py` | `docs/dev/comfyui/prompts.md` § Generating (the parser) and § The `comfy_prompts` job (the per-plan 409s) |
| `backend/tests/test_blueprint_expand.py` | `docs/dev/comfyui/structured-expansion.md` § Testing |
| `backend/tests/test_blueprint_http.py`, `backend/tests/test_blueprint_top_up_http.py` | `docs/dev/comfyui/structured.md` § Testing and § Top up |
| `backend/tests/test_blueprint_outline_http.py`, `backend/tests/test_parse_outline.py` | `docs/dev/comfyui/structured-llm.md` § The outline call |
| `backend/tests/test_prompt_rewrite.py`, `backend/tests/test_blueprint_rewrite.py`, `backend/tests/test_blueprint_rewrite_http.py` | `docs/dev/comfyui/structured-llm.md` § The rewrite pass |
| `frontend/e2e/comfy.spec.ts` | `docs/dev/comfyui/page.md` § Frontend |
| `frontend/e2e/comfy-run.spec.ts` | `docs/dev/comfyui/page.md` § Frontend for the journeys and the blueprint spec; + `docs/dev/comfyui/run.md` § Testing the run for the fake ComfyUI it points at |
