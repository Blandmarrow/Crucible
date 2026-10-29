# Copy-on-write hooks

This file covers the two hooks other routers call before they overwrite or delete an image
file in place — `protect_file_before_overwrite` and `mark_image_deleted_in_versions` — the
containment enforced inside `_store_row_bytes` and at the call sites, the shared
`_backup_and_record_hash` helper, and the table of every site that fires them. Read it
before writing any path that overwrites or deletes an image file in place.

The CLAUDE.md Path traversal guard names both hooks by symbol. The object store they write
into is `docs/dev/versioning/storage.md`, and why they fire in every versioning mode is
`docs/dev/versioning/guards.md`; restore's Pass 1, the caller inside `version_service.py`
itself, is `docs/dev/versioning/restore.md`.

## The two hooks

- `protect_file_before_overwrite(image_id, file_path, db)` — COW hook; fires in **every** versioning mode, gated only on `_has_lazy_rows` — the image has a snapshot row with `file_hash IS NULL` — and never on the mode (GPT-022, PM-026). It only *flushes* the hash backfill — every caller that overwrites the file before its own final commit must `commit()` right after the hook (all current call sites do), otherwise a crash during the overwrite rolls the backfill back and the snapshot rows silently claim "content unchanged"
- `mark_image_deleted_in_versions(image_id, file_path, db)` — deletion hook; the same `_has_lazy_rows` gate, Off mode included (a fully hashed snapshot already holds the bytes, so it is a no-op there)

## Containment

- **Containment is enforced inside `_store_row_bytes`**, the single place a row's bytes enter `{ds}/.versions/objects/`: `_backup_and_record_hash` (and so both hooks) and Manual `create_snapshot`'s eager copy both route through it, so one guard covers every call site. The eager copy was the second entry and went ungated until PM-026 — an escaping symlink was copied in on every Manual snapshot; such a row is now skipped with its own `[warning]` line, which is a refusal and never counts as a backup failure. It is not just bookkeeping — an out-of-tree `file_path` is an arbitrary-file read primitive retrievable through a snapshot restore, even if the caller skipped the unlink. The base is the row's own `dataset.folder_path` rather than `settings.datasets_dir`, which is stricter and also catches a row pointing into another dataset; a refusal is a skip-and-log, so an out-of-tree row still completes its overwrite or deletion instead of failing the job. Because `within_datasets_dir` resolves, it catches the one live vector: a symlink placed in `{ds}/images/` that rescan registers under its in-tree path while `open()` follows it out of tree. `backend/tests/test_versioning_maintenance.py` pins the hook and the eager copy, the symlink case built rather than reasoned about.

  The **call-site** gates (`utils.contained_path`, per the CLAUDE.md Path traversal guard) are still the right shape and are defence in depth rather than redundancy: they also gate the `unlink`, and they let a destructive route drop the row for a path it refuses to touch. A destructive route that refuses a path must therefore skip the hook too, not only the `unlink`; and a site that fires *both* hooks must gate the whole loop, not the deletion branch alone. Two sites gated only their unlinks until 2026-07-31: `routers/filesystem.py::delete_path`'s row loop (reachable because the endpoint validates with `sanitize_abs_path`, which proves a path absolute, not contained) and `restore_snapshot`'s extras loop, whose `else` branch reaches the same primitive through `protect_file_before_overwrite`. Both are now gated per row and the row deletes stay unconditional. Tests: `backend/tests/test_path_containment_http.py` and `test_versioning_restore.py` — both need a lazy (NULL-hash) snapshot row, or the hook no-ops and the test proves nothing; the restore one needs **two** snapshots, since an extra added after the only snapshot has no `VersionImageState` row and the hook returns on its first query.

## The shared backup helper

