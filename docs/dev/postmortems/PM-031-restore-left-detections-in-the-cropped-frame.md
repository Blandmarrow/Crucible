# PM-031: a restore put back the original pixels but left the crop's detection boxes

### Symptom

Snapshot an image with a detection, replace-crop it, then restore the snapshot: the
file comes back at its original size, but the detection's `bbox`/`mask` stay as the
crop remapped them. On a 100×80 image with a face at `[0.2, 0.2, 0.4, 0.4]`, a
`(10, 8, 50, 40)` crop remaps it to `[0.2, 0.2, 0.6, 0.6]`, and after the restore the
detail page draws that box over the uncropped picture — a region four times too large,
in the wrong place. Crop-to-detection, export loss masks and the Stats coverage panel
all read the same wrong geometry. Nothing errors. Found by external review (GPT-026);
it had been written down as a "known limitation" in `docs/dev/bulk-image-jobs.md`.

### Root cause

Detection geometry is stored **normalized to the pixels it was measured on**, so it
is a qualifier of those pixels in the same way the scores are. The crop path knew that
and remapped every detection through the crop. The versioning layer did not: a
detection is a row in `detections`, not a column on `Image`, so `VersionImageState`
never mirrored it, `create_snapshot` never recorded it, and `restore_snapshot` swapped
the pixels back and left the rows alone. The same shape as `image_labels` before it
was mirrored, with a worse result: an unmirrored label is merely stale, an unmirrored
geometry is wrong.

### Generalizable rule

When a restore, undo or revert puts **pixels** back, ask what else is expressed in
those pixels' coordinate frame — normalized geometry, masks, crops, anything
remapped by an in-place edit — and restore it from the same snapshot, or the restore
produces a mixed state that no moment in history ever had. Likewise, any reconcile
path that *keeps* the current pixels must keep that geometry's current values too.
And treat a "known limitation" written beside an in-place edit as an open bug in the
rollback path, not a closed scope decision.

### Why it wasn't caught the first time

`test_video_lineage_mirrors.py` walks `Image`'s columns against `VersionImageState`'s,
so it cannot see a child table at all — the CLAUDE.md join-table invariant says as
much, and the label guard that followed was label-specific. Detections were judged as
"not image metadata" because they are regenerable, and the crop-remap work that made
their geometry pixel-relative recorded the restore gap as out of scope rather than
as a mirror the snapshot was missing.

### Fix

`VersionImageState.detections` (migration `a6d2e8f4c1b3`): nullable, so NULL means
"not recorded" and pre-migration snapshots leave detections alone. `create_snapshot`
records each image's rows in one canonical shape from `detection_service`; the
restore replaces them at the end of Pass 2c keyed on `p.img.id`, puts the pre-restore
set back in Pass 3b's reconcile when the content could not be written, and deletes
them explicitly with a re-created row that goes. The diff summarises them by label.
TopBar refreshes detection caches after restore and checkout jobs. Dataset
duplication still drops detections by design. Guarded by
`backend/tests/test_detections_snapshot.py`, including a structural test over both
ends.

### Status & date

MITIGATED. Last reviewed for staleness: 2026-10-28.
