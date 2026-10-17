# Versioning findings

These are the verified findings for snapshots, restore and branches. For the subsystem itself, read
`docs/dev/versioning.md` and `docs/dev/versioning-service.md` first. The probes are in
`docs/dev/gpt-review/probes/backend/probe_versioning*.py`, and the conventions are in `docs/dev/gpt-review/README.md`.

Several of these findings compound each other:
- 015 (rename during a restore) feeds 021.
- 021, 022 and 023 all end as a "completed" restore because of 025.

So fix them as one branch.

## GPT-021 — [P1] Restoring after a dataset rename writes outside the dataset, and can overwrite another dataset's file

**Verdict:** CONFIRMED. The real scope is **wider than claimed**. This was not documented anywhere.

**Mechanism.**
- Snapshots store **absolute** paths: `VersionImageState.file_path`, written in `create_snapshot`.
- `dataset_service.rename_dataset` rebases the live `Image` and `Video` paths but never touches
  `VersionImageState`.
- In `restore_snapshot`:
  - Pass 0 sets `target_path = state.file_path` (~`version_service.py:753`).
  - Pass 2c writes `img.file_path = p.target_path` and commits (~:1055; GPT cited :980).
  - Pass 3b copies or moves the bytes there (~:1151).
- Nothing checks that the target lies under the dataset's current `folder_path`.

**Observed, with name reuse** (`probe_versioning_021.py::test_gpt021_name_reuse_overwrites_B`):
1. Dataset A was named `reused-name`. A snapshot was taken (Manual mode), then A was renamed to
   `new-root-name`.
2. Dataset B was created as `reused-name` with its own `x.png`.
3. A's snapshot was restored with `pre_restore_snapshot=true`.

The results:
- The job reported `completed` with no error.
- B's file sha256 changed to A's original bytes. B has no backup anywhere; the safety snapshot
  only protects A.
- Both rows now name `.../reused-name/images/x.png`.

Auto mode clobbers B as well, through the pure-rename move path
(`probe_versioning_021_auto.py::test_gpt021_auto_mode_name_reuse`).

**Observed, without name reuse** (`test_gpt021_control_no_reuse`): B is untouched, but restore
`mkdir`s the old root again and puts A's image there. A's row now points outside A's
`folder_path`.

**Any rename followed by a restore of a pre-rename snapshot hits this.** That variant alone is a
P2 path escape, and it is easy to reach from the UI.

**Fix notes.**
- Store snapshot paths relative to the dataset root, or rebase them on read against the current
  `folder_path`. This needs a migration or compatibility read for existing snapshots.
  `dataset_service.rename_dataset` already rebases the live paths, so it is the place to also
  rebase `VersionImageState`.
- Before any write, refuse a target outside the dataset's root, or one owned by another dataset's
  row, and do so before touching any file.
- Regression: both probes, inverted.
- Check `Video` paths in snapshots, if any, under the same rule.

## GPT-025 — [P2] Restore failures are discarded and the job reports complete

**Verdict:** CONFIRMED. NEW.

**Where.** `restore_snapshot` counts `files_failed` and `files_unavailable` and returns them
(~`version_service.py:1277-1288`). The router's `_run` closure (`routers/versioning.py:396-405`)
drops that return value. The job therefore ends `completed` with `result_data={}`, and
`frontend/src/components/versioning/RestoreConfirmModal.tsx` toasts "Restore complete". Nothing in
the frontend reads `files_failed`.

**Observed.** The probes are `probe_versioning.py::test_gpt025_failed_copy_job_still_completed`
and `test_gpt025_service_returns_files_failed`. Only the object→target `shutil.copy2` was
injected to fail. The service returned `files_failed=1`, while the job showed `completed` with a
NULL `error_msg`. The DB says 100×80 and the file on disk is 40×40. A missing object behaves the
same way, counted as `files_unavailable`.

**Fix notes.**
- Persist the summary in `result_data`, and give the job a partial or failed outcome.
- Surface the outcome in the modal.
- For each failed image, reconcile its DB metadata with the pixels that survived, while keeping
  the deliberate DB-before-filesystem ordering.
- Checkout reuses `restore_snapshot`, so it needs the same treatment.

## GPT-022 — [P2] Switching Auto → Manual leaves earlier lazy snapshots unprotected

**Verdict:** CONFIRMED. NEW: `docs/dev/versioning.md` describes the modes but not this hazard.

