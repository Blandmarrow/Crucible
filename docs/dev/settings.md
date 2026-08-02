# Settings page

This file covers `SettingsPage` and the `/settings` router: the `ThresholdSettings` singleton row, and every tab (Gallery, Captioning, UI Behavior, Quality Thresholds, Versioning, LLM Providers, ComfyUI, API Keys).


`frontend/src/pages/SettingsPage.tsx`, route `/settings`, sidebar nav item "Settings". Exposes all eight scoring thresholds — the six quality-flag thresholds plus the Grounding DINO box-confidence and SAM 3 confidence thresholds — as editable number inputs.

## Backend

`backend/routers/settings.py`, prefix `/settings`. Two endpoint pairs — thresholds, and the secrets pair documented under § API Keys tab below:

| Endpoint | Behaviour |
|---|---|
| `GET /thresholds` | Returns current thresholds from the `threshold_settings` singleton row (id=1); if the row doesn't exist yet, returns in-memory defaults from `DEFAULTS` in `threshold_service.py` without writing anything |
| `PATCH /thresholds` | Creates the row on first save (upsert on id=1), updates only the fields present in the body, commits |

## Model

`backend/models/threshold_settings.py` — `ThresholdSettings` table with a single row (`id=1`). Holds the quality-threshold `Float` columns (with `server_default` matching the constants in `technical_scorer.py`), the `versioning_mode` string, the `auto_rescan_on_open` boolean (`server_default="0"`), and the three secret `String` columns. It is the catch-all single-row table for app-wide server-side settings — add new global toggles here and to `ThresholdsOut`/`ThresholdsUpdate` in `routers/settings.py`. **A new *secret* is the exception**: it goes on this same row but into `SecretsOut`/`SecretsUpdate` and the `/secrets` pair instead, for the reasons under § API Keys tab. Defaults are canonically defined in `backend/services/threshold_service.py::DEFAULTS`.

## Frontend

`useQuery({ queryKey: ["settings", "thresholds"], staleTime: 60_000 })` — shared key with `StatsPage` so both components see the same cached value. Save button is enabled only when at least one field differs from the loaded values (`isChanged`). Save sends only the changed fields via `PATCH`. "Reset to defaults" restores the local form state to the `DEFAULTS` constant without an API call.

The Settings page uses a **tab-based layout** with eight tabs. All localStorage-backed preferences take effect immediately (no Save button); the quality thresholds, versioning mode, and both ComfyUI fields require an explicit Save. The **ComfyUI tab** holds `comfyui_url` (with a *Test connection* button → `comfyApi.ping`) and `comfy_workflow_dir` (with a `DirPickerModal` Browse), both server-side `ThresholdSettings` columns — see `docs/dev/comfyui.md`.

## Gallery tab

Five immediate-save preferences:
- Images per page (`25 | 50 | 100 | 200`). Stored under `GALLERY_PAGE_SIZE_KEY`. Read by `GalleryPage` (gallery list limit) and `ImageDetailPage` (end-of-page detection + prefetch limit for cross-page arrow-key navigation). Parse and default via `getGalleryPageSize()`.
- Selection checkbox size (px slider, 14–32, live `GalleryCheckbox` preview). Stored under `GALLERY_CHECKBOX_SIZE_KEY` but owned by `uiPrefsStore`, not read directly — see `docs/dev/persistence.md` § `constants/storage.ts` — the key registry.
- License badge on cards (`true | false`, off by default). Stored under `GALLERY_LICENSE_BADGE_KEY` but owned by `uiPrefsStore.galleryLicenseBadge` — `ImageCard` subscribes to the store, so a Settings pane and a gallery pane side by side stay in step. Its own group, deliberately **not** inside **Gallery defaults**: that group's copy promises first-open-only application and clearing via the gallery reset button, and neither is true of a global display toggle. When on, **every** card gets a badge — an image with no license anywhere shows the muted "No license" descriptor, which is the state the preference exists to surface. See `docs/dev/provenance.md`.
- Subfolder rename on move (`on | off`). Stored under `SUBFOLDER_RENAME_KEY`. Read by `SelectionToolbar`'s `moveSubfolderMutation` at mutation time; passed as `rename_on_move` to `POST /images/batch/move-subfolder`.
- **Gallery defaults** section: default sort (`GALLERY_DEFAULT_SORT_KEY`, index into `SORT_OPTIONS`), default caption filter (`GALLERY_DEFAULT_CAPTION_KEY`, `"all" | "captioned" | "uncaptioned"`), default quality filter (`GALLERY_DEFAULT_QUALITY_KEY`, flag key or `""`). Applied the first time you open a dataset's gallery, before any filter choices have been remembered for it. Once visited, per-dataset `gallery-state-*` state (persisted to `localStorage`) takes precedence — use the Reset filters button in the gallery toolbar to clear it and fall back to these defaults again. A third tier outranks both: a gallery **deep link** (`?subfolder=` / `?source_video_id=`, or the equivalent pane view) is applied once per change during render and overrides the restored subfolder and lineage filter, resetting the page to 1. It never touches the sort or the caption/quality filters, so the precedence above still holds for all three of these defaults. See `docs/dev/gallery.md` and PM-012. Helpers `getGalleryDefaultSort()`, `getGalleryDefaultCaptionFilter()`, `getGalleryDefaultQualityFilter()` in `storage.ts` are used at `useState` init time.

