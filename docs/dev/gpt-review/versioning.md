# Versioning findings

These are the verified findings for snapshots, restore and branches. For the subsystem itself, read
`docs/dev/versioning.md` and `docs/dev/versioning-service.md` first. The probes are in
`docs/dev/gpt-review/probes/backend/probe_versioning*.py`, and the conventions are in `docs/dev/gpt-review/README.md`.

Several of these findings compound each other: 022 and 023 both end as a "completed"
restore because of 025, so fix them as one branch. (021 and 015, which compounded the same
way, are fixed — see PM-025.)

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
