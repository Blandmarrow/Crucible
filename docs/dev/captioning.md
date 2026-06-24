# Captioning post-processing & LLM providers

This file covers caption job post-processing (merging, refusal stripping, rename-on-caption), job queuing/execution, and OpenAI-compatible provider configuration.

### Captioning post-processing

`CaptionJobRequest` (in `backend/routers/captioning.py`) accepts the following fields for post-processing, merging, and job display:

| Field | Default | Effect |
|---|---|---|
| `label` | `null` | Optional display name shown in the job queue. When omitted, the router auto-generates `"{model_short} — N images"`. |
| `delimiter_mode` | `"overwrite"` | How to merge new caption with existing: `"overwrite"` replaces, `"append"` adds new text after (`existing + delimiter + new`), `"prepend"` adds new text before (`new + delimiter + existing`). Merge only runs when an existing caption is non-empty; images with no prior caption always receive just the new caption regardless of mode. |
| `delimiter` | `", "` | Text inserted between the two strings when `delimiter_mode` is `"append"` or `"prepend"`. |
| `strip_refusals` | `true` | Remove common AI refusal phrases from generated captions via `_REFUSAL_RE` compiled regex. The regex is narrowly scoped: `"This image appears to be X"` (Florence-2 PromptGen v2 standard output) is NOT stripped; only actual refusal phrasing like `"be depicting"` is matched. |
| `strip_thinking` | `false` | Strips `<think>…</think>` / `<thinking>…</thinking>` blocks and a first-line thinking preamble ("Let me think/analyze…", "I'll describe…", "Alright, let's…"). Thinking is **detected regardless** of this flag — a match always writes `has_ai_artifacts` to `quality_flags`. |
| `strip_underscores` | `false` | `word_word` → `word word` (`(?<=\w)_(?=\w)`). Prose styles: applied to the whole text. Tag styles (`_TAG_STYLES`): applied per-tag before rejoining with `, `. |
| `strip_hedges` | `false` | Strips sentence-level hedging prefixes ("It appears that…", "Possibly,…", "Looking at this image,…"), re-capitalising the remainder. Hedges are **detected regardless** — a match always sets `has_ai_artifacts`. |
| `min_aesthetic_score` | `null` | Pre-filter applied in the initial DB query: only caption images with `aesthetic_score ≥` this value. `null` = no filter. |
| `exclude_flags` | `null` | Pre-filter: skip images with any of the listed quality flags set truthy; validated against `ALLOWED_FLAG_KEYS` via a `field_validator`. |
| `append_tags` | `true` | Tag styles only. When `delimiter_mode != "overwrite"`, new tags are deduplicated against the existing comma-separated `caption_text` (new tags win; remaining existing tags are appended). `false` = no merge. (`PipelineStep.append_tags` defaults `false` and merges against `prev_captions`.) |
| `save_backup` | `false` | Before calling `set_caption`, write the existing `.txt` sidecar to `.txt.bak`. |
| `rename_on_caption` | `false` | After saving each caption, rename the image file to `{subfolder_slug}_{NNN}.ext` (or `image_{NNN}.ext` for root). Sets `is_auto_named=True`. Subfolder and original filename are fetched from the initial bulk query — no per-image DB round-trip. |
| `wd14_threshold` | `0.35` | Minimum confidence (0–1) for a WD14 tag to be included in output. Only used when `model` starts with `wd14:`. |

`PipelineStep` has its own `delimiter_mode`, `delimiter`, `strip_refusals`, `strip_thinking`, `strip_underscores`, `strip_hedges`, `target_width`, `target_height`, and `wd14_threshold` fields — all configurable per step in the UI. `save_backup` and `rename_on_caption` live on `CaptionPipelineRequest` (pipeline-wide, not per-step). `prev_captions` is fetched from the DB at the start of each pipeline step when `delimiter_mode != "overwrite"` so the merge has the current caption text. When `delimiter_mode == "overwrite"` and `body.overwrite` is `False`, only images with empty captions are queued; append/prepend modes always process all images (the merge requires the existing caption).

**Captioning job execution**: `_run` in `routers/captioning.py` processes images one at a time (generate → save → emit SSE). Each event carries `image_id`, `throughput_ips`, and `vram_used_mb` (sampled every 10 images; Ollama always 0; WD14 and OpenAI-compat always 0). Failed images accumulate in `failed_image_ids`; a `caption_summary` SSE event is emitted after the loop if any failed. Cancellation is checked at each image boundary via a scalar `SELECT status` on the outer session (not a new `AsyncSessionLocal` per image). `has_ai_artifacts` is written to `quality_flags` whenever `_has_thinking()` or `_has_hedges()` matches the generated caption, **regardless** of whether `strip_thinking` / `strip_hedges` are enabled (stripping is independent of detection); `strip_refusals` does **not** set this flag. The flag flows through `set_caption(..., has_ai_artifacts=)`. Tag-producing styles (WD14, booru-style outputs, and the four JoyCaption tag styles) produce comma-separated text stored directly in `caption_text` — there is no longer a separate tags table. The `_TAG_STYLES` frozenset in `routers/captioning.py` identifies them; see `docs/dev/ml-models.md` § ML model management for the definition and JoyCaption tag-style details.

