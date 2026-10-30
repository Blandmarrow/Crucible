# Frontend persistence: storage keys, blobs & the persistent page state pattern

This file covers where saved state lives: the data stored on the server, then everything the frontend stores in `localStorage`/`sessionStorage`: the key registry in `frontend/src/constants/storage.ts`, the `loadPersisted`/`useDebouncedPersist` helpers, the three persistence shapes that exist and why two of them are deliberately off the hook, and the workflow/filters split every configurable page follows. The stores that read these keys live in `docs/dev/frontend-core.md` (§ Frontend state).

### Data stored on the server

User-written data that must follow the install rather than the browser lives in the database, behind `backend/routers/app_data.py` (prefix `/app-data`) and two routes in `backend/routers/comfy.py`. The ruling is in `docs/dev/decisions/03-app-stored-prompts.md`, and the migration from browser storage is planned in `docs/dev/server-state-plan/README.md`, which also says how far it has got.

| Data | Storage | Routes |
|---|---|---|
| Caption prompt presets | `caption_prompt_presets` | `GET`/`POST /app-data/presets`, `DELETE /app-data/presets/{id}` |
| Declared empty categories | `declared_categories` | `GET`/`POST`/`DELETE /app-data/categories`, `POST /app-data/categories/rename` |
| Captioning workflow | `ThresholdSettings.captioning_workflow` | `GET`/`PUT`/`DELETE /app-data/captioning-workflow` |
| ComfyUI "Generate prompts" form | `ComfyPlan.gen_settings` | `GET`/`PUT /comfy/plans/{id}/gen-settings` |

Three rules a change here has to keep:

- **NULL means never saved, and both JSON columns use `JSON(none_as_null=True)`.** A plain `JSON` column stores Python `None` as JSON `null`, which is not SQL NULL, so "never saved" would silently stop being true. The import's first-copy-wins rule and the Captioning page's fallback to Settings' defaults both depend on it.
- **Category names travel in the body or query, never the path**, because a name can contain `/`.
- **Saving `gen_settings` must not bump `ComfyPlan.updated_at`.** `ComfyPage` keys the workflow editor on `plan.id + plan.updated_at`, and this form saves as the user types, so a bump would remount the editor underneath them. Both write sites pin the column to itself to suppress `onupdate`.

`POST /app-data/legacy-import` is the one-time carry-over of what a browser still holds, in one transaction, and is safe to re-run. Presets merge, skipping exact matches. Categories union. A single-value section is stored only if NULL, and otherwise reports `same` or `conflict` by dict equality, so key order doesn't matter. Entries are validated one at a time, so a bad one is reported instead of costing the batch a 422, and a Comfy entry naming a missing plan is reported as `discarded`. The route holds a module-level lock, because presets have no unique constraint and two tabs loading together would each read "no match" and insert the same one.

On the client, `frontend/src/utils/legacyImport.ts` runs that import once per load, started from `main.tsx`'s `else` branch without blocking render. Every query for this data awaits `runLegacyImport()` first, so a page can't read the server before the copy arrives; it never rejects, so a failed import leaves the page working and its section unmarked for the next load. What's been copied is recorded per section under `LEGACY_IMPORT_MARKER_KEY`, and it's the marker that stops a re-import, not the server's dedupe: a preset deleted after importing would otherwise come straight back on reload. The old keys are read but never deleted, as a copy to roll back to for one release.

`frontend/src/hooks/usePromptPresets.ts` is how a component reads and changes presets. Its `save` resolves to whether it worked, so a caller clears its form and reports success only then, and it reports failures itself. Show its `emptyMessage` rather than a literal when the list is empty: while the query is pending or has failed, "No presets saved yet." is what convinces someone their presets are gone.

### `constants/storage.ts` — the key registry

Every *statically named* storage key is declared here rather than inline in a component. One row per key; the six documented exceptions are listed below the table.

