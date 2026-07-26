# Settings page

This file covers `SettingsPage` and the `/settings` router: the `ThresholdSettings` singleton row, and every tab (Gallery, Captioning, UI Behavior, Quality Thresholds, Versioning, LLM Providers, ComfyUI).


`frontend/src/pages/SettingsPage.tsx`, route `/settings`, sidebar nav item "Settings". Exposes all eight scoring thresholds — the six quality-flag thresholds plus the Grounding DINO box-confidence and SAM 3 confidence thresholds — as editable number inputs.

**Backend**: `backend/routers/settings.py`, prefix `/settings`. Two endpoints:

| Endpoint | Behaviour |
|---|---|
| `GET /thresholds` | Returns current thresholds from the `threshold_settings` singleton row (id=1); if the row doesn't exist yet, returns in-memory defaults from `DEFAULTS` in `threshold_service.py` without writing anything |
| `PATCH /thresholds` | Creates the row on first save (upsert on id=1), updates only the fields present in the body, commits |

**Model**: `backend/models/threshold_settings.py` — `ThresholdSettings` table with a single row (`id=1`). Holds the quality-threshold `Float` columns (with `server_default` matching the constants in `technical_scorer.py`), the `versioning_mode` string, and the `auto_rescan_on_open` boolean (`server_default="0"`). It is the catch-all single-row table for app-wide server-side settings — add new global toggles here and to `ThresholdsOut`/`ThresholdsUpdate` in `routers/settings.py`. Defaults are canonically defined in `backend/services/threshold_service.py::DEFAULTS`.

**Frontend**: `useQuery({ queryKey: ["settings", "thresholds"], staleTime: 60_000 })` — shared key with `StatsPage` so both components see the same cached value. Save button is enabled only when at least one field differs from the loaded values (`isChanged`). Save sends only the changed fields via `PATCH`. "Reset to defaults" restores the local form state to the `DEFAULTS` constant without an API call.

The Settings page uses a **tab-based layout** with seven tabs. All localStorage-backed preferences take effect immediately (no Save button); the quality thresholds, versioning mode, and both ComfyUI fields require an explicit Save. The **ComfyUI tab** holds `comfyui_url` (with a *Test connection* button → `comfyApi.ping`) and `comfy_workflow_dir` (with a `DirPickerModal` Browse), both server-side `ThresholdSettings` columns — see `docs/dev/comfyui.md`.

**Gallery tab** — five immediate-save preferences:
- Images per page (`25 | 50 | 100 | 200`). Stored under `GALLERY_PAGE_SIZE_KEY`. Read by `GalleryPage` (gallery list limit) and `ImageDetailPage` (end-of-page detection + prefetch limit for cross-page arrow-key navigation). Parse and default via `getGalleryPageSize()`.
- Selection checkbox size (px slider, 14–32, live `GalleryCheckbox` preview). Stored under `GALLERY_CHECKBOX_SIZE_KEY` but owned by `uiPrefsStore`, not read directly — see `docs/dev/persistence.md` § `constants/storage.ts` — the key registry.
- License badge on cards (`true | false`, off by default). Stored under `GALLERY_LICENSE_BADGE_KEY` but owned by `uiPrefsStore.galleryLicenseBadge` — `ImageCard` subscribes to the store, so a Settings pane and a gallery pane side by side stay in step. Its own group, deliberately **not** inside **Gallery defaults**: that group's copy promises first-open-only application and clearing via the gallery reset button, and neither is true of a global display toggle. When on, **every** card gets a badge — an image with no license anywhere shows the muted "No license" descriptor, which is the state the preference exists to surface. See `docs/dev/provenance.md`.
- Subfolder rename on move (`on | off`). Stored under `SUBFOLDER_RENAME_KEY`. Read by `SelectionToolbar`'s `moveSubfolderMutation` at mutation time; passed as `rename_on_move` to `POST /images/batch/move-subfolder`.
- **Gallery defaults** section: default sort (`GALLERY_DEFAULT_SORT_KEY`, index into `SORT_OPTIONS`), default caption filter (`GALLERY_DEFAULT_CAPTION_KEY`, `"all" | "captioned" | "uncaptioned"`), default quality filter (`GALLERY_DEFAULT_QUALITY_KEY`, flag key or `""`). Applied the first time you open a dataset's gallery, before any filter choices have been remembered for it. Once visited, per-dataset `gallery-state-*` state (persisted to `localStorage`) takes precedence — use the Reset filters button in the gallery toolbar to clear it and fall back to these defaults again. Helpers `getGalleryDefaultSort()`, `getGalleryDefaultCaptionFilter()`, `getGalleryDefaultQualityFilter()` in `storage.ts` are used at `useState` init time.

