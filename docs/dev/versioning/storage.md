# Snapshot storage

This file covers what a snapshot is made of on disk and in the database: the
content-addressed object store and its GC, the `is_present` invariant, the three versioning
tables, why `VersionImageState.file_path` is absolute and what keeps it honest, the
`DatasetVersion` fields and cascades, and the two functions that create and delete versions —
`create_snapshot` and the version delete's head reassignment
(`routers/versioning.py::delete_version`).

Which columns a `VersionImageState` row mirrors, and how two versions are diffed, is
`docs/dev/versioning/mirrors.md`; the hooks that fill the object store lazily are
`docs/dev/versioning/hooks.md`.

## Object store and GC

**Object store** — content-addressable, git-style:
`{dataset.folder_path}/.versions/objects/{sha256[:2]}/{sha256[2:]}`

Files are stored **only once per unique content** (idempotent). All writes go through `_store_object(dataset_folder, src_path) -> sha256`: it streams the file once, hashing *while* writing to an `objects/.tmp-{uuid}` temp file (same filesystem), then atomically `os.replace`s it to the hash path — so the stored bytes and their address can never disagree, even if the source file is overwritten mid-copy. This closes the hash-then-copy TOCTOU that could permanently poison a content-addressed entry (realistic race: a manual-mode snapshot job vs. an interactive replace endpoint). Never add a separate "hash, then copy" path.

**GC** — `prune_object_store(db, dataset_id, job_id=None, min_age_seconds=3600)` deletes objects no `VersionImageState.file_hash` of the dataset references, plus stale `.tmp-*` leftovers, and returns `{objects_deleted, objects_kept, bytes_freed}`. Files younger than `min_age_seconds` are always kept (a COW write racing the reference scan may have stored an object whose state row isn't committed yet — belt-and-suspenders on top of the busy flag). Objects are per-dataset (the store lives under the dataset folder), so cross-dataset references are impossible. User-triggered via `POST /{id}/versions/prune` (`docs/dev/versioning/routes.md`) and the "Prune storage" button on `VersionsPage`.

## Tables

**`is_present` invariant**: A `VersionImageState` row always has `is_present=True` — it records that the image was present at snapshot time. When an image is deleted, post-deletion snapshots simply have no row for it. This means restoring a pre-deletion snapshot correctly re-creates the image from the object store (the deletion hook backs up the file before it is unlinked). Do not retroactively set `is_present=False` on old rows.

**DB tables** (`backend/models/versioning.py`):

| Table | Purpose |
|---|---|
| `dataset_branches` | Named branches; `head_version_id` FK to latest snapshot on the branch |
| `dataset_versions` | Snapshot records; `parent_id` self-ref for chain; auto-named `Snapshot YYYY-MM-DD HH:MM` if name omitted |
| `version_image_states` | One row per image per snapshot — stores all metadata + `file_hash` (SHA-256; NULL until COW fills it in `"auto"` mode) + `processing_history` (JSON array of replace operations) + `sort_order` (custom gallery position; NULL if image was unordered at snapshot time) + the five provenance columns (see **Provenance mirror** in `docs/dev/versioning/mirrors.md`) + all ten `*_score` columns + the three frame-lineage columns (`source_video_id`, `source_timestamp_ms`, `source_shot_index`) + the two join-table mirrors, `label_ids` (NOT NULL, `'[]'`) and `detections` (nullable: NULL = recorded before the column existed, which a restore leaves alone) — the rules behind each group are `docs/dev/versioning/mirrors.md` |

**`VersionImageState.file_path` is absolute** — the path the image had when the snapshot was taken, and the path a restore writes to. Three things keep it honest, because a path read from history is only as good as the root it names (PM-025):
- `dataset_service.rename_dataset` rebases every snapshot row of the dataset with the same unresolved `is_relative_to` rule as the live rows (restore's Pass 0 compares the two as raw strings), as a chunked bulk UPDATE issued *before* the folder rename and rolled back if it raises (PM-013). Before this, a pre-rename snapshot restored into the old root, and over another dataset's files once one reused the name (GPT-021).
- Migration `e3b7a9c2d4f1` repaired the snapshots already stale: per version, only when every stray row is provably `<parent>/<old_slug>/images/.../<filename>` with one shared old slug and no collision; anything else is left with a WARNING. It also prints a count of live rows outside their dataset folder and repairs none of them (no file moves in a migration).
- Restore's Pass 0c gate refuses whatever still escapes, before anything is written (`docs/dev/versioning/restore.md`).

`datasets.current_branch_id` — tracks the active branch (updated on checkout).

**`DatasetVersion` fields**: `id`, `dataset_id`, `branch_id`, `parent_id`, `name`, `description`, `image_count`, `created_at`, `source` (`Literal["manual", "pre_restore", "branch_init"]`), `is_pinned` (`bool`).

**`passive_deletes`**: `DatasetVersion.image_states` sets `passive_deletes=True` — `version_image_states.version_id` has `ondelete="CASCADE"` and `PRAGMA foreign_keys=ON` is set per connection in `backend/database.py`, so the DB deletes state rows and the ORM no longer loads thousands of them per version delete. `DatasetBranch.versions` deliberately does **not**: its FK is `ondelete="SET NULL"`, so the ORM cascade must keep deleting version rows itself (each of those deletes now skips loading states, which is the actual win).

## Creating and deleting versions

- `create_snapshot(db, dataset_id, name, description, branch_id, parent_id, job_id, source)` — creates snapshot; an explicit `branch_id` must belong to the dataset (`ValueError` → 404 otherwise, GPT-024), and `create_branch`/`restore_snapshot`/`diff_versions`/`checkout_branch` check their version ids the same way. `parent_id` is not vestigial: `create_branch` passes `parent_id=from_version_id` so a branch-init snapshot's parent is the source branch's head. `source` is `"manual"` (user-triggered), `"pre_restore"` (auto before restore), or `"branch_init"` (new branch). When `branch_id` is omitted it resolves to the dataset's **current branch** (`dataset.current_branch_id`), falling back to `main` only for never-branched datasets — so pre-restore/checkout auto-snapshots land on the branch the user is actually on and never move `main`'s head. `"manual"` mode also copies every file eagerly; per-file backup failures are counted, appended as a `[warning]` line to the version description, and emitted in the job progress (a NULL hash would otherwise restore as "content unchanged" with no trace). `create_branch` resolves its `from_version_id` default from the current branch's head the same way.
- `DELETE /{id}/versions/{version_id}` head reassignment: when the deleted version is the branch head, the head moves to the version's **parent** if it exists on the same branch, else to the newest remaining version on the branch. Parent-first matters after a restore: the newest-by-`created_at` version is then the pre-restore auto-snapshot — exactly the state the user restored away from.