| Key | Storage & value | Read / written by | Notes |
|---|---|---|---|
| `CONFIRM_DEFAULT_KEY` | `localStorage`, `"cancel" \| "confirm"` | `ConfirmDialog` (reads on mount), `SettingsPage` (reads/writes on toggle) | The user's delete-confirmation default-button preference |
| `BRANCH_SNAPSHOT_KEY` | `localStorage`, `"ask" \| "auto"` | Read by `BranchSelector` before checkout and branch creation; written by `SettingsPage` | Branch/checkout snapshot behavior |
| `VERSIONS_BRANCH_KEY` | `sessionStorage` prefix (`"versions-branch"`); append `-${datasetId}` | Written by `VersionsPage.handleBranchSelect` and `SidebarVersionPanel`'s `onSelect` after checkout; read by `VersionsPage` on mount | Last-browsed branch on `VersionsPage` |
| `VIDEO_STRIP_COLLAPSED_KEY` | `localStorage` prefix (`"gallery-videos-collapsed"`); append `-${datasetId}` via `datasetScopedKey()` | Read and written by `components/gallery/VideoStrip.tsx` | Whether the gallery's video strip is collapsed, per dataset (see `docs/dev/video/ui.md`) |
| `GALLERY_PAGE_SIZE_KEY` | `localStorage`, `25 \| 50 \| 100 \| 200` | `GalleryPage`, `ImageDetailPage` (prefetch limit and end-of-page detection), `SettingsPage` | Images per page; read via `getGalleryPageSize()` (below) |
| `SUBFOLDER_RENAME_KEY` | `localStorage`, `"on" \| "off"` | Read by `SelectionToolbar` at mutation time; written by `SettingsPage` | Subfolder auto-rename preference |
| `GALLERY_CHECKBOX_SIZE_KEY` | `localStorage`, px | **Never read or written directly by a component** — owned by `uiPrefsStore`, which hydrates via `getGalleryCheckboxSize()` and writes back on set | Gallery selection-checkbox edge length. `clampGalleryCheckboxSize(v)` bounds it to `GALLERY_CHECKBOX_SIZE_MIN`/`MAX` (14/32) on both read and write, so a hand-edited entry can't produce a checkbox that covers the thumbnail; `GALLERY_CHECKBOX_SIZE_DEFAULT` is 18 |
| `GALLERY_LICENSE_BADGE_KEY` | `localStorage`, `"true" \| "false"`, **off** by default | Also owned by `uiPrefsStore` (`getGalleryLicenseBadge()`), never read directly by a component | Per-card license badge. Store ownership is what keeps a Settings pane and a gallery pane open side by side in step |
| `GALLERY_STYLE_METER_KEY` | `localStorage`, `"true" \| "false"`, **on** by default | Also owned by `uiPrefsStore` (`getGalleryStyleMeter()`), never read directly by a component | Per-card style-match percentile meter. On by default, so the getter reads `!== "false"` and not `=== "true"` — the absent key must mean on, the `CAPTION_DEFAULT_STRIP_REFS_KEY` precedent. It also gates `useStyleDistribution`'s `enabled`, so switching it off stops the request, not just the render |
| `LABEL_HOTKEYS_KEY` | `localStorage`, `"true" \| "false"`, **on** by default | Also owned by `uiPrefsStore` (`getLabelHotkeysEnabled()`), never read directly by a component | Whether single-key label hotkeys are live in the image detail view. On by default, so the getter reads `!== "false"` — the `GALLERY_STYLE_METER_KEY` precedent. In the store rather than read at render time so toggling it in Settings reaches an already-mounted detail pane in split view. See `docs/dev/labels-ui.md` § Hotkeys |
| `DECLARED_CATEGORIES_KEY` | `localStorage`, `string[]` | Read/written by `DatasetsPage` | Frontend-only empty category names — categories with no datasets assigned have no backend record, so this bridges the gap. Parsed with an `Array.isArray` guard; synced back on every `emptyCategories` change via `useEffect`; kept in sync with rename/delete mutations |
| `GALLERY_DEFAULT_SORT_KEY` | `localStorage`, sort index | Read at `GalleryPage` init time via `getGalleryDefaultSort()` | Gallery default |
| `GALLERY_DEFAULT_CAPTION_KEY` | `localStorage`, `"all" \| "captioned" \| "uncaptioned"` | Read at `GalleryPage` init time via `getGalleryDefaultCaptionFilter()` | Gallery default |
| `GALLERY_DEFAULT_QUALITY_KEY` | `localStorage`, flag key or `""` | Read at `GalleryPage` init time via `getGalleryDefaultQualityFilter()` | Gallery default |
| `CAPTION_DEFAULT_MODEL_KEY`, `CAPTION_DEFAULT_STYLE_KEY`, `CAPTION_DEFAULT_SCOPE_KEY`, `CAPTION_DEFAULT_DELIMITER_KEY`, `CAPTION_DEFAULT_STRIP_REFS_KEY`, `CAPTION_DEFAULT_RENAME_KEY`, `CAPTION_DEFAULT_SAVE_BACKUP_KEY` | `localStorage` | Read at `CaptioningPage` init time | Captioning defaults; serve as first-run fallbacks (see Persistent page state below) |
| `CAPTIONING_WORKFLOW_KEY`, `EXPORT_WORKFLOW_KEY`, `QUALITY_WORKFLOW_KEY`, `BULK_EDIT_WORKFLOW_KEY`, `TAG_CONSOLIDATE_WORKFLOW_KEY`, `DATASETS_UI_KEY` | `localStorage`, JSON blob | Their owning page | Global "workflow" blobs — one value shared across all datasets. `DATASETS_UI_KEY` holds DatasetsPage collapse / density / rail selection (see `docs/dev/datasets-page.md`) |
| `LEGACY_IMPORT_MARKER_KEY` | `localStorage`, JSON `{done: string[]}` | `utils/legacyImport.ts` | Which sections this browser has copied to the server (§ Data stored on the server) |
| `CAPTIONING_FILTERS_PREFIX`, `EXPORT_FILTERS_PREFIX`, `QUALITY_FILTERS_PREFIX`, `BULK_EDIT_FILTERS_PREFIX`, `STATS_FILTERS_PREFIX` | `localStorage` prefix, JSON blob; append `-${datasetId}` via `datasetScopedKey()` | Their owning page | Per-dataset "filters" blobs |

