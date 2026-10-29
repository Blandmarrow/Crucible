# Versioning guards

This file covers the gates around versioning: the three versioning modes and what each one
switches, why copy-on-write protection is gated on the data rather than the mode, the
in-process dataset-busy guard and the routes that call it, and the structural dataset routes
guarded since GPT-015.

The hooks the modes gate are `docs/dev/versioning/hooks.md`; the routes
`_require_versioning_enabled` refuses are `docs/dev/versioning/routes.md`.

## Versioning modes

**Versioning mode** — stored in `threshold_settings.versioning_mode` (same singleton row as quality thresholds, same `GET/PATCH /api/v1/settings/thresholds` endpoints). Three values:

| Mode | Snapshot behaviour | COW overwrite hook |
|---|---|---|
| `"off"` | Disabled — every *write* route 400s via `_require_versioning_enabled` (snapshot, prune, branch create/delete/checkout, version update/delete, restore). The four read routes still answer, deliberately: `Sidebar` and `DatasetsPage` list branches and versions whatever the mode is | Fires for existing lazy rows |
| `"manual"` | Snapshot copies every file to the object store eagerly (full point-in-time backup). Always runs as a background job. | Fires for existing lazy rows (none, unless an Auto snapshot or a failed copy left some) |
| `"auto"` | Snapshot records metadata + `file_hash=NULL`; object store copies are made lazily on first overwrite (copy-on-write). | Fires before every in-place overwrite |

**Protection is gated on the data, not the mode** (GPT-022, PM-026). Both hooks — overwrite and deletion — fire in every mode whenever the image has a snapshot row with `file_hash IS NULL` (`_has_lazy_rows`, one indexed `LIMIT 1` query), and back the file up first. The mode only decides how a *new* snapshot is taken. Gating on the mode let an Auto snapshot's lazy rows go unprotected after a switch to Manual or Off, and a restore then read the overwritten file as "unchanged". The cost: Off mode keeps spending disk on snapshots that already exist, and an overwrite in Manual or Off can now fail when the object store cannot be written, as it always could in Auto.

## Dataset-busy guard

**Dataset-busy guard** — `backend/services/dataset_busy.py`, a small in-process module (single-process app): `busy(dataset_id, reason)` contextmanager + `ensure_not_busy(dataset_id)` which raises HTTP 409. The five versioning job `_run` wrappers in `routers/versioning.py` (restore, checkout, background snapshot, background branch-create, prune) hold the flag for the job's duration, and they are no longer the only holders: `routers/videos.py`'s `_delete_previous_frames` takes it around the replace step alone, because that step deletes N rows, N files and N thumbnails in seconds — extraction as a whole deliberately does not hold it (`docs/dev/video/extract.md`). Interactive *direct-mutation* endpoints call `ensure_not_busy` after resolving the dataset id — `routers/images.py` (upload, delete/batch-delete/bulk-delete, bulk-rename, rename, resize, crop, batch move-subfolder, batch move-dataset + copy-dataset — both source and target, plus `bulk_provenance`, single-image provenance, `bulk_thumbnails` — which guards every dataset its rows touch — and `_guard_batch_datasets`, the shared guard `/batch/resize` and `/batch/crop` acquired with PM-018), `routers/quality.py` (`resolve_duplicates`), `routers/captions.py` (single caption save, find-replace, bulk-edit) — and so do the video and filesystem routers, whose own docs own the detail: `routers/videos.py` (extract, re-extract, rename, delete — `docs/dev/video/extract.md`, `docs/dev/video/reextract-endpoints.md`, `docs/dev/video/reextract.md`, `docs/dev/video/endpoints.md`) and `routers/filesystem.py` (rename, delete — `docs/dev/file-browser-rename-delete.md`). Treat the list as "where to look", not as exhaustive; grep `ensure_not_busy` for the current set. Enqueued background jobs are deliberately *not* guarded — the single job queue already serializes them against versioning jobs. No frontend change: the 409 surfaces through the normal error toast.

**The structural dataset routes are guarded too** (GPT-015) — `routers/datasets.py`: `update_dataset` when the **name** changes (the rename moves the folder and rebases every stored path under it, which a running restore is still writing to — the race that fed GPT-021), `delete_dataset` after its 404 check, and `remove_subfolder` (it rewrites `Image.subfolder`, the column `repath_subfolder_endpoint` already guards for). A category- or provenance-only PATCH stays unguarded on purpose: `DatasetsPage` sends those in bulk and nothing on disk moves. `DatasetsPage`'s delete mutation toasts the 409 rather than failing silently. `backend/tests/test_dataset_busy_routes_http.py` pins all four.