**Job queuing**: The backend `asyncio.Queue` in `workers/job_queue.py` runs caption and pipeline jobs serially — any number of jobs may be submitted while one is running. `enqueue()` emits a `"pending"` SSE event immediately after putting the job on the queue (before the worker picks it up), so the frontend knows the job exists right away. The worker checks the DB status after dequeuing; if a job was cancelled while pending it emits a `"cancelled"` SSE event and skips without running. The cancel endpoint (`DELETE /jobs/{id}`) accepts both `"running"` and `"pending"` status. The frontend tracks all submitted job IDs in `submittedJobIds: string[]`; `submittedActiveJobId` is the oldest non-terminal entry (gated only by `seenTerminalJobIds` ref, not live store status, to avoid the effectiveJobId race where the gallery invalidation never fires). `otherPendingJobs` (the queue list displayed in the CaptioningPage live-progress panel) is derived from `allActiveJobs` (the persistent Zustand store), not from `submittedJobIds`, so the list survives navigation/remount. `globalCaptionJob` (the fallback when `submittedActiveJobId` is null) also includes `"pending"` status for the same reason. Cancelling a pending job in either `CaptioningPage` or `TopBar` calls `useJobStore.getState().updateJob(id, { status: "cancelled" })` optimistically before the API call so the job disappears from the queue list immediately without waiting for an SSE event.

**Ollama timeout**: `httpx.AsyncClient` in `ollama_captioner.py` uses a 300-second timeout per image to accommodate slow hardware and cold model loads.

### OpenAI-compatible providers

**Router**: `backend/routers/providers.py`, prefix `/providers`. Registered in `main.py`. No service layer — CRUD is thin enough to live in the router.

| Endpoint | Behaviour |
|---|---|
| `GET /` | List all providers ordered by `created_at` |
| `POST /` | Create provider; 409 on duplicate name |
| `PATCH /{id}` | Update any fields; `exclude_none=True` so omitted fields are not cleared |
| `DELETE /{id}` | Hard delete |
| `GET /{id}/models` | Calls provider's `/v1/models` endpoint via `openai.OpenAI` with 5-second timeout; returns `{"models": []}` on any error (provider offline, auth failure) — never raises |

**Model**: `backend/models/openai_provider.py` — `OpenAIProvider` table. Fields: `id` (UUID), `name` (unique), `base_url`, `api_key` (stored plaintext), `default_model`, `max_image_px` (128–4096, default 1024 — image is JPEG-encoded at this resolution before sending), `max_tokens` (64–32768, default 2048), `created_at`.

**Schema**: `OpenAIProviderOut` masks the API key (last 4 chars visible) and adds a computed `is_remote: bool` — true when the base URL hostname is not `localhost`/`127.0.0.1`/`::1`. Remote providers show a warning banner in the CaptioningPage and Settings form.

**Captioner**: `ml/openai_compat_captioner.py` — `caption_image(image_path, base_url, api_key, model_name, style, custom_prompt, max_px, max_tokens, target_w, target_h)`. Encodes the image as JPEG base64 (after `preprocess_for_caption` and optional `max_px` downscale), sends via `openai.ChatCompletion` with a `image_url` content block. 120-second per-image timeout. Not tracked by `model_manager`.

**Model ID format** in captioning: `openai_compat:{provider_id}:{model_name}`. The router splits on `:` with `maxsplit=2` to recover `provider_id` and `model_name`. If `model_name` is empty, `openai_provider.default_model` is used.

**Settings UI**: Settings page → "LLM Providers" tab. Add/edit/delete providers. The "Default model" field uses `ModelPicker` (preset dropdown for well-known cloud APIs; fetch button for local servers). Provider mutations also invalidate `["captioning-models"]` so CaptioningPage model list updates immediately.

**`ModelPicker` component** (`frontend/src/components/providers/ModelPicker.tsx`): Props `{ value, onChange, providerId?, baseUrl?, placeholder? }`. On mount with `providerId`, auto-fetches models via `providersApi.fetchModels(providerId)`. Computes `presets = getPresetsForUrl(baseUrl)` from `providerPresets.ts`. When both are empty: plain text input. When either has entries: `<select>` with a "Custom…" sentinel + optional text input below for custom entry. When `value` is not in the list, the select shows "Custom…" and the text input is pre-filled. Explicitly selecting "Custom…" sets local `showCustom: bool` state so the text input appears even when the current value is a known model; selecting any list item resets it. A `useEffect` on `[value, allModels]` also resets `showCustom` whenever `value` becomes a known model (e.g. parent switches providers and passes a new default), preventing the select from staying stuck on "Custom…" after an external value change. A refresh button (↻) allows manual re-fetch. `fetchError` shows a muted "Could not reach provider" message on empty result.
