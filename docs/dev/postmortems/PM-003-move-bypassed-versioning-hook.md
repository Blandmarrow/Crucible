# PM-003: Cross-dataset move bypassed the versioning deletion hook

### Symptom

After moving images to another dataset with batch move, restoring a pre-move
snapshot in the source dataset crashed with a PK IntegrityError (the snapshot tried
to re-insert the moved image's ID, which still exists — in the other dataset). And
because no backup was taken at move time, snapshots could not materialize the moved
image's file at all: its content was simply gone from the source dataset's object
store. Caught in a code review plus a repro harness, not in production.

### Root cause

The versioning deletion hook (`mark_image_deleted_in_versions`) only fired on
literal deletes. A cross-dataset move is not a delete — the row survives with a new
`dataset_id` — but from the *source dataset's* history it is exactly a deletion:
the image leaves the dataset's scope and its file leaves the dataset folder. The
hook taxonomy was keyed on the DB operation, not on dataset scope.

### Generalizable rule

Flag any code path that removes an image from a dataset's *scope* — moves and
re-parenting included, not just row deletes — that does not fire the versioning
deletion hook before the file leaves the dataset folder. If a pre-change snapshot
could reference the file, the file must be backed up before the change, whatever
the DB verb is.

### Why it wasn't caught the first time

The hook audit checklist enumerated delete endpoints only; batch move was reviewed
as "an update, not a delete". No test restored a snapshot taken before a
cross-dataset move.

### Fix

`batch_move_dataset` now calls `mark_image_deleted_in_versions` per moved image
before the `dataset_id` updates, and restore's Pass 0b handles the surviving
foreign ID (adopt matching content or fork a fresh ID). Regression test:
`test_restore_after_cross_dataset_move` in
`backend/tests/test_versioning_restore.py`.

### Status & date

MITIGATED. See PM-014 for the recurrence at `/filesystem/delete`. Last reviewed for
staleness: 2026-07-29.
