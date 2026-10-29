# LLM providers

This file covers OpenAI-compatible provider configuration: the `/providers` CRUD router, the `OpenAIProvider` row and its masked schema, the `ml/openai_compat_captioner.py` vision client and its `openai_compat:{provider_id}:{model_name}` model id, the Settings → LLM Providers tab and the `ModelPicker` component. Providers have two consumers — the `openai_compat:` branch of the caption dispatch chain, whose failure reporting (timeouts, empty replies) is in `docs/dev/captioning.md`, and the ComfyUI prompt generator in `docs/dev/comfy-prompts.md`. The user-facing setup is `docs/settings.md` § LLM Providers.

## Router

`backend/routers/providers.py`, prefix `/providers`. Registered in `main.py`. No service layer — CRUD is thin enough to live in the router.

| Endpoint | Behaviour |
|---|---|
| `GET /` | List all providers ordered by `created_at` |
| `POST /` | Create provider; 409 on duplicate name. Builds the row as `OpenAIProvider(**body.model_dump())` — the old explicit field list silently dropped `max_tokens`, and would have dropped `timeout_s` too |
| `PATCH /{id}` | Update any fields; `exclude_none=True` so omitted fields are not cleared |
| `DELETE /{id}` | Hard delete |
| `GET /{id}/models` | Calls provider's `/v1/models` endpoint via `openai.OpenAI` with a 5-second timeout and `max_retries=0` (without it the never-raises probe spends 15 s); returns `{"models": []}` on any error (provider offline, auth failure) — never raises |

## Model

`backend/models/openai_provider.py` — `OpenAIProvider` table. Fields: `id` (UUID), `name` (unique), `base_url`, `api_key` (stored plaintext), `default_model`, `max_image_px` (`ge=128`, `le=2**31 - 1` — default 1024; image is JPEG-encoded at this resolution before sending, and the ceiling is the SQLite integer bind's rather than a resolution limit, since `_encode_image` only downscales), `max_tokens` (`ge=1`, `le=2**31 - 1` — default 2048; the ceiling is the SQLite integer bind's, not a model limit, and exists so an absurd value is a 422 rather than an `OverflowError` out of the commit), `timeout_s` (10–3600 s, default 300), `created_at`.

## Schema

`OpenAIProviderOut.from_orm_row` is the remaining hand-maintained field list (the create path was fixed by the `**model_dump()` splat above) — a column added to the model and the schema but forgotten there fails *silently*, leaving the field at its default. `OpenAIProviderOut` masks the API key (last 4 chars visible) and adds a computed `is_remote: bool` — true when the base URL hostname is not `localhost`/`127.0.0.1`/`::1`. Remote providers show a warning banner in the CaptioningPage and Settings form.

## Captioner

`ml/openai_compat_captioner.py` — `caption_image(image_path, base_url, api_key, model_name, style, custom_prompt, max_px, max_tokens, target_w, target_h, timeout_s)`. Encodes the image as JPEG base64 (after `preprocess_for_caption` and optional `max_px` downscale), sends via the v1 SDK (`OpenAI(base_url=…).chat.completions.create`) with an `image_url` content block. The per-image timeout is the provider's `timeout_s`; `_caption_sync` takes it as its **last** positional parameter, and the `run_in_executor` call is positional, so both signatures append new parameters at the end. Not tracked by `model_manager`. `_caption_sync` binds `resp.choices[0]` and **inspects `finish_reason`** before returning: an empty stripped content logs a warning naming the model, the `finish_reason` and whether a `reasoning_content` field was present (a provider extension the SDK carries as an extra field, hence `getattr`), pointing at Max tokens — LM Studio returns exactly that shape when a reasoning model spends its whole budget thinking; non-empty content with `finish_reason == "length"` logs that the caption is truncated. Both are logs only — it still returns `""`, and the router owns the failure (see `docs/dev/captioning.md` § Captioning job execution, the empty-caption bullet).

## Model ID format in captioning

`openai_compat:{provider_id}:{model_name}`. The router splits on `:` with `maxsplit=2` to recover `provider_id` and `model_name`. If `model_name` is empty, `openai_provider.default_model` is used.

## Settings UI

Settings page → "LLM Providers" tab. Add/edit/delete providers. `Max tokens` and `Timeout` are `NumberField`s, not sliders: a `type="range"` cannot express "no upper bound", and its `max={16384}` *was* the ceiling users hit. Each field's bounds live in the `clamp` callback it passes — including `max_tokens`' `2 ** 31 - 1`, which mirrors the schema's `le` rather than adding a `max` attribute. The "Default model" field uses `ModelPicker` (preset dropdown for well-known cloud APIs; fetch button for local servers). Provider mutations also invalidate `["captioning-models"]` so CaptioningPage model list updates immediately.

## `ModelPicker` component

`frontend/src/components/providers/ModelPicker.tsx`: Props `{ value, onChange, providerId?, baseUrl?, placeholder? }`. On mount with `providerId`, auto-fetches models via `providersApi.fetchModels(providerId)`. Computes `presets = getPresetsForUrl(baseUrl)` from `providerPresets.ts`. When both are empty: plain text input. When either has entries: `<select>` with a "Custom…" sentinel + optional text input below for custom entry. When `value` is not in the list, the select shows "Custom…" and the text input is pre-filled. Explicitly selecting "Custom…" sets local `showCustom: bool` state so the text input appears even when the current value is a known model; selecting any list item resets it. A `useEffect` on `[value, allModels]` also resets `showCustom` whenever `value` becomes a known model (e.g. parent switches providers and passes a new default), preventing the select from staying stuck on "Custom…" after an external value change. A refresh button (↻) allows manual re-fetch. `fetchError` shows a muted "Could not reach provider" message on empty result.
