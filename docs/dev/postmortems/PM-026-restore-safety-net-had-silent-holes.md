# PM-026: the restore safety net had silent holes

### Symptom

A snapshot restore reported success while the database and the pixels disagreed. Five
routes led there, all found by an external review (GPT-025, 022, 023, 024 and the restore
half of 016, verified with `probe_versioning.py`), none in production:

- **A failed copy was reported as a success.** The job ended `completed` with
  `result_data={}`, and the modal toasted "Restore complete". The service had counted
  `files_failed=1`, and the router threw the count away. The row said 100×80 while the file
  stayed 40×40. Because `shutil.copy2` truncated the target first, a copy that failed half
  way could leave no usable pixels at all.
- **Switching Auto → Manual (or Off) left Auto snapshots unprotected.** A replace-crop
  overwrote the file with no backup, and restoring the Auto snapshot then reported
  0 failed and 0 unavailable over the cropped pixels.
- **The pre-restore safety snapshot was committed with holes.** When a per-file backup
  failed, the snapshot recorded a NULL hash and a `[warning]` line, and the restore went
  ahead and overwrote the file anyway. Undoing it through the safety version put the
  edited image's metadata over the original's pixels, again with 0 failed.
- **A file that failed to hash skipped its backup in Pass 1**, and Pass 3 overwrote it
  regardless.
- **A restore left a stale or NULL `phash`.** The re-created row dropped out of duplicate
  scans.

Alongside these, `POST /datasets/{A}/versions` accepted dataset B's `branch_id` and moved
B's branch head, and Manual `create_snapshot` copied an escaping symlink's target into the
object store, which is an arbitrary-file read primitive.

### Root cause

`VersionImageState.file_hash IS NULL` means two different things: **"unchanged since the
snapshot"**, which is the Auto-mode copy-on-write contract, and **"never backed up"**. Restore can
only read the first meaning. Three writers left NULLs that meant the second:

- the overwrite hook, gated on the *current* mode rather than on whether a lazy row
  existed;
- a Manual or pre-restore backup that failed and was recorded anyway;
- Pass 1 skipping protection when it could not compute a hash.

Each of these made a sentinel false without filling or refusing it, so restore read
"unchanged" over changed pixels. The outcome reporting then lost the failures restore *could*
see. That is PM-023's mechanism again: a per-item loop counts its failures into locals and
returns normally, and the router drops the return value.

### Generalizable rule

**A "nothing changed" sentinel must be filled or refused by every path that can make it
false, and the gate must be the data, never the configuration.** When a NULL (or a default)
stands for "unchanged since X", ask of every write to the underlying thing whether it fills
the sentinel first. A mode, a flag or a setting is the wrong gate, because configuration
can change after the sentinel was written, and the sentinel outlives the setting that
created it. And a backup that has to be complete must refuse rather than record a hole:
"committed with a warning" is a NULL with a comment attached.

Plus PM-023's rule, applied to restore: **a job that loses items must say so in
`result_data`**, and the UI must read it. A status of `completed` alone is not an outcome.

### Why it wasn't caught the first time

Every restore test ran in a single mode from start to finish, so no test switched modes
between the snapshot and the edit, which is the only way the mode gate could show. The
backup-failure path was tested for its `[warning]` line, the visible half, and never
followed through a restore of the safety version. The restore tests asserted
`files_failed` on the service's return value and never looked at the job row the user
sees. The review question that would have caught all three: *for each place this NULL is
written, what does restore do when it reads it?*

### Fix

All eight commits are on branch `fix/gpt-review`.

- **Ownership (GPT-024).** Every service function refuses a branch or version id from
  another dataset, and the routes answer 404 before creating a job.
- **Hooks (GPT-022).** Both copy-on-write hooks are gated on `_has_lazy_rows` in every mode.
- **Complete-or-abort safety snapshot (GPT-023).** `create_snapshot(require_complete=True)`
  rolls back and raises `SnapshotIncomplete` for the pre-restore snapshot.
- **Single entry to `objects/`.** `_store_row_bytes` is now the only place bytes enter the
  object store, so the Manual eager copy passes through the containment gate.
- **Pass 1 (new gap).** Pass 1 protects a file even when hashing it fails, and any backup
  failure aborts the restore before a file changes. The abort also discards the safety
  snapshot it had already committed.
- **Pass 3 (GPT-025):**
  - writes atomically, with a temp file in `objects/` and `replace_retrying`;
  - counts and names every failure;
  - reconciles rows whose content was not written: re-created rows are deleted, and other
    rows get their pre-restore pixel values back and are re-measured;
  - returns `failed_count`, `failure_summary` and `failed`, which both routes write to
    `result_data`. `showRestoreSummaryToast` warns from it.
- **phash (GPT-016).** Restore measures phash, size, format and geometry from the file it
  wrote, rather than mirroring them.

Guarded by:

- `backend/tests/test_versioning_ownership_http.py`
- `backend/tests/test_versioning_protection.py`
- `backend/tests/test_versioning_pre_restore_complete.py`
- `backend/tests/test_restore_outcome_http.py`
- `backend/tests/test_restore_measures_pixels.py`
- the symlink case in `backend/tests/test_versioning_maintenance.py`

### Status & date

MITIGATED. Three holes remain, documented in `docs/dev/versioning/restore.md`:

- A staging failure is counted but not made safe.
- A keep-mode extra whose rename-aside fails can be overwritten when no snapshot row covers
  it.
- `_remove_stale_files` still swallows its errors.

Last reviewed for staleness: 2026-10-27.
