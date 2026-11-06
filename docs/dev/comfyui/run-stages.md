# ComfyUI — the stages of a run

An automated run (`docs/dev/comfyui/automation.md`) is a list of stages, and each one either
queues an ordinary child job or finishes inline. This file covers what each stage does, the
rule that a stage with nothing in scope is skipped, the chunked id lists, rows waiting for a
rewrite, and unloading the caption model. How the stages are stepped (`advance_run`, the four
outcomes of `_start_next`, the run lock and the plan lock) is `docs/dev/comfyui/automation.md`
§ Stepping: `_after_terminal` and `advance_run`. What runs before the first stage is
`docs/dev/comfyui/run-start.md`. The loop's `rewrite`, `free_rewrite_model` and `next_pass`
stages are `docs/dev/comfyui/run-loop.md` and the `gate` stage is
`docs/dev/comfyui/run-gate.md`.

## The stages

| Stage | What it does |
|---|---|
| `snapshot` | M6 §6, opt-in. A `create_snapshot` job through `routers/versioning.start_snapshot_job`, lifted out of the snapshot route and always the job path (the route keeps its inline path for small datasets in auto mode). Skipped with a note when `versioning_mode` is off. The only **head** stage (`HEAD_STAGES`): it has no `pass_no`, so `next_pass` never copies it into a later pass. The job writes `{version_id, name, images}` to `result_data` after the snapshot's own commit. |
| `prompts` | Top up's fill on the **stored** blueprint, through `routers/comfy.fill_blueprint` under the plan lock (Apply, then `prepare_top_up`). `confirm_total=None`, since the lock holds from Apply's count to its write. Stores the pending row ids in `context`. With no blueprint it is skipped. A first pass over the row cap is refused at the start (`docs/dev/comfyui/run-start.md` § Starting, and the checks it runs first); the stage still fails with Apply's message if the blueprint grew past the cap between the start and the stage. |
| `handoff_to_comfy` | A `run_handoff` job: `model_manager.evict_all()`. The note counts the models that were resident (`evict_all` returns every registered id) and names any still leased. `evict_all` ends with a torch cache flush that raises on a torch-free install; that is caught, because nothing torch-backed was loaded there. With Settings → ComfyUI's GPU handoff on (`llm_gpu_handoff`), it also unloads every loaded local Strata server, each one's note appended (`docs/dev/comfyui/gpu-handoff.md`). |
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

**Long id lists are chunked.** `start_score_job` and `start_caption_job` run an
`image_ids` filter through `utils.chunked` and gather the rows, de-duplicating the ids
first. A run can pass up to 100,000 ids, past SQLite's bound-variable limit (the class
M8's follow-up 9 tracks, `docs/dev/cleanup-plan/README.md` § Stage 1 follow-ups).
`backend/tests/test_start_id_chunking.py` holds it with more ids than one chunk.

**Rows waiting for a §3 rewrite pause the run** at `prompts`, with "N rows wait for a
rewrite. Run it on the ComfyUI page, then resume." Top up refuses them for the same reason
(`docs/dev/comfyui/structured.md` § Top up). An unattended run cannot review which scenarios
to rewrite on its own, so only the scenarios ticked on the start form are rewritten, by
the run's own `rewrite` stage (`start_rewrite_job`; `docs/dev/comfyui/run-loop.md` § The
rewrite stage). Rows of any other rewrite-enabled scenario still pause the run. The
`rewrite`, `free_rewrite_model` and `next_pass` stages are that file's too; the `gate` stage is
`docs/dev/comfyui/run-gate.md`.

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
- **A Strata server** (its `/health` answers `"service": "strata"`) is checked before LM
  Studio's API, which it lacks: `POST /v1/unload` when the GPU handoff setting is on, and a
  note that it stays loaded when it is off (`docs/dev/comfyui/gpu-handoff.md`).

All LLM-server calls go through `run_service._LLM_TRANSPORT`, which the tests set to an
`httpx.MockTransport`. Of the two VRAM handoffs, only `handoff_to_comfy` acts on an LLM, and
only on a Strata server with the setting on. Since §5 a rewrite
stage can run an LLM before generating, and `free_rewrite_model` unloads it after the
rewrite through the same `_free_llm` (`docs/dev/comfyui/run-loop.md`).
