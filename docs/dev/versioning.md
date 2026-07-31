# Dataset versioning

This file covers snapshot-based version control: the versioning-mode and dataset-busy guards, the model and the copy-on-write object store, the `/datasets/{id}/versions` routes, and the frontend versioning UI.

Snapshot-based version control for datasets. Users can create named snapshots, restore to any prior state, compare two snapshots (diff), and maintain named branches.

`backend/services/version_service.py` itself — snapshot creation, restore's four passes, diff, and the two hooks other routers call — is `docs/dev/versioning-service.md`.

## Guards

**Versioning mode** — stored in `threshold_settings.versioning_mode` (same singleton row as quality thresholds, same `GET/PATCH /api/v1/settings/thresholds` endpoints). Three values:

| Mode | Snapshot behaviour | COW overwrite hook |
|---|---|---|
| `"off"` | Disabled — every *write* route 400s via `_require_versioning_enabled` (snapshot, prune, branch create/delete/checkout, version update/delete, restore). The four read routes still answer, deliberately: `Sidebar` and `DatasetsPage` list branches and versions whatever the mode is | No-op |
| `"manual"` | Snapshot copies every file to the object store eagerly (full point-in-time backup). Always runs as a background job. | No-op |
| `"auto"` | Snapshot records metadata + `file_hash=NULL`; object store copies are made lazily on first overwrite (copy-on-write). | Fires before in-place resize/upscale/LUT replace |

Deletion protection fires in both `"manual"` and `"auto"` because deletion is irreversible — the file is backed up before `Path.unlink()`.

**Dataset-busy guard** — `backend/services/dataset_busy.py`, a small in-process module (single-process app): `busy(dataset_id, reason)` contextmanager + `ensure_not_busy(dataset_id)` which raises HTTP 409. The five versioning job `_run` wrappers in `routers/versioning.py` (restore, checkout, background snapshot, background branch-create, prune) hold the flag for the job's duration, and they are no longer the only holders: `routers/videos.py`'s `_delete_previous_frames` takes it around the replace step alone, because that step deletes N rows, N files and N thumbnails in seconds — extraction as a whole deliberately does not hold it (`docs/dev/video-extract.md`). Interactive *direct-mutation* endpoints call `ensure_not_busy` after resolving the dataset id — `routers/images.py` (upload, delete/batch-delete/bulk-delete, bulk-rename, rename, resize, crop, batch move-subfolder, batch move-dataset + copy-dataset — both source and target, plus `bulk_provenance` and single-image provenance), `routers/quality.py` (`resolve_duplicates`), `routers/captions.py` (single caption save, find-replace, bulk-edit) — and so do the video and filesystem routers, whose own docs own the detail: `routers/videos.py` (extract, re-extract, rename, delete — `docs/dev/video-extract.md`, `docs/dev/video-reextract.md`, `docs/dev/video-endpoints.md`) and `routers/filesystem.py` (rename, delete — `docs/dev/file-browser.md`). Treat the list as "where to look", not as exhaustive; grep `ensure_not_busy` for the current set. Enqueued background jobs are deliberately *not* guarded — the single job queue already serializes them against versioning jobs. No frontend change: the 409 surfaces through the normal error toast.

## Model and storage

**Object store** — content-addressable, git-style:
`{dataset.folder_path}/.versions/objects/{sha256[:2]}/{sha256[2:]}`

Files are stored **only once per unique content** (idempotent). All writes go through `_store_object(dataset_folder, src_path) -> sha256`: it streams the file once, hashing *while* writing to an `objects/.tmp-{uuid}` temp file (same filesystem), then atomically `os.replace`s it to the hash path — so the stored bytes and their address can never disagree, even if the source file is overwritten mid-copy. This closes the hash-then-copy TOCTOU that could permanently poison a content-addressed entry (realistic race: a manual-mode snapshot job vs. an interactive replace endpoint). Never add a separate "hash, then copy" path.