**Component-local keys — the six sanctioned exceptions.** The first four are storage owned
end-to-end by one component and read nowhere else, so a registry row would buy nothing; three
of those have a key computed per entity, which is why it cannot be a static constant, while
`STATS_VIS_KEY` is exempt for the other reason — its key *is* static, and single-owner locality
is what earns it the exemption. The last two are exceptions to the single-owner rule itself,
because a hand-off needs two ends:

- `` `comfy-genprompts-${planId}` `` and `` `comfy-genprompts-job-${planId}` ``
  (`components/comfy/GeneratePromptsModal.tsx`) — per-plan draft settings and the re-attachable
  job id, keyed by plan. See `docs/dev/comfy-prompts.md`.
- `` `video-extract-job-${videoId}` `` (`hooks/useVideoExtractJobs.ts`, via the exported
  `videoExtractJobKey`) — the re-attachable `video_extract` job id, keyed by video because a
  batch runs one job per video. It is what lets `VideoDetailPage` show a live bar for its own
  video with no modal open, and what survives a reload. Read by the hook's **recovery**
  effect, which is declared before its persist effect so the stored id is seen before the
  write can clear it. Retired with `clearPersisted` — never rewritten as `{jobId: null}`,
  which reads the same to every consumer but leaves one dead entry per video ever extracted —
  at all four sites: the persist effect when the live job id becomes `null`, the recovery
  effect when the fetched job is already terminal, that same effect's `.catch` on a 404, and
  deleting the video.
  Pass 2's re-extract dialog deliberately has **no** counterpart key: it emits per frame, so it
  re-attaches by adopting live jobs from `jobStore` instead. See
  `docs/dev/video/extract-ui.md` and `docs/dev/video/reextract-ui.md`.
- `STATS_VIS_KEY` (`"stats-visibility-v1"`, `pages/StatsPage.tsx`) — owned end-to-end by the
  `useStatsVisibility` hook and never read elsewhere. See `docs/dev/statistics.md`.
- `` `stats-hist-edges-{metric}-${datasetId}` `` (`pages/StatsPage.tsx`) — one per editable
  histogram, thirteen of them, passed to `HistPanel` as a `storageKey` and read/written there
  with raw `localStorage` calls. Keyed by metric *and* dataset, so custom bucketing on one
  metric never leaks to another. Not part of the `STATS_FILTERS_PREFIX` blob, which some prose
  used to imply. See `docs/dev/statistics.md` § Editable histograms.