- `_backup_and_record_hash(image_id, file_path, dataset_folder, db, precomputed_sha256=None)` — shared helper: store via `_store_row_bytes` → backfill all NULL `file_hash` rows for the image in one `UPDATE` with the hash of the bytes *actually stored*. `precomputed_sha256` is only a fast path: when its object already exists, all I/O is skipped (the content is safely stored — correct even if the file changed since the caller hashed it); otherwise the file is re-streamed through `_store_object`, so a stale caller hash can never poison the store or the state rows.

## Copy-on-write injection points

Existing routers, all fire before the file operation:

| File | Operation |
|---|---|
| `backend/routers/images.py` | `resize` endpoint, batch crop `_run`, batch resize `_run` — calls `protect_file_before_overwrite` |
| `backend/routers/images.py` | `crop` endpoint, replace=True branch — calls `protect_file_before_overwrite` |
| `backend/services/image_delete.py` | `unlink_image_files`, the files-first half of `images.delete_image`, `images.batch_delete`, `images.bulk_delete_filtered` and `quality.resolve_duplicates` — calls `mark_image_deleted_in_versions` before the unlink |
| `backend/routers/upscaling.py` | `_run` coroutine, replace=True branch — calls `protect_file_before_overwrite` |
| `backend/routers/detection.py` | `crop_to_detection` `_run` coroutine, replace=True branch — calls `protect_file_before_overwrite` |
| `backend/routers/lut.py` | `_run` coroutine, replace=True branch — calls `protect_file_before_overwrite` |
| `backend/routers/videos.py` | `_run_reextraction`'s per-frame `_rewrite` — calls `protect_file_before_overwrite` before swapping the full-resolution frame in. Unlike the rows above it verifies the new file re-opens *first*, so a frame that fails is never protected-then-not-overwritten (`docs/dev/video/reextract.md`) |
| `backend/routers/videos.py` | `_delete_previous_frames` — replace-mode extraction's step that clears a video's previous frames; calls `mark_image_deleted_in_versions` per row before the row delete and the unlink, inside the `busy(dataset_id, "Replacing extracted frames")` block (`docs/dev/video/extract.md`) |
| `backend/routers/filesystem.py` | `delete_path` (`POST /filesystem/delete`), both branches — calls `mark_image_deleted_in_versions` per `Image` row under the deleted file or folder, each gated by `utils.contained_path` (the row delete below it stays unconditional). This site must fire before the *row* delete as well as before the file operation: the hook loads the image with `db.get`, which after a staged Core delete autoflushes and returns None (PM-014) |
| `backend/routers/quality.py` | `resolve_duplicates`, delete branch — calls `mark_image_deleted_in_versions` per row, then `refresh_stats` per dataset |
| `backend/routers/images.py` | `batch_move_dataset` — calls `mark_image_deleted_in_versions` per moved image *before* the dataset_id updates (a cross-dataset move is a deletion from the source dataset's history; without the backup, pre-move snapshots could never materialize the image) |
| `backend/services/version_service.py` | `restore_snapshot`, Pass 1 — calls `protect_file_before_overwrite` (gated: only files that will actually be overwritten/moved/deleted, hash passed as `precomputed_sha256`) before any file operation, backing current content into the pre-restore snapshot so the restore is undoable in auto mode |
| `backend/services/version_service.py` | `restore_snapshot`'s extras loop in Pass 1 (before any FS write) — `mark_image_deleted_in_versions` per extra in `"remove"` mode, `protect_file_before_overwrite` for a `"keep"`-mode extra occupying a restore target. `utils.contained_path` gates the **whole loop**, not just the `"remove"` branch: both hooks end at `_store_object` and are the same read primitive. Files + `.txt` sidecars + thumbnails are unlinked later in Pass 3c via `_remove_stale_files` in an executor, which gates containment **inside** the helper (V-83): it has five call sites and this is the riskiest, handing over an arbitrary row's stored `file_path`/`thumbnail_path` untouched. `contained_path` never raises, which matters — that helper's body sits in a bare `except Exception: pass` that would swallow it silently |
