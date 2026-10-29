# Versioning tests

This file lists the backend test files that cover versioning and what each one pins. Tests
that guard one rule are also named beside that rule — in `docs/dev/versioning/mirrors.md`,
`docs/dev/versioning/hooks.md`, `docs/dev/versioning/guards.md`
(`test_dataset_busy_routes_http.py`) and `docs/dev/versioning/restore.md` (the
`duplicate_dataset` path check); restore's pass structure, which most of these exercise, is
`docs/dev/versioning/restore.md`.

## Where each part is pinned

**Regression tests**: `backend/tests/test_versioning_restore.py` exercises the real service against a scratch DB + files (via `asyncio.run`, no async pytest plugin): filename swaps, deleted-image name reuse (keep/remove), pre-restore snapshot branch placement, cross-dataset-move restore (fork + adopt), abort-on-failed-pre-snapshot, and the Pass 0c path gate (an out-of-dataset target, a path another dataset owns, a refused checkout staying on its branch). Extend it when touching restore's pass structure.

**Rename-then-restore tests**: `backend/tests/test_versioning_restore_after_rename_http.py` drives the GPT-021 scenario end to end — snapshot, rename, a new dataset reusing the old name, restore — in both modes, plus a legacy un-rebased snapshot that must be refused with nothing changed; `backend/tests/test_dataset_rename_folder_http.py` pins the rebase itself and its rollback, and `backend/tests/test_migration_rebase_snapshot_paths.py` the migration's planner.

**PM-026's fixes** each have their own file: `test_versioning_ownership_http.py` (foreign ids, GPT-024), `test_versioning_protection.py` (lazy rows protected in every mode, GPT-022), `test_versioning_pre_restore_complete.py` (the complete-or-abort safety snapshot and the Pass 1 abort, GPT-023), `test_restore_outcome_http.py` (failure accounting, reconciliation and `result_data`, GPT-025) and `test_restore_measures_pixels.py` (measured phash, GPT-016).

`backend/tests/test_versioning_maintenance.py` (the same harness as `test_versioning_restore.py`) covers `_store_object` atomicity + the stale-`precomputed_sha256` TOCTOU regression, prune (referenced kept, junk/`.tmp-*` deleted, `min_age_seconds` skip), the busy guard, and the version-delete DB cascade.