Constants defined in `docs/dev/frontend-core.md` § Frontend constants and `docs/dev/persistence.md`.

## Captioning tab

Seven immediate-save preferences (lazy-loads `["captioning-models"]` query only when tab is first opened). These are **first-run fallbacks**: they apply the first time you visit the Captioning page, or after clearing the remembered workflow configuration. Once the page has been used, the model, style, scope, and other settings are remembered automatically via `CAPTIONING_WORKFLOW_KEY` (see Persistent page state in `docs/dev/persistence.md`) and take precedence over these defaults.
- Default model (`CAPTION_DEFAULT_MODEL_KEY`). Applied if no workflow blob exists yet and `selectedModel` is `""`. If the remembered workflow model is not installed (uninstalled model / removed provider), the model-validation `useEffect` clears it and falls back to this key. Also corrects the remembered style if it is incompatible with the applied model.
- Default style (`CAPTION_DEFAULT_STYLE_KEY`, e.g. `"detailed" | "short" | "tags" | "promptgen" | "booru"`).
- Default scope (`CAPTION_DEFAULT_SCOPE_KEY`, `"uncaptioned" | "all"`).
- Default delimiter mode (`CAPTION_DEFAULT_DELIMITER_KEY`, `"overwrite" | "append" | "prepend"`).
- Strip refusals toggle (`CAPTION_DEFAULT_STRIP_REFS_KEY`, default `true`).
- Rename on caption toggle (`CAPTION_DEFAULT_RENAME_KEY`, default `false`).
- Save backup toggle (`CAPTION_DEFAULT_SAVE_BACKUP_KEY`, default `false`).
- **Reset remembered Captioning configuration** ghost button at the bottom of this section: clears the global workflow blob **and every per-dataset `captioning-filters-*` blob**, the latter found by scanning `localStorage` for the prefix rather than by enumerating dataset ids — so the one button forgets the Captioning page's remembered setup everywhere, not only globally. `toast.success` only, no confirm dialog.

## UI Behavior tab

Immediate-save preferences:
- Delete-confirmation default button (`cancel` / `confirm`). Stored under `CONFIRM_DEFAULT_KEY`. Read by `ConfirmDialog` on every mount when `danger=true` and no `defaultFocus` prop is provided.
- Branch snapshot behavior (`ask` / `auto`). Stored under `BRANCH_SNAPSHOT_KEY`. When `"ask"`, `BranchSelector` shows an inline prompt before checkout or branch creation letting the user choose whether to create a snapshot. When `"auto"`, snapshots are always created without prompting.
- **Auto-rescan dataset on open** (`auto_rescan_on_open`, default off). Unlike the two above, this is a *server-side* setting persisted on the `ThresholdSettings` row (not localStorage), but the toggle still saves immediately via `mutation.mutate({ auto_rescan_on_open })` rather than the page-level Save button. When on, opening a dataset gallery fires `POST /datasets/{id}/rescan` once per dataset open (`GalleryPage`, gated by the `settingsApi.getThresholds` query). See `docs/dev/image-files.md` § Importing captions & folder rescan.

## Quality Thresholds tab