**Mechanism.**
- Auto snapshots are copy-on-write, with `file_hash=NULL` until the file is first overwritten.
- `protect_file_before_overwrite` returns early outside Auto (~`version_service.py:244`).
- `PATCH /settings/thresholds` (`routers/settings.py:70-83`) just flips the mode.
- Restore then reads a NULL hash as "unchanged".

**Observed** (`test_gpt022_auto_to_manual_unprotected`, control `test_gpt022_control_stay_auto`):
1. Take an Auto snapshot of a 100×80 image.
2. Switch to Manual.
3. Replace-crop the image to 40×40.
4. Restore the snapshot.

The restore reports 0 failed and 0 unavailable. The DB says 100×80, while the pixels stay 40×40.
In the control the mode stays Auto, and the same restore brings the file back correctly.

**Fix notes.** Either protect lazy references regardless of the current mode, or materialize every
outstanding NULL-hash snapshot before leaving Auto. If materialization fails, refuse the mode
change.

## GPT-023 — [P2] A failed per-file safety backup does not abort restore

**Verdict:** CONFIRMED. Partly known: `docs/dev/versioning-service.md` documents the `[warning]`
line on a backup failure, but not that restore then proceeds anyway.

**Mechanism.** Manual `create_snapshot` catches each per-file backup failure and still commits the
version, with a NULL hash and a warning in its description. The pre-restore gate
(~`version_service.py:696-716`) aborts only if `create_snapshot` *raises*.

**Observed** (`test_gpt023_failed_backup_does_not_abort_restore`, with `_store_object` stubbed to
raise once): the restore went ahead and overwrote the edited 40×40 file. The safety version holds
no copy of that 40×40 file, so restoring the safety version gives DB 40×40 over pixels at 100×80,
with 0 failed reported.

**Fix notes.** Make the pre-restore snapshot "complete or abort": a required file that was not
backed up stops the restore before any mutation. Leave Manual-snapshot warnings as they are for
user-initiated snapshots.

## GPT-015 — [P2] Dataset rename and delete bypass the busy guard

**Verdict:** CONFIRMED, and wider than claimed: DELETE is affected too. NEW. `docs/dev/versioning.md`
says to "grep `ensure_not_busy` for the current set" and does not list this gap.

**Observed** (`test_gpt015_rename_and_delete_bypass_busy`, holding
`dataset_busy.busy(id, "restore")`):
- `DELETE /images/{id}` → 409. This is the control.
- `PATCH /datasets/{id}` (rename) → 200, and the folder moved.
- `DELETE /datasets/{id}` → 204.

The only `ensure_not_busy` call in `routers/datasets.py` is in the subfolder-rename endpoint.

**Fix notes.** Add the guard to `update_dataset` (at least when the name or folder changes) and to
`delete_dataset`. Then audit every other structural route.

## GPT-024 — [P3, GPT said P2] Snapshot creation accepts another dataset's branch

**Verdict:** CONFIRMED. NEW. Downgraded because the UI never sends a foreign `branch_id`.

**Observed** (`test_gpt024_foreign_branch`): POST `/datasets/{A}/versions` with B's `branch_id`
returns 200. The new version has `dataset_id=A` and `branch_id=B's branch`, and B's branch head
now points at it. `create_snapshot` does `db.get(DatasetBranch, branch_id)` with no ownership
check (~`version_service.py:358`). The router forwards the id untouched.

**Fix notes.** Validate `branch_id` and `from_version_id` against the dataset, both in the router
and inside the service.

## GPT-026 — [P3] Pixel restore leaves detection geometry from the cropped image

**Verdict:** CONFIRMED-KNOWN. `docs/dev/bulk-image-jobs.md` says: "Known limitation: a
version-restore that swaps in an older image file does not remap detections".

**Observed** (`test_gpt026_detection_geometry_not_restored`):
1. Start from a 100×80 image with a bbox of `[0.2,0.2,0.4,0.4]`, and snapshot it.
2. Crop to (10,8,50,40). The bbox correctly remaps to `[0.2,0.2,0.6,0.6]`.
3. Restore the snapshot. The bbox stays at `[0.2,0.2,0.6,0.6]`.

**Fix notes.** Decide the contract first: either version detection state, or invalidate or flag
detections whose coordinate frame no longer matches.

## GPT-016 (restore half) — [P2] Restore never recomputes `phash`

See `docs/dev/gpt-review/file-mutations.md` § GPT-016 for the whole finding. The restore half is the new part, and
belongs with this branch: restore sets pixels back without recomputing `phash`, and a row that
restore re-creates gets `phash=None`.
