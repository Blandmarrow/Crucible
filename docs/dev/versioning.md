# Dataset versioning

This file covers snapshot-based version control: branches, snapshots, the copy-on-write object store, diff, restore, and the frontend versioning UI.

### Dataset versioning

Snapshot-based version control for datasets. Users can create named snapshots, restore to any prior state, compare two snapshots (diff), and maintain named branches.

**Versioning mode** — stored in `threshold_settings.versioning_mode` (same singleton row as quality thresholds, same `GET/PATCH /api/v1/settings/thresholds` endpoints). Three values:

| Mode | Snapshot behaviour | COW overwrite hook |
|---|---|---|
| `"off"` | Disabled — all versioning endpoints return 400 | No-op |
| `"manual"` | Snapshot copies every file to the object store eagerly (full point-in-time backup). Always runs as a background job. | No-op |
| `"auto"` | Snapshot records metadata + `file_hash=NULL`; object store copies are made lazily on first overwrite (copy-on-write). | Fires before in-place resize/upscale/LUT replace |

Deletion protection fires in both `"manual"` and `"auto"` because deletion is irreversible — the file is backed up before `Path.unlink()`.

**Object store** — content-addressable, git-style:
`{dataset.folder_path}/.versions/objects/{sha256[:2]}/{sha256[2:]}`

Files are stored **only once per unique content** (idempotent copy). No GC in v1 — deleted versions leave orphaned objects.

**`is_present` invariant**: A `VersionImageState` row always has `is_present=True` — it records that the image was present at snapshot time. When an image is deleted, post-deletion snapshots simply have no row for it. This means restoring a pre-deletion snapshot correctly re-creates the image from the object store (the deletion hook backs up the file before it is unlinked). Do not retroactively set `is_present=False` on old rows.

**DB tables** (`backend/models/versioning.py`):

| Table | Purpose |
|---|---|
| `dataset_branches` | Named branches; `head_version_id` FK to latest snapshot on the branch |
| `dataset_versions` | Snapshot records; `parent_id` self-ref for chain; auto-named `Snapshot YYYY-MM-DD HH:MM` if name omitted |
| `version_image_states` | One row per image per snapshot — stores all metadata + `file_hash` (SHA-256; NULL until COW fills it in `"auto"` mode) + `processing_history` (JSON array of replace operations) + `sort_order` (custom gallery position; NULL if image was unordered at snapshot time) |

`datasets.current_branch_id` — tracks the active branch (updated on checkout).

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

**Backend service** (`backend/services/version_service.py`):

Key functions:
- `protect_file_before_overwrite(image_id, file_path, db)` — COW hook; no-op unless `"auto"` mode and the image has NULL-hash snapshot rows
- `mark_image_deleted_in_versions(image_id, file_path, db)` — deletion hook; no-op if `"off"` or no snapshot rows exist for image
- `_backup_and_record_hash(image_id, file_path, dataset_folder, db)` — shared helper: hash → copy to object store → backfill all NULL `file_hash` rows for the image in one `UPDATE`
- `create_snapshot(db, dataset_id, name, description, branch_id, job_id, source)` — creates snapshot; `source` is `"manual"` (user-triggered), `"pre_restore"` (auto before restore), or `"branch_init"` (new branch). `"manual"` mode also copies every file eagerly.
- `restore_snapshot(db, dataset_id, version_id, handle_extra_images, pre_restore_snapshot, job_id)` — restores files from object store + updates DB; optionally auto-snapshots current state first; after all files are restored, sets `branch.head_version_id = version_id` so the UI "Current" badge moves to the restored snapshot
- `diff_versions(db, dataset_id, v1, v2)` — pure DB, no background job; uses `_DIFF_COLS` column-explicit select for efficiency; `processing_history` changes render as `+`/`−` operation badges in `DiffModal`

**Copy-on-write injection points** (existing routers, all fire before the file operation):

| File | Operation |
|---|---|
| `backend/routers/images.py` | `resize` endpoint, batch crop `_run`, batch resize `_run` — calls `protect_file_before_overwrite` |
| `backend/routers/images.py` | `crop` endpoint, replace=True branch — calls `protect_file_before_overwrite` |
| `backend/routers/images.py` | `delete_image`, `batch_delete`, `bulk_delete_filtered` (`POST /images/bulk-delete`) — calls `mark_image_deleted_in_versions` |
| `backend/routers/upscaling.py` | `_run` coroutine, replace=True branch — calls `protect_file_before_overwrite` |
| `backend/routers/lut.py` | `_run` coroutine, replace=True branch — calls `protect_file_before_overwrite` |
| `backend/routers/quality.py` | `resolve_duplicates`, delete branch — calls `mark_image_deleted_in_versions` per row, then `refresh_stats` per dataset |
| `backend/services/version_service.py` | `restore_snapshot`, `handle_extra_images="remove"` branch — calls `mark_image_deleted_in_versions` before unlinking each extra (backs it up into the pre-restore snapshot, making the restore undoable); also unlinks the extra's `.txt` sidecar and thumbnail |