Eight editable number inputs from the `FIELDS` array: blur, noise, uniformity, duplicate, watermark, NSFW, DINO box confidence (`gdino_threshold`), and SAM 3 confidence (`sam3_threshold`). Requires Save; the flag thresholds apply to the next scoring run, `gdino_threshold` to the next SAM2 detection run, `sam3_threshold` to the next SAM3 run.

## Versioning tab

Version control mode radio (`off | manual | auto`) plus branch snapshot behavior radio. Requires Save for the version control mode; branch snapshot behavior is immediate (localStorage).

## LLM Providers tab

Manage OpenAI-compatible provider configurations (see `docs/dev/captioning.md` § OpenAI-compatible providers). Add / edit / delete providers. Name and Base URL are required; changes are saved immediately per-mutation (no page-level Save). Provider mutations also invalidate `["captioning-models"]` so the model picker on CaptioningPage reflects changes immediately.

## API Keys tab

Three secrets — `hf_token`, `gelbooru_api_key`, `gelbooru_user_id` — that were previously readable only from `.env`/OS env, resolved once at import into the `backend.config.settings` singleton. They are now `String(500)` columns on the same `ThresholdSettings` row (migration `a2f4c6e8b0d1`, whose docstring records the three decisions below in full), editable from the page with no restart.

**Precedence: the DB wins when non-empty, otherwise the `.env`/OS-env chain.** Deliberately not 12-factor. The reasoning is a failure mode rather than a principle: a token typed into a field and silently overridden by an env var invisible from the UI is the worst outcome available, while the reverse is visible — each row states its own source. **Empty means inherit, not "no secret"**: `""` is the single not-set sentinel in both stores, so a cleared field and an absent row resolve identically. Values are **stored unencrypted**, like the LLM provider keys, and the tab says so in one line.

**`backend/services/secrets_service.py`** is the resolver, kept out of the 25-line `threshold_service.py` because it mutates process state:

| Function | Role |
|---|---|
| `resolve_secret(row, field)` | The effective value. `row=None` is legal and resolves purely to `settings` |
| `secret_source(row, field)` | `"db"` \| `"env"` \| `"unset"` |
| `sync_env(row)` | The **only** writer of `os.environ["HF_TOKEN"]` |
| `sync_env_from_db(session)` | `sync_env` of `get_thresholds(session)` |

One `field` string indexes both stores, which is why the column names **must** equal the `config.py` field names (`watermark_threshold` is the existing precedent for a name living in both). All three are in `threshold_service.DEFAULTS` as `""`, because `get_thresholds` builds a *transient* `ThresholdSettings(**DEFAULTS)` when no row exists and an unset attribute on a transient object reads `None`.

**Why a runtime env projection exists.** All **ten** HuggingFace-hub loaders pass no `token=` and rely on the ambient `HF_TOKEN`: `aesthetic_scorer`, `wd14_tagger`, `sam2_predictor`'s *two* (GroundingDINO and SAM2), and model_manager's Florence-2, PaliGemma-2, LLaVA, DINOv2, NSFW and tag-embedder loaders. The tag embedder's MiniLM repo is public and needs no token, but it reads the same variable, so the projection covers it; SAM3 is *not* in the list — it loads a local safetensors checkpoint, never the hub (see `docs/dev/detection-inference.md`). Every one of the ten is sync and runs in an executor thread, so none can await an `AsyncSession` — the env var is the only carrier that reaches any loader. `huggingface_hub` re-reads `os.environ` on every call and caches nothing, so a mid-process assignment needs no restart. Nothing may ever assign to `settings.*`: it is the restore target when an override is cleared, and its immutability is a chosen invariant, not a type (`config.py` has no `frozen=True`, and the suite monkeypatches it).

`sync_env` is called from exactly three places: **import time** in `main.py` with `row=None`, positioned between the config and router imports so `.env` behaviour holds in contexts that never run the lifespan (notably `conftest.api_env`); the **lifespan**, right after `await init_db()`, awaited and wrapped so a failure never fails startup — without it a saved token is silently forgotten on every restart; and **`PATCH /secrets`** after the commit, unconditionally even when only a gelbooru field changed. Clearing pops the variable rather than assigning `""`, since popping is what re-exposes `HUGGING_FACE_HUB_TOKEN` and `~/.cache/huggingface/token`. The pop branch cannot remove a token the OS set — if it had one, pydantic read it into `settings.hf_token` and the assignment branch fires instead.

