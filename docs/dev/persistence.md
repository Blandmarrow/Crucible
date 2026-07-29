# Frontend persistence: storage keys, blobs & the persistent page state pattern

This file covers everything the frontend stores in `localStorage`/`sessionStorage`: the key registry in `frontend/src/constants/storage.ts`, the `loadPersisted`/`useDebouncedPersist` helpers, the three persistence shapes that exist and why two of them are deliberately off the hook, and the workflow/filters split every configurable page follows. The stores that read these keys live in `docs/dev/frontend-core.md` (§ Frontend state).

### `constants/storage.ts` — the key registry

Every *statically named* storage key is declared here rather than inline in a component. One row per key; the two documented exceptions are listed below the table.

| Key | Storage & value | Read / written by | Notes |
|---|---|---|---|
| `CONFIRM_DEFAULT_KEY` | `localStorage`, `"cancel" \| "confirm"` | `ConfirmDialog` (reads on mount), `SettingsPage` (reads/writes on toggle) | The user's delete-confirmation default-button preference |
| `BRANCH_SNAPSHOT_KEY` | `localStorage`, `"ask" \| "auto"` | Read by `BranchSelector` before checkout and branch creation; written by `SettingsPage` | Branch/checkout snapshot behavior |
| `VERSIONS_BRANCH_KEY` | `sessionStorage` prefix (`"versions-branch"`); append `-${datasetId}` | Written by `VersionsPage.handleBranchSelect` and `SidebarVersionPanel`'s `onSelect` after checkout; read by `VersionsPage` on mount | Last-browsed branch on `VersionsPage` |
| `VIDEO_STRIP_COLLAPSED_KEY` | `localStorage` prefix (`"gallery-videos-collapsed"`); append `-${datasetId}` via `datasetScopedKey()` | Read and written by `components/gallery/VideoStrip.tsx` | Whether the gallery's video strip is collapsed, per dataset (see `docs/dev/video-ui.md`) |
| `GALLERY_PAGE_SIZE_KEY` | `localStorage`, `25 \| 50 \| 100 \| 200` | `GalleryPage`, `ImageDetailPage` (prefetch limit and end-of-page detection), `SettingsPage` | Images per page; read via `getGalleryPageSize()` (below) |
| `SUBFOLDER_RENAME_KEY` | `localStorage`, `"on" \| "off"` | Read by `SelectionToolbar` at mutation time; written by `SettingsPage` | Subfolder auto-rename preference |
| `GALLERY_CHECKBOX_SIZE_KEY` | `localStorage`, px | **Never read or written directly by a component** — owned by `uiPrefsStore`, which hydrates via `getGalleryCheckboxSize()` and writes back on set | Gallery selection-checkbox edge length. `clampGalleryCheckboxSize(v)` bounds it to `GALLERY_CHECKBOX_SIZE_MIN`/`MAX` (14/32) on both read and write, so a hand-edited entry can't produce a checkbox that covers the thumbnail; `GALLERY_CHECKBOX_SIZE_DEFAULT` is 18 |
| `GALLERY_LICENSE_BADGE_KEY` | `localStorage`, `"true" \| "false"`, **off** by default | Also owned by `uiPrefsStore` (`getGalleryLicenseBadge()`), never read directly by a component | Per-card license badge. Store ownership is what keeps a Settings pane and a gallery pane open side by side in step |
| `DECLARED_CATEGORIES_KEY` | `localStorage`, `string[]` | Read/written by `DatasetsPage` | Frontend-only empty category names — categories with no datasets assigned have no backend record, so this bridges the gap. Parsed with an `Array.isArray` guard; synced back on every `emptyCategories` change via `useEffect`; kept in sync with rename/delete mutations |
| `GALLERY_DEFAULT_SORT_KEY` | `localStorage`, sort index | Read at `GalleryPage` init time via `getGalleryDefaultSort()` | Gallery default |
| `GALLERY_DEFAULT_CAPTION_KEY` | `localStorage`, `"all" \| "captioned" \| "uncaptioned"` | Read at `GalleryPage` init time via `getGalleryDefaultCaptionFilter()` | Gallery default |
| `GALLERY_DEFAULT_QUALITY_KEY` | `localStorage`, flag key or `""` | Read at `GalleryPage` init time via `getGalleryDefaultQualityFilter()` | Gallery default |
| `CAPTION_DEFAULT_MODEL_KEY`, `CAPTION_DEFAULT_STYLE_KEY`, `CAPTION_DEFAULT_SCOPE_KEY`, `CAPTION_DEFAULT_DELIMITER_KEY`, `CAPTION_DEFAULT_STRIP_REFS_KEY`, `CAPTION_DEFAULT_RENAME_KEY`, `CAPTION_DEFAULT_SAVE_BACKUP_KEY` | `localStorage` | Read at `CaptioningPage` init time | Captioning defaults; serve as first-run fallbacks (see Persistent page state below) |
| `CAPTIONING_WORKFLOW_KEY`, `EXPORT_WORKFLOW_KEY`, `QUALITY_WORKFLOW_KEY`, `BULK_EDIT_WORKFLOW_KEY`, `TAG_CONSOLIDATE_WORKFLOW_KEY`, `DATASETS_UI_KEY` | `localStorage`, JSON blob | Their owning page | Global "workflow" blobs — one value shared across all datasets. `DATASETS_UI_KEY` holds DatasetsPage collapse / density / rail selection (see `docs/dev/datasets-page.md`) |
| `CAPTIONING_FILTERS_PREFIX`, `EXPORT_FILTERS_PREFIX`, `QUALITY_FILTERS_PREFIX`, `BULK_EDIT_FILTERS_PREFIX`, `STATS_FILTERS_PREFIX` | `localStorage` prefix, JSON blob; append `-${datasetId}` via `datasetScopedKey()` | Their owning page | Per-dataset "filters" blobs |