Constants defined in `docs/dev/frontend-core.md` § Frontend constants and `docs/dev/persistence.md`.

**Captioning tab** — seven immediate-save preferences (lazy-loads `["captioning-models"]` query only when tab is first opened). These are **first-run fallbacks**: they apply the first time you visit the Captioning page, or after clearing the remembered workflow configuration. Once the page has been used, the model, style, scope, and other settings are remembered automatically via `CAPTIONING_WORKFLOW_KEY` (see Persistent page state in `docs/dev/persistence.md`) and take precedence over these defaults.
- Default model (`CAPTION_DEFAULT_MODEL_KEY`). Applied if no workflow blob exists yet and `selectedModel` is `""`. If the remembered workflow model is not installed (uninstalled model / removed provider), the model-validation `useEffect` clears it and falls back to this key. Also corrects the remembered style if it is incompatible with the applied model.
- Default style (`CAPTION_DEFAULT_STYLE_KEY`, e.g. `"detailed" | "short" | "tags" | "promptgen" | "booru"`).
- Default scope (`CAPTION_DEFAULT_SCOPE_KEY`, `"uncaptioned" | "all"`).
- Default delimiter mode (`CAPTION_DEFAULT_DELIMITER_KEY`, `"overwrite" | "append" | "prepend"`).
- Strip refusals toggle (`CAPTION_DEFAULT_STRIP_REFS_KEY`, default `true`).
- Rename on caption toggle (`CAPTION_DEFAULT_RENAME_KEY`, default `false`).
- Save backup toggle (`CAPTION_DEFAULT_SAVE_BACKUP_KEY`, default `false`).
- **Reset remembered Captioning configuration** ghost button at the bottom of this section: calls `clearPersisted(CAPTIONING_WORKFLOW_KEY)` (global workflow only — per-dataset filter blobs are cleared from the on-page button per dataset). `toast.success` only, no confirm dialog.

**UI Behavior tab** — immediate-save preferences:
- Delete-confirmation default button (`cancel` / `confirm`). Stored under `CONFIRM_DEFAULT_KEY`. Read by `ConfirmDialog` on every mount when `danger=true` and no `defaultFocus` prop is provided.
- Branch snapshot behavior (`ask` / `auto`). Stored under `BRANCH_SNAPSHOT_KEY`. When `"ask"`, `BranchSelector` shows an inline prompt before checkout or branch creation letting the user choose whether to create a snapshot. When `"auto"`, snapshots are always created without prompting.
- **Auto-rescan dataset on open** (`auto_rescan_on_open`, default off). Unlike the two above, this is a *server-side* setting persisted on the `ThresholdSettings` row (not localStorage), but the toggle still saves immediately via `mutation.mutate({ auto_rescan_on_open })` rather than the page-level Save button. When on, opening a dataset gallery fires `POST /datasets/{id}/rescan` once per dataset open (`GalleryPage`, gated by the `settingsApi.getThresholds` query). See `docs/dev/image-files.md` § Importing captions & folder rescan.

**Quality Thresholds tab** — eight editable number inputs from the `FIELDS` array: blur, noise, uniformity, duplicate, watermark, NSFW, DINO box confidence (`gdino_threshold`), and SAM 3 confidence (`sam3_threshold`). Requires Save; the flag thresholds apply to the next scoring run, `gdino_threshold` to the next SAM2 detection run, `sam3_threshold` to the next SAM3 run.

**Versioning tab** — version control mode radio (`off | manual | auto`) plus branch snapshot behavior radio. Requires Save for the version control mode; branch snapshot behavior is immediate (localStorage).

**LLM Providers tab** — manage OpenAI-compatible provider configurations (see `docs/dev/captioning.md` § OpenAI-compatible providers). Add / edit / delete providers. Name and Base URL are required; changes are saved immediately per-mutation (no page-level Save). Provider mutations also invalidate `["captioning-models"]` so the model picker on CaptioningPage reflects changes immediately.