- `` `gallery-state-${datasetId}` `` (`localStorage`) and `` `gallery-nav-${datasetId}` ``
  (`sessionStorage`) — the gallery↔detail hand-off pair, and the reason this list is not four
  entries long. `GalleryPage` owns both, but `ImageDetailPage` is the other end: it rewrites
  the `page`/`scrollTop` of the first and reads and rewrites the second. A registry row would
  be honest here; they are listed as exceptions rather than promoted because the shapes are
  documented where they are used, in `docs/dev/gallery/state.md` and
  `docs/dev/gallery/nav.md`. `gallery-state-*` is still
  page-private; `gallery-nav-*` no longer is — its shape, its read/write pair and the
  `injectNavId`/`removeNavId` mutators live in `frontend/src/utils/galleryNav.ts`, because
  three independent inline casts of it let the writer drop filters the reader needed.
- `"caption-prompt-presets"` (`utils/legacyImport.ts`) — where presets lived before they moved
  to the server. This build never writes it, though a tab left open on the previous build can.
  It's read once, by the import, and declared there because it goes away with that module.

Anything else belongs in `storage.ts` with a row above.

`getGalleryPageSize(): number` — shared helper that reads `GALLERY_PAGE_SIZE_KEY`, guards against `NaN`, and returns the default `100` on parse failure. Use this everywhere the page size is read; never inline the `parseInt` + fallback pattern.

The workflow and filters blobs are all loaded via `loadPersisted` (and reset via `clearPersisted`) from `utils/persistentState.ts`, and saved via `useDebouncedPersist` from `hooks/useDebouncedPersist.ts`. **Add new storage keys here rather than defining them inline in components.**

### Persistence helpers

`frontend/src/utils/persistentState.ts` — four helpers for durable `localStorage` state across navigation and browser restarts:
- `loadPersisted<T>(key, defaults): T` — reads a JSON blob from `localStorage`, shallow-merges it onto `defaults`. Returns a fresh copy of `defaults` if the key is absent or parsing fails. Shallow merge is forward-compatible: new fields added to `defaults` later appear automatically for users with older stored blobs.
- `savePersisted<T>(key, value): void` — serializes and stores a JSON blob; swallows quota/serialization errors (best-effort).
- `clearPersisted(key): void` — removes a stored blob (used by "Reset to defaults" buttons).
- `datasetScopedKey(prefix, datasetId): string` — returns `${prefix}-${datasetId}` for per-dataset filter blobs.

`frontend/src/hooks/useDebouncedPersist.ts` — `useDebouncedPersist(key: string | null, value: object, delay = PERSIST_DEBOUNCE_MS): void`. The single sanctioned way to write a persisted page blob; every debounced `savePersisted` call site goes through it. Import this rather than hand-rolling `useEffect` + `setTimeout` + `clearTimeout` — that pattern's cleanup *cancels* the pending write, so a change made inside the debounce window and followed by a navigation was silently lost (it was live in all ten call sites before this hook existed).

- `key === null` persists nothing this render — use it for the `datasetId`-less case rather than an early `return`.
- `value` is compared by `JSON.stringify`, not identity, so callers rebuild the object literal each render with no `useMemo`, and the hook owns its own dependency (this replaced hand-maintained dep arrays of up to 23 entries).
- **The flush is unmount-only, and deliberately not per-cleanup.** The debounce effect still *cancels* on a dependency change. This matters because per-dataset keys change while mounted in split-pane mode, and the effect that reloads the blob for the new dataset lands a render later; flushing on every cleanup would write the previous dataset's values under the new dataset's key and corrupt it. Do not "simplify" the two effects into one.
- A `pagehide` listener covers tab close/reload inside the debounce window.
- **The window itself is `PERSIST_DEBOUNCE_MS` in `frontend/src/constants/storage.ts` (350 ms), and every site that depends on it reads it from there** — this hook's `delay` default, `GalleryPage`'s custom timer below, and `frontend/e2e/gallery-restore.spec.ts`, which waits `PERSIST_DEBOUNCE_MS * 3` before asserting the write landed. The spec used to hard-code `1000`, so raising the window past a second would have left it silently passing without guarding the regression it was written for. It lives in `constants/storage.ts` and not in this hook file because that module compiles standalone under the e2e tsconfig (no DOM lib, no React); the spec is the only e2e file importing from `src/`, and `npm run typecheck:e2e` is what keeps that import honest.

