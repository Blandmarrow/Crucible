# Captioning — caption jobs, the caption editor and the dictionary

Captioning writes an image's caption two ways: as a job over many images, from
`CaptioningPage` through `/captioning/run` and `/captioning/pipeline`, and one image at a
time in `ImageDetailPage`'s caption panel, where the AI section runs a single-image job and
the helpers edit only the unsaved draft. This file is the
family's entry point: the tables below route from a code path to whichever file in
`docs/dev/captioning/` owns it. The `openai_compat:` provider rows and their client are
`docs/dev/llm-providers.md`; where a model's `kind` is declared, and the WD14 tagger's own cache,
are `docs/dev/model-registry.md`; caption sidecars and the tokenizer are indexed in
`docs/dev/shared-utilities.md`; caption bulk edit is `docs/dev/bulk-ops.md`; the rest of the
detail page is `docs/dev/image-detail.md`.

## The family

| File | Covers |
|---|---|
| [jobs.md](captioning/jobs.md) | A caption job: the post-processing and merge fields on `CaptionJobRequest` and `PipelineStep` (delimiter modes, refusal stripping, rename-on-caption), `{previous_caption}`, requests in flight (`InOrderCalls`, Max concurrent requests), job queuing, which job `CaptioningPage`'s progress panel shows, sidecar writes off the event loop |
| [failures.md](captioning/failures.md) | How a job reports a failure: `failure_summary` and `result_data`, `_failure_headline`, what a failed or cancelled job counts in `done_items`, the empty-caption guard, HTTP 429, the Ollama timeout |
| [model-picker.md](captioning/model-picker.md) | Which models a picker offers and a request accepts: the registry's `kind`/`description`, `GET /captioning/models`, `_caption_backend`'s 422 (the silent no-op), `captionBackend` and `takesPrompt`, why `modelType` is the wrong gate |
| [editor.md](captioning/editor.md) | `ImageDetailPage`'s caption panel: the token counter, the image-keyed draft, Generate with AI and Fix mode, the Other captions panel (`similar-captions`), Check tags (`/booru/check`) and the synonym card — `canTakeTags`, `replaceTag`, `replaceSpan` |
| [dictionary.md](captioning/dictionary.md) | The offline WordNet synonym dictionary (#52): the install and why it skips the job queue, the `/dictionary` router, the archive guards, the schema, the morphy lookup |

## Where things live

Each row names the file that **owns** that code. A `+` clause is a second genuine owner,
not a see-also, and says which part of the code it owns.

### Backend

| Editing… | Read |
|---|---|
| `backend/routers/captioning.py` | `docs/dev/captioning/jobs.md`; + `docs/dev/captioning/failures.md` for the two loop tails and `_failure_headline`; + `docs/dev/captioning/model-picker.md` for `GET /captioning/models` and `_caption_backend` |
| `backend/services/caption_service.py` | `docs/dev/captioning/jobs.md` |
| `backend/utils.py` (`InOrderCalls`) | `docs/dev/captioning/jobs.md` § Requests in flight |
| `backend/ml/openai_compat_captioner.py` | `docs/dev/llm-providers.md`; + `docs/dev/captioning/failures.md` for timeouts, HTTP 429 and empty replies |
| `backend/ml/ollama_captioner.py` | `docs/dev/captioning/failures.md` § Ollama timeout |
| `backend/ml/wd14_tagger.py` | `docs/dev/captioning/model-picker.md` for `_VARIANTS`' descriptions; + `docs/dev/model-registry.md` for its cache |
| `backend/routers/images.py` (`similar-captions`), `backend/services/similar_captions_service.py` | `docs/dev/captioning/editor.md` § Other captions panel |
| `backend/services/booru_service.py` (`check_tags`) | `docs/dev/captioning/editor.md` § Check tags; + `docs/dev/workspace.md` for the router and the Danbooru client |
| `backend/routers/dictionary.py`, `backend/services/dictionary_service.py` | `docs/dev/captioning/dictionary.md` |

### Frontend

| Editing… | Read |
|---|---|
| `frontend/src/pages/CaptioningPage.tsx` | `docs/dev/captioning/jobs.md` for the scope rules and the progress panel; + `docs/dev/captioning/model-picker.md` for its picker |
| `frontend/src/constants/captionModels.ts` | `docs/dev/captioning/model-picker.md`; + `docs/dev/captioning/jobs.md` for `withPreviousCaption`; + `docs/dev/captioning/editor.md` for `buildFixPrompt` |
| `frontend/src/pages/ImageDetailPage.tsx` (its caption panel) | `docs/dev/captioning/editor.md`; + `docs/dev/captioning/model-picker.md` for the AI section's picker |
| `frontend/src/components/caption/OtherCaptionsPanel.tsx`, `CheckTagsPanel.tsx`, `SynonymCard.tsx` | `docs/dev/captioning/editor.md`, the section named after the component |
| `frontend/src/utils/captionTags.ts`, `frontend/src/utils/captionWords.ts` | `docs/dev/captioning/editor.md`; + `docs/dev/captioning/dictionary.md` for the word limits `selectedWord` shares with the lookup |
| `frontend/src/utils/tokenCount.ts` | `docs/dev/captioning/editor.md` § ImageDetailPage caption panel |
