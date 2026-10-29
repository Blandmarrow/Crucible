# Dataset versioning — snapshots, restore, hooks and the diff

Snapshot-based version control for datasets. Users can create named snapshots, restore to
any prior state, compare two snapshots (diff), and maintain named branches; every router that
overwrites or deletes an image file in place calls into it first. This file is the family's
entry point: the tables below route from a code path to whichever file in
`docs/dev/versioning/` owns it. The subsystems whose data a snapshot carries keep their own
docs — `docs/dev/provenance.md`, `docs/dev/label-consumers.md`, `docs/dev/detection.md`.

## The family

| File | Covers |
|---|---|
| [guards.md](versioning/guards.md) | The three versioning modes, protection gated on the data rather than the mode, `dataset_busy` and the routes that call `ensure_not_busy`, the structural dataset routes |
| [routes.md](versioning/routes.md) | The `/datasets/{id}/versions` route table, which routes run as background jobs, the id-ownership 404 |
| [storage.md](versioning/storage.md) | The object store and `_store_object`, prune/GC, the three tables, the `is_present` invariant, absolute snapshot paths and their rebase, `DatasetVersion` and `passive_deletes`, `create_snapshot`, the version delete's head reassignment |
| [hooks.md](versioning/hooks.md) | `protect_file_before_overwrite`, `mark_image_deleted_in_versions`, `_store_row_bytes` containment and the call-site gates, `_backup_and_record_hash`, the table of every site that fires a hook |
| [restore.md](versioning/restore.md) | `restore_snapshot`'s four passes, the complete-or-abort safety snapshot, Pass 3's failure contract and `result_data`, phash and geometry measured rather than mirrored (GPT-016), `duplicate_dataset`'s read-side twin |
| [mirrors.md](versioning/mirrors.md) | What `VersionImageState` mirrors and why — scores, lineage, `label_ids`, `detections`, `scores_stale`, `aesthetic_model`, provenance — and `diff_versions` |
| [frontend.md](versioning/frontend.md) | `VersionsPage`, the snapshot/restore/diff modals, `BranchSelector`, `versioningApi`, the sidebar panel, the query keys |
| [tests.md](versioning/tests.md) | Which backend test file pins which part |

## Where things live

Each row names the file that **owns** that code. A `+` clause is a second genuine owner,
not a see-also, and says which part of the code it owns.

### Backend

| Editing… | Read |
|---|---|
| `backend/services/version_service.py` | `docs/dev/versioning/hooks.md` for the two hooks, `_store_row_bytes` and `_backup_and_record_hash`; + `docs/dev/versioning/storage.md` for `_store_object`, `prune_object_store` and `create_snapshot`; + `docs/dev/versioning/restore.md` for `restore_snapshot` and `checkout_branch`'s outcome; + `docs/dev/versioning/mirrors.md` for `diff_versions`, `_DIFF_COLS` and `_DIFF_COMPARE_FIELDS` |
| `backend/routers/versioning.py` | `docs/dev/versioning/routes.md`; + `docs/dev/versioning/guards.md` for `_require_versioning_enabled` and the `busy` job wrappers; + `docs/dev/versioning/storage.md` for `delete_version`'s head reassignment; + `docs/dev/versioning/restore.md` for `_store_result` |
| `backend/services/dataset_busy.py` | `docs/dev/versioning/guards.md` |
| `backend/models/versioning.py` | `docs/dev/versioning/storage.md`; + `docs/dev/versioning/mirrors.md` for what each `VersionImageState` mirror column means |
| `backend/services/dataset_service.py` (`rename_dataset`'s snapshot rebase, `duplicate_dataset`'s snapshot branch) | `docs/dev/versioning/storage.md` for the rebase; + `docs/dev/versioning/restore.md` for the duplicate's path check |
| Any route that overwrites or deletes an image file in place — `backend/routers/images.py`, `backend/services/image_delete.py`, `backend/routers/upscaling.py`, `backend/routers/lut.py`, `backend/routers/detection.py`, `backend/routers/videos.py`, `backend/routers/filesystem.py`, `backend/routers/quality.py` | `docs/dev/versioning/hooks.md`; + `docs/dev/versioning/guards.md` for the `ensure_not_busy` call a direct-mutation route makes (`backend/routers/captions.py` too) |
| `backend/routers/datasets.py` (rename, delete, `remove_subfolder`) | `docs/dev/versioning/guards.md` for the busy guard on each |
| `backend/services/label_service.py`, `backend/services/detection_service.py` (their snapshot helpers) | `docs/dev/versioning/mirrors.md` |
| `backend/tests/test_versioning_restore.py` and the other versioning and restore test files | `docs/dev/versioning/tests.md` |

### Frontend

| Editing… | Read |
|---|---|
| `frontend/src/pages/VersionsPage.tsx` | `docs/dev/versioning/frontend.md` |
| `frontend/src/components/versioning/` (the modals, `BranchSelector`, `SidebarVersionPanel`) | `docs/dev/versioning/frontend.md`; + `docs/dev/versioning/mirrors.md` for how `DiffModal` renders each diff field |
| `frontend/src/api/versioning.ts` | `docs/dev/versioning/frontend.md` |
| `frontend/src/components/common/JobProgressBar.tsx` | `docs/dev/versioning/frontend.md` |
