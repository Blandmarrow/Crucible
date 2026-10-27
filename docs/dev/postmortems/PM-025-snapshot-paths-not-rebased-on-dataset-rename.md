# PM-025: snapshot paths were not rebased on a dataset rename

### Symptom

Restoring a snapshot taken before a dataset rename reported `completed` with no error and
wrote outside the dataset. Without name reuse, the restore re-created the dataset's
**old** folder (`{datasets_dir}/{old_slug}/images/`), put the image there, and left the
row pointing outside the dataset's `folder_path`. When a new dataset had since been
created under the old name, it was worse: that dataset's file was **overwritten** with
the restored bytes (Manual mode, object-store copy) or **moved over** (Auto mode, the
pure-rename path), with no backup anywhere — the pre-restore safety snapshot protects only
the dataset being restored. Both rows then named one path, `.../reused-name/images/x.png`.
`duplicate_dataset` from such a snapshot had the read-side twin: an Auto-mode row copied
another dataset's file into the clone.

Found by an external review (GPT-021, verified with `probe_versioning_021*.py`), not in
production. GPT-015 (rename and delete ignored the dataset-busy guard) fed the same hole
from the other side: a rename could land while a restore was running.

### Root cause

`VersionImageState.file_path` is **absolute** — the path the image had when the snapshot
was taken — and restore's Pass 0 uses it as the target verbatim. `rename_dataset` moved
the folder and rebased the live `Image` and `Video` rows, and its docstring said
"`.versions` needs nothing": true of the object store, which is derived from
`dataset.folder_path` at read time, and false of the snapshot rows, which are a third
table storing paths under the same root. Nothing at restore time checked that a target
lay inside the dataset it was being restored into, so a stale path was trusted as an
instruction to write.

### Generalizable rule

**Moving a root must rewrite every table that stores paths under it — history included.**
When reviewing any code that renames or moves a directory, enumerate every column in the
schema that holds a path (live rows, snapshot mirrors, job payloads, caches), not only the
ones the feature's own model owns. A docstring saying a table "needs nothing" is a claim to
verify against the schema, not against the code that happens to read it.

**And a path read from history is a record, not an instruction.** Before any write to a
path that came out of a snapshot, a log, or another stored record, check it against the
*current* root and against what other rows own it — before the first mutation, so a
refusal changes nothing. An absolute path is only as valid as the root it names.

### Why it wasn't caught the first time

The rename tests (`test_dataset_rename_folder_http.py`) asserted the live rows and never
took a snapshot, and the restore tests (`test_versioning_restore.py`) never renamed a
dataset. The interaction lived in the gap between two well-tested features. The earlier
fix that added `Video` rows to the rename was the moment to ask "what *else* stores a path
under this folder?", and the answer stopped at the two live tables. The busy-guard sweep
had the same shape of gap: it enumerated image and caption endpoints, and the
`datasets` router's own structural routes were never on the list.

### Fix

- `dataset_service.rename_dataset` rebases every `VersionImageState.file_path` of the
  dataset, with the same rule as the live rows, as a chunked bulk UPDATE issued *before*
  the folder rename and rolled back if the rename raises (PM-013).
- Migration `e3b7a9c2d4f1` repairs snapshots already stale, only where the rebase is
  provable; anything else is left with a warning.
- `version_service.restore_snapshot` gains Pass 0c, which refuses (`RestoreRefused`) a
  target outside the dataset folder, a moved source outside `datasets_dir`, or any path
  another dataset's row owns — before the pre-restore snapshot, so nothing changes.
- `duplicate_dataset`'s snapshot branch skips a lazy row whose recorded path is outside
  the source dataset.
- `routers/datasets.py`: rename, delete and subfolder delete call `ensure_not_busy`.

Guarded by `backend/tests/test_versioning_restore_after_rename_http.py`,
`backend/tests/test_dataset_rename_folder_http.py`,
`backend/tests/test_versioning_restore.py` (the gate),
`backend/tests/test_migration_rebase_snapshot_paths.py` and
`backend/tests/test_dataset_busy_routes_http.py`.

### Status & date

MITIGATED — the rename rebases, the migration repairs, and the gate refuses what remains.
Live rows already damaged by a pre-fix restore are refused rather than repaired; the
migration prints how many there are, and the repair (rescan or delete) is manual.
Last reviewed for staleness: 2026-10-27.