**Component-local keys — the three sanctioned exceptions.** All are storage whose key is
computed per entity, so it cannot be a static constant and has no meaningful registry row:

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
  at all four sites: the job going terminal, a recovery GET that 404s, and deleting the video.
  Pass 2's re-extract dialog deliberately has **no** counterpart key: it emits per frame, so it
  re-attaches by adopting live jobs from `jobStore` instead. See
  `docs/dev/video-extract-ui.md` and `docs/dev/video-reextract.md`.
- `STATS_VIS_KEY` (`"stats-visibility-v1"`, `pages/StatsPage.tsx`) — owned end-to-end by the
  `useStatsVisibility` hook and never read elsewhere. See `docs/dev/statistics.md`.

Anything else belongs in `storage.ts` with a row above.

`getGalleryPageSize(): number` — shared helper that reads `GALLERY_PAGE_SIZE_KEY`, guards against `NaN`, and returns the default `100` on parse failure. Use this everywhere the page size is read; never inline the `parseInt` + fallback pattern.

The workflow and filters blobs are all loaded via `loadPersisted` (and reset via `clearPersisted`) from `utils/persistentState.ts`, and saved via `useDebouncedPersist` from `hooks/useDebouncedPersist.ts`. **Add new storage keys here rather than defining them inline in components.**

### Persistence helpers

`frontend/src/utils/persistentState.ts` — four helpers for durable `localStorage` state across navigation and browser restarts:
- `loadPersisted<T>(key, defaults): T` — reads a JSON blob from `localStorage`, shallow-merges it onto `defaults`. Returns a fresh copy of `defaults` if the key is absent or parsing fails. Shallow merge is forward-compatible: new fields added to `defaults` later appear automatically for users with older stored blobs.
- `savePersisted<T>(key, value): void` — serializes and stores a JSON blob; swallows quota/serialization errors (best-effort).
- `clearPersisted(key): void` — removes a stored blob (used by "Reset to defaults" buttons).
- `datasetScopedKey(prefix, datasetId): string` — returns `${prefix}-${datasetId}` for per-dataset filter blobs.

`frontend/src/hooks/useDebouncedPersist.ts` — `useDebouncedPersist(key: string | null, value: object, delay = 350): void`. The single sanctioned way to write a persisted page blob; every debounced `savePersisted` call site goes through it. Import this rather than hand-rolling `useEffect` + `setTimeout` + `clearTimeout` — that pattern's cleanup *cancels* the pending write, so a change made inside the debounce window and followed by a navigation was silently lost (it was live in all ten call sites before this hook existed).

