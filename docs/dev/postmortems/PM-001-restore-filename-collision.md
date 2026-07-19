# PM-001: Restore aborted on filename swaps — UNIQUE constraint failed

### Symptom

Restoring a snapshot after images had swapped or chained filenames (e.g. `1.png` ↔
`2.png`, or bulk-rename renumbering) aborted the whole restore with
`Restore failed: UNIQUE constraint failed: images.dataset_id, images.filename`.
The same error hit when a deleted image's old filename had since been taken by a
newer image — the re-creation INSERT collided and the snapshot could never be
restored. Caught in a code review plus a repro harness (scratch-DB scenario tests),
not in production.

### Root cause

SQLite checks unique constraints **per statement**, not per transaction. Restore's
Pass 2 flushed all final `filename`/`file_path` values in one go; any permutation of
names under `uq_dataset_filename` (swap, renumber chain, deleted-name reuse) meant
some intermediate UPDATE or INSERT momentarily produced a duplicate name and the
whole flush raised IntegrityError.

### Generalizable rule

Flag any batch write that *permutes* values under a unique constraint (renames,
reorders of unique keys, re-inserting a row whose key another row now holds). It
needs staged updates: park rows on unique temp values first, or order the flushes so
deletes/clears land before inserts/finals. "The end state is collision-free" is not
enough — every intermediate statement must be too.

### Why it wasn't caught the first time

Restore was only tested with disjoint before/after name sets; no test performed a
swap or reused a deleted image's name before restoring. The review question "can two
rows exchange unique values in this batch?" was never asked.

### Fix

Restore's Pass 2 is now staged in three flushes: 2a clears extras off restored
names (delete or rename-aside), 2b parks every renamed image on a unique
`__dbtmp_{id8}` temp filename, 2c applies final values + re-creation INSERTs
collision-free. Regression tests: `backend/tests/test_versioning_restore.py`
(`test_restore_filename_swap`, `test_restore_name_reuse_*`).

### Status & date

MITIGATED. Last reviewed for staleness: 2026-07-19.