**Frontend**:
- `frontend/src/pages/VersionsPage.tsx` — route `/datasets/:datasetId/versions`, sidebar "Versions". Shows disabled-state when `versioning_mode="off"` (link to Settings). Otherwise shows branch selector, filter bar (debounced search + date range), version list with source badges (`Manual`/`Pre-restore`/`Branch init`) and pin icon per card. Pin toggle uses `setQueryData` optimistic update + client-side re-sort (no refetch). Active branch persisted to `sessionStorage` under `VERSIONS_BRANCH_KEY-${datasetId}`; falls back to `dataset.current_branch_id`, then `branches[0]`. `resolvedBranchId = activeBranch?.id` is passed to `BranchSelector` (not raw `activeBranchId`) so the dropdown stays in sync after restarts. A `useRef`+`useEffect` watches `dataset.current_branch_id` post-mount; the guard (`prev !== undefined`) prevents the initial data load from clobbering the stored preference.
- `frontend/src/components/versioning/CreateSnapshotModal.tsx` — name + description inputs; shows `JobProgressBar` during bg job; passes `activeBranchId` in the snapshot body so new snapshots land on the correct branch.
- `frontend/src/components/versioning/RestoreConfirmModal.tsx` — keep/remove radio for extra images, pre-restore snapshot checkbox, file-unavailability warning, `JobProgressBar`
- `frontend/src/components/versioning/DiffModal.tsx` — select two versions; shows Added/Removed/Modified sections with field-level changes
- `frontend/src/components/versioning/BranchSelector.tsx` — branch `<select>` + "New branch…" option. Checkout and branch creation show a `SnapshotPrompt` dialog first when `BRANCH_SNAPSHOT_KEY === "ask"`. Checkout triggers a bg job; a fixed-position progress card appears bottom-right; `onSelect` fires only after job completion to avoid stale data. For sync branch creation (≤100 images), `doCheckout(result.id, false)` is called immediately so `current_branch_id` updates — `pre_restore_snapshot=false` because the branch was just created. A trash icon opens a delete modal with its own `<select>` (excluding `currentBranchId`); the active branch cannot be deleted.
- `frontend/src/components/common/JobProgressBar.tsx` — shared progress bar (message + animated fill bar); used by snapshot, restore, and branch-checkout flows
- `frontend/src/api/versioning.ts` — `versioningApi`: `listBranches`, `createBranch(datasetId, name, fromVersionId?, includeSnapshot = true)`, `checkoutBranch(datasetId, branchId, preRestoreSnapshot = true)`, `deleteBranch(datasetId, branchId)`, `listVersions` (accepts `ListVersionsParams` object with `branchId`, `search`, `createdAfter`, `createdBefore`), `createSnapshot`, `getVersion`, `deleteVersion`, `updateVersion` (PATCH for `is_pinned`), `restoreVersion`, `diff`. `createSnapshot`/`createBranch` return `Version | { job_id: string }` — discriminate with `"job_id" in data`.
- **Sidebar version panel** (`SidebarVersionPanel.tsx`) — accordion below "Active dataset" label. Collapsed: branch name · head snapshot + chevron. Expanded: `<BranchSelector>`, the 7 most recent snapshots with `[Restore]` buttons (current shows "Now"), and "View all →" link. Snapshot query (`["versions", datasetId, activeBranch.id, "sidebar"]`) gated on `expanded` with `limit: 7`. `onSelect` writes `VERSIONS_BRANCH_KEY-${datasetId}` to sessionStorage; `onSuccess` after restore invalidates branches, dataset, images, captions, and versions queries. Only rendered when `activeBranch` is defined.

**TanStack Query keys**:
- `["branches", datasetId]` — invalidated after snapshot creation, restore, checkout
- `["versions", datasetId, resolvedBranchId, search, createdAfter, createdBefore]` — invalidated after snapshot creation, delete, restore; pin toggle uses `setQueryData` instead of invalidation
- `["images", datasetId]` — invalidated after restore and after checkout (image set and captions change)
- `["image"]` — prefix invalidation (no imageId) after restore and checkout; clears all cached image detail pages so `ImageDetailPage` refetches immediately
- `["caption"]` — prefix invalidation after restore and checkout; clears all cached caption data
- `["dataset", datasetId]` — invalidated after restore and checkout (image count, `current_branch_id`)
- `["dataset-stats", datasetId]`, `["tag-stats", datasetId]`, `["score-values", datasetId]`, `["tag-cooccurrence", datasetId]` — all four stats queries invalidated after restore (`VersionsPage` and `SidebarVersionPanel`) and after checkout (`BranchSelector`)

**`DatasetVersion` fields**: `id`, `dataset_id`, `branch_id`, `parent_id`, `name`, `description`, `image_count`, `created_at`, `source` (`Literal["manual", "pre_restore", "branch_init"]`), `is_pinned` (`bool`).