**GC** — `prune_object_store(db, dataset_id, job_id=None, min_age_seconds=3600)` deletes objects no `VersionImageState.file_hash` of the dataset references, plus stale `.tmp-*` leftovers, and returns `{objects_deleted, objects_kept, bytes_freed}`. Files younger than `min_age_seconds` are always kept (a COW write racing the reference scan may have stored an object whose state row isn't committed yet — belt-and-suspenders on top of the busy flag). Objects are per-dataset (the store lives under the dataset folder), so cross-dataset references are impossible. User-triggered via `POST /{id}/versions/prune` (below) and the "Prune storage" button on `VersionsPage`.

**`is_present` invariant**: A `VersionImageState` row always has `is_present=True` — it records that the image was present at snapshot time. When an image is deleted, post-deletion snapshots simply have no row for it. This means restoring a pre-deletion snapshot correctly re-creates the image from the object store (the deletion hook backs up the file before it is unlinked). Do not retroactively set `is_present=False` on old rows.

**DB tables** (`backend/models/versioning.py`):

| Table | Purpose |
|---|---|
| `dataset_branches` | Named branches; `head_version_id` FK to latest snapshot on the branch |
| `dataset_versions` | Snapshot records; `parent_id` self-ref for chain; auto-named `Snapshot YYYY-MM-DD HH:MM` if name omitted |
| `version_image_states` | One row per image per snapshot — stores all metadata + `file_hash` (SHA-256; NULL until COW fills it in `"auto"` mode) + `processing_history` (JSON array of replace operations) + `sort_order` (custom gallery position; NULL if image was unordered at snapshot time) + the five provenance columns (see **Provenance mirror** below) |

`datasets.current_branch_id` — tracks the active branch (updated on checkout).

**`DatasetVersion` fields**: `id`, `dataset_id`, `branch_id`, `parent_id`, `name`, `description`, `image_count`, `created_at`, `source` (`Literal["manual", "pre_restore", "branch_init"]`), `is_pinned` (`bool`).

**`passive_deletes`**: `DatasetVersion.image_states` sets `passive_deletes=True` — `version_image_states.version_id` has `ondelete="CASCADE"` and `PRAGMA foreign_keys=ON` is set per connection in `backend/database.py`, so the DB deletes state rows and the ORM no longer loads thousands of them per version delete. `DatasetBranch.versions` deliberately does **not**: its FK is `ondelete="SET NULL"`, so the ORM cascade must keep deleting version rows itself (each of those deletes now skips loading states, which is the actual win).

## Router and endpoints

**Backend router** (`backend/routers/versioning.py`, prefix `/datasets`, registered in `main.py`):

| Method | Path | Behaviour |
|---|---|---|
| `GET` | `/{id}/versions/branches` | List branches |
| `POST` | `/{id}/versions/branches` | Create branch; body: `BranchCreate { name, from_version_id?, include_snapshot: bool = true }`. Sync ≤100 images or when `include_snapshot=false`, else bg job |
| `POST` | `/{id}/versions/branches/{branch_id}/checkout` | Checkout branch (always bg job); body: `CheckoutRequest { pre_restore_snapshot: bool = true }` |
| `DELETE` | `/{id}/versions/branches/{branch_id}` | Delete branch + all its versions (cascade); 400 if last branch or if branch is currently active (`dataset.current_branch_id`) — must switch first |
| `GET` | `/{id}/versions` | List versions — filters: `branch_id`, `search` (name/description ilike), `created_after`/`created_before` (ISO date strings); sorted pinned-first then `created_at DESC` |
| `POST` | `/{id}/versions` | Create snapshot (manual mode always bg job; auto mode inline ≤100 images) |
| `GET` | `/{id}/versions/diff` | Diff two versions (`?v1=&v2=`) — declared BEFORE `/{version_id}` to prevent FastAPI collision |
| `GET` | `/{id}/versions/{version_id}` | Get version detail |
| `PATCH` | `/{id}/versions/{version_id}` | Update version (`is_pinned`) |
| `DELETE` | `/{id}/versions/{version_id}` | Delete version (400 if last on branch) |
| `POST` | `/{id}/versions/{version_id}/restore` | Restore (always bg job → `{job_id}`) |
| `POST` | `/{id}/versions/prune` | Prune unreferenced object-store files (bg job `prune_versions` → `{job_id}`); 400 if versioning mode is `off`; summary written to `BackgroundJob.result_data` and emitted as the final progress message |

The service behind these routes — `create_snapshot`, `restore_snapshot`'s four passes,
`diff_versions`, the two copy-on-write hooks and the table of routers that fire them — is
`docs/dev/versioning-service.md`.

## Frontend

**Frontend**:
- `frontend/src/pages/VersionsPage.tsx` — route `/datasets/:datasetId/versions`, sidebar "Versions". Shows disabled-state when `versioning_mode="off"` (link to Settings). Otherwise shows branch selector, filter bar (debounced search + date range), version list with source badges (`Manual`/`Pre-restore`/`Branch init`) and pin icon per card. Pin toggle uses `setQueryData` optimistic update + client-side re-sort (no refetch). Active branch persisted to `sessionStorage` under `VERSIONS_BRANCH_KEY-${datasetId}`; falls back to `dataset.current_branch_id`, then `branches[0]`. `resolvedBranchId = activeBranch?.id` is passed to `BranchSelector` (not raw `activeBranchId`) so the dropdown stays in sync after restarts. A `useRef`+`useEffect` watches `dataset.current_branch_id` post-mount; the guard (`prev !== undefined`) prevents the initial data load from clobbering the stored preference.
- `frontend/src/components/versioning/CreateSnapshotModal.tsx` — name + description inputs; shows `JobProgressBar` during bg job; passes `activeBranchId` in the snapshot body so new snapshots land on the correct branch.
- `frontend/src/components/versioning/RestoreConfirmModal.tsx` — keep/remove radio for extra images, pre-restore snapshot checkbox, file-unavailability warning, `JobProgressBar`
- `frontend/src/components/versioning/DiffModal.tsx` — select two versions; shows Added/Removed/Modified sections with field-level changes
- `frontend/src/components/versioning/BranchSelector.tsx` — branch `<select>` + "New branch…" option. Checkout and branch creation show a `SnapshotPrompt` dialog first when `BRANCH_SNAPSHOT_KEY === "ask"`. Checkout triggers a bg job; a fixed-position progress card appears bottom-right; `onSelect` fires only after job completion to avoid stale data. For sync branch creation (≤100 images), `doCheckout(result.id, false)` is called immediately so `current_branch_id` updates — `pre_restore_snapshot=false` because the branch was just created. A trash icon opens a delete modal with its own `<select>` (excluding `currentBranchId`); the active branch cannot be deleted.
- `frontend/src/components/common/JobProgressBar.tsx` — shared progress bar (message + animated fill bar); used by snapshot, restore, and branch-checkout flows
- `frontend/src/api/versioning.ts` — `versioningApi`: `listBranches`, `createBranch(datasetId, name, fromVersionId?, includeSnapshot = true)`, `checkoutBranch(datasetId, branchId, preRestoreSnapshot = true)`, `deleteBranch(datasetId, branchId)`, `listVersions` (accepts `ListVersionsParams` object with `branchId`, `search`, `createdAfter`, `createdBefore`), `createSnapshot`, `getVersion`, `deleteVersion`, `updateVersion` (PATCH for `is_pinned`), `restoreVersion`, `pruneStorage` (→ `{job_id}`; triggered by the "Prune storage" ghost button in the `VersionsPage` header, behind a `ConfirmDialog`; the global job progress bar picks the job up automatically — no cache invalidation needed), `diff`. `createSnapshot`/`createBranch` return `Version | { job_id: string }` — discriminate with `"job_id" in data`.
- **Sidebar version panel** (`SidebarVersionPanel.tsx`) — accordion below "Active dataset" label. Collapsed: branch name · head snapshot + chevron. Expanded: `<BranchSelector>`, the 7 most recent snapshots with `[Restore]` buttons (current shows "Now"), and "View all →" link. Snapshot query (`["versions", datasetId, activeBranch.id, "sidebar"]`) gated on `expanded` with `limit: 7`. `onSelect` writes `VERSIONS_BRANCH_KEY-${datasetId}` to sessionStorage; `onSuccess` after restore invalidates branches, dataset, images, captions, and versions queries. Only rendered when `activeBranch` is defined.

**TanStack Query keys**:
- `["branches", datasetId]` — invalidated after snapshot creation, restore, checkout
- `["versions", datasetId, resolvedBranchId, search, createdAfter, createdBefore]` — invalidated after snapshot creation, delete, restore; pin toggle uses `setQueryData` instead of invalidation
- `["images", datasetId]` — invalidated after restore and after checkout (image set and captions change)
- `["image"]` — prefix invalidation (no imageId) after restore and checkout; clears all cached image detail pages so `ImageDetailPage` refetches immediately
- `["caption"]` — prefix invalidation after restore and checkout; clears all cached caption data
- `["dataset", datasetId]` — invalidated after restore and checkout (image count, `current_branch_id`)
- `["dataset-stats", datasetId]`, `["tag-stats", datasetId]`, `["score-values", datasetId]`, `["tag-cooccurrence", datasetId]` — all four stats queries invalidated after restore (`VersionsPage` and `SidebarVersionPanel`) and after checkout (`BranchSelector`)

## Provenance mirror and regression tests

**Provenance mirror**: `VersionImageState` mirrors the five `Image` provenance columns (`source_name`, `source_url`, `license`, `attribution`, `source_meta`) — they are set in the snapshot loop's `VersionImageState(...)` construction and written back field-by-field in restore Pass 2c, alongside `generation_metadata` in both, and all five are diffed. `create_snapshot`'s `select(Image)` must carry `options=[undefer(Image.source_meta)]`: that column is `deferred=True`, and the snapshot build reads it, so without the undefer it lazy-loads on an async session and raises `MissingGreenlet`. `restore_snapshot` only assigns it, which triggers no load. Omitting any of them makes a restore silently wipe provenance; `test_provenance.py::test_snapshot_restore_preserves_provenance` guards it. See `docs/dev/provenance.md`.

**Regression tests**: `backend/tests/test_versioning_restore.py` exercises the real service against a scratch DB + files (via `asyncio.run`, no async pytest plugin): filename swaps, deleted-image name reuse (keep/remove), pre-restore snapshot branch placement, cross-dataset-move restore (fork + adopt), and abort-on-failed-pre-snapshot. Extend it when touching restore's pass structure. `backend/tests/test_versioning_maintenance.py` (same harness) covers `_store_object` atomicity + the stale-`precomputed_sha256` TOCTOU regression, prune (referenced kept, junk/`.tmp-*` deleted, `min_age_seconds` skip), the busy guard, and the version-delete DB cascade.