- `key === null` persists nothing this render — use it for the `datasetId`-less case rather than an early `return`.
- `value` is compared by `JSON.stringify`, not identity, so callers rebuild the object literal each render with no `useMemo`, and the hook owns its own dependency (this replaced hand-maintained dep arrays of up to 23 entries).
- **The flush is unmount-only, and deliberately not per-cleanup.** The debounce effect still *cancels* on a dependency change. This matters because per-dataset keys change while mounted in split-pane mode, and the effect that reloads the blob for the new dataset lands a render later; flushing on every cleanup would write the previous dataset's values under the new dataset's key and corrupt it. Do not "simplify" the two effects into one.
- A `pagehide` listener covers tab close/reload inside the debounce window.

### Three persistence shapes exist, and the two off-hook ones are deliberate

Don't "unify" them without reading this. The hook covers every *debounced blob* write (the ten workflow/filters sites). The exceptions:
- **Synchronous, undebounced**: `TagConsolidatePage` (`{threshold}`) and `GeneratePromptsModal` (`{instructions, providerId, …}`) call `savePersisted` directly in an effect body. With no debounce there is no window in which an unmount can drop a write, so the hook would *add* a 350ms delay rather than fix anything — strictly worse for `GeneratePromptsModal`, which is closed by unmounting. Both carry a comment saying so.
- **Custom, with its own unmount flush**: `GalleryPage` writes `gallery-state-${datasetId}` via raw `localStorage` on a 350ms timer plus a separate unmount-only effect that re-reads `scrollTop` from a DOM ref (see `docs/dev/image-detail.md`). It cannot use the hook because the hook's `value` is computed during render, whereas `scrollTop` must be sampled at flush time. This effect is the prior art `useDebouncedPersist` generalized from — it identified the <350ms navigation gap long before the other ten sites were fixed.

### Persistent page state pattern

`CaptioningPage`, `ExportPage`, `QualityPage`, `BulkEditPage`, `StatsPage`, and `GalleryPage` all persist their configuration to `localStorage` so settings survive navigation and browser restarts. The split is:
- *Workflow* fields (model/prompt/style/toggles — *how you like to work*) are stored in a **global** blob shared across all datasets, keyed by `*_WORKFLOW_KEY`. Loaded once at `useState` init time.
- *Filters/scope* fields that reference dataset-specific data (subfolder, score thresholds, flag exclusions) are stored in a **per-dataset** blob, keyed by `datasetScopedKey(*_FILTERS_PREFIX, datasetId)`. Reloaded when `datasetId` changes (via a `prevDatasetId` ref guard effect) so pane-mode dataset switches load the right per-dataset blob without unmounting.
- Both blobs are saved by `useDebouncedPersist` (above). Pass `null` as the key for the per-dataset blob while `datasetId` is unset instead of guarding with an early `return`.
- Each page exposes a "Reset to defaults" button (ghost style, near the configuration header) that calls `clearPersisted` on both blobs and resets state to the hardcoded defaults, re-reading `CAPTION_DEFAULT_*` Settings values as appropriate for `CaptioningPage`. If the page contains child forms with their own internal `useState` (e.g. text fields, operation pickers), those won't remount just because the parent scope/tab state returns to its default value. Fix by adding a `resetKey` counter to the parent (`const [resetKey, setResetKey] = useState(0)`), incrementing it in the reset handler (`setResetKey(k => k + 1)`), and including it in the child form's `key` prop (`key={\`${scope}-${resetKey}\``}). `BulkEditPage` uses this pattern — all eight tab forms carry `key={\`${scope}-${resetKey}\`}` (eight across seven tabs; the Detections tab renders two).
- Settings → Captioning tab also has a "Reset remembered Captioning configuration" button that clears `CAPTIONING_WORKFLOW_KEY` globally (the per-dataset filter blobs are reset per-page since Settings has no enumeration of all dataset IDs).
- `Set<string>` fields (`excludeFlags`, `selectedSubfolders`, `selectedRefIds`, `selectedFlags`) are serialized as `string[]` and converted back to `Set` at each page's load boundary; `loadPersisted`/`savePersisted` stay generic over plain JSON shapes.