### Three persistence shapes exist, and the two off-hook ones are deliberate

Don't "unify" them without reading this. The hook covers every *debounced blob* write (the ten workflow/filters sites). The exceptions:
- **Synchronous, undebounced**: `TagConsolidatePage` (`{threshold}`) and `GeneratePromptsModal` (`{instructions, providerId, …}`) call `savePersisted` directly in an effect body. With no debounce there is no window in which an unmount can drop a write, so the hook would *add* a `PERSIST_DEBOUNCE_MS` delay rather than fix anything — strictly worse for `GeneratePromptsModal`, which is closed by unmounting. Both carry a comment saying so.
- **Custom, with its own unmount flush**: `GalleryPage` writes `gallery-state-${datasetId}` via raw `localStorage` on its own `PERSIST_DEBOUNCE_MS` timer plus a separate unmount-only effect that re-reads `scrollTop` from a DOM ref (see `docs/dev/gallery/state.md`). It cannot use the hook because the hook's `value` is computed during render, whereas `scrollTop` must be sampled at flush time. This effect is the prior art `useDebouncedPersist` generalized from — it identified the <350ms navigation gap long before the other ten sites were fixed. The price of staying off the hook is that a new field in that blob is **six** hand edits rather than one (enumerated in `docs/dev/labels-ui.md` § GalleryPage persistence: the six edits), and that **neither write site merges with the stored value** — each `JSON.stringify`s a full literal, so a field present in one and missing from the other is silently deleted on whichever path happens to run.

### Persistent page state pattern

`CaptioningPage`, `ExportPage`, `QualityPage`, `BulkEditPage`, `StatsPage`, and `GalleryPage` all persist their configuration to `localStorage` so settings survive navigation and browser restarts. The split is:
- *Workflow* fields (model/prompt/style/toggles — *how you like to work*) are stored in a **global** blob shared across all datasets, keyed by `*_WORKFLOW_KEY`. Loaded once at `useState` init time.
- *Filters/scope* fields that reference dataset-specific data (subfolder, score thresholds, flag exclusions) are stored in a **per-dataset** blob, keyed by `datasetScopedKey(*_FILTERS_PREFIX, datasetId)`. Reloaded when `datasetId` changes (via a `prevDatasetId` ref guard effect) so pane-mode dataset switches load the right per-dataset blob without unmounting.
- Both blobs are saved by `useDebouncedPersist` (above). Pass `null` as the key for the per-dataset blob while `datasetId` is unset instead of guarding with an early `return`.
- Each page exposes a "Reset to defaults" button (ghost style, near the configuration header) that calls `clearPersisted` on both blobs and resets state to the hardcoded defaults, re-reading `CAPTION_DEFAULT_*` Settings values as appropriate for `CaptioningPage`. If the page contains child forms with their own internal `useState` (e.g. text fields, operation pickers), those won't remount just because the parent scope/tab state returns to its default value. Fix by adding a `resetKey` counter to the parent (`const [resetKey, setResetKey] = useState(0)`), incrementing it in the reset handler (`setResetKey(k => k + 1)`), and including it in the child form's `key` prop (`key={\`${scope}-${resetKey}\``}). `BulkEditPage` uses this pattern — all nine tab forms carry `key={\`${scope}-${resetKey}\`}` (nine across eight tabs; the Detections tab renders two).
- Settings → Captioning tab also has a "Reset remembered Captioning configuration" button that clears `CAPTIONING_WORKFLOW_KEY` globally **and every per-dataset `CAPTIONING_FILTERS_PREFIX` blob**. It needs no dataset list to do it: the handler scans `localStorage` for the prefix and removes every key it matches. So the one button forgets the captioning setup for every dataset, not only the global workflow.
- `Set<string>` fields (`excludeFlags`, `selectedSubfolders`, `selectedRefIds`, `selectedFlags`, and `GalleryPage`'s `expandedPaths`) are serialized as `string[]` and converted back to `Set` at each page's load boundary; `loadPersisted`/`savePersisted` stay generic over plain JSON shapes. Guard the restore with `Array.isArray` where the blob predates the field — a shallow merge hands back whatever was stored, including a value written by a build that had no such key.