**Endpoints** (schemas inline in the router, per that file's convention):

| Endpoint | Behaviour |
|---|---|
| `GET /secrets` | `SecretsOut` — one nested `{masked, source}` per secret, built from `get_thresholds` |
| `PATCH /secrets` | Same get-or-create as `update_thresholds`, `exclude_none` setattr loop, commit, then `sync_env(row)` |

`SecretsUpdate` strips whitespace on every field (pasted tokens carry trailing newlines), so whitespace-only means clear. **The nested read shape is the mask-echo defence**: a flat `hf_token: str | None` cannot tell a mask from a token that happens to be asterisks, so `PATCH(GET().json())` would silently save `****abcd`. Nested, that call is a **422** — a runtime property, asserted in `backend/tests/test_settings_secrets.py`, which is where a future flattening must fail. The masked value is the **effective** one, so the UI can show `****abcd` beside "inherited from `.env`"; that newly exposes four characters of an `.env`-only token, accepted deliberately on this unauthenticated LAN surface where `GET /filesystem/list` already reads `.env` outright. The mask formula is `schemas.mask_secret`, shared with `OpenAIProviderOut.api_key_masked`.

**A rejected value is not echoed either.** The masking above governs what a *successful* response carries; a 422 is the other half, and it used to leak. FastAPI's default validation handler returns pydantic's error entries verbatim, `input` included — so a token over the 500-character column came back in the error body. `main.py`'s app-level `RequestValidationError` handler strips `input`/`ctx` from every entry, leaving `loc` and `msg`; `test_settings_secrets.py::test_rejected_value_is_not_echoed_in_the_422` pins it. The handler is app-level rather than a validator here, because `OpenAIProviderCreate.api_key` had the identical exposure — see `docs/dev/backend-infrastructure.md` § App-level exception handlers.

Folding these into `/thresholds` was rejected beyond the caching argument: `GET /thresholds` returns the ORM row under `from_attributes`, so a derived `hf_token_masked` field would look for a nonexistent row attribute and fail validation, and that PATCH's blind setattr loop is the wrong tool for a value with a process-level side effect. `ThresholdsOut` needs no change — `from_attributes` ignores the three new ORM attributes.

**Frontend** — `settingsApi.getSecrets`/`updateSecrets` under query key `["settings", "secrets"]`, never `["settings","thresholds"]` (cached by `StatsPage`, `QualityPage`, `GalleryPage`, `DatasetsPage`, `VersionsPage` and `WorkflowScanModal`), gated `enabled: activeTab === "secrets"` on the `["captioning-models"]` lazy-tab precedent, so secrets never go over the wire for anyone who does not open the tab. `onSuccess` invalidates rather than writing the response into the cache — a client-side mask-echo guard. `SecretsUpdate` is deliberately **not** `Partial<Secrets>`, so `updateSecrets(secrets)` is a compile error caught by `npm run build`. `frontend/src/components/settings/SecretField.tsx` renders one row: local `draft` state, `type="password"`, a status line naming the source, and separate **Save** / **Clear** buttons. They are never one control — blank means "keep the current value" everywhere else in this app, so Save is disabled on an empty draft and Clear sends an explicit `""`. No confirm dialog (it drops an override, it destroys nothing), but the toast is derived from the *response*, the only thing that knows whether clearing revealed an inherited value or nothing at all.

Three details of that row are load-bearing rather than cosmetic. The input is associated with its label by `useId()` + `htmlFor` (the `ProvenanceFields` pattern — see `docs/dev/shared-utilities.md`) and the root carries `role="group"` + `aria-label={label}`, so `frontend/e2e/settings.spec.ts` can scope to one row by accessible name; all three rows carry a "Save" button with the same name, and the alternative is walking parent nodes. `autoComplete` is `"new-password"`, not `"off"` — Chrome ignores `off` on a password input and offers to save the key to the browser's password manager. And **busy is per row**: one `useMutation` drives all three, so a bare `isPending` greys out the two rows the user did not touch. `SettingsPage` derives the in-flight field from `secretsMutation.variables` with the same `Object.keys(body)[0]` idiom `onSuccess` uses — every call sends exactly one key, and no new state is needed.

Gelbooru's two credentials have no env projection — `routers/booru.py` resolves them per request via `resolve_secret`; see `docs/dev/workspace.md` § Booru tag lookup.

