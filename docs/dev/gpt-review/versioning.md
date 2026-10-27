# Versioning findings

This is the remaining verified finding for snapshots, restore and branches. For the subsystem itself, read
`docs/dev/versioning.md` and `docs/dev/versioning-service.md` first. The probes are in
`docs/dev/gpt-review/probes/backend/probe_versioning*.py`, and the conventions are in `docs/dev/gpt-review/README.md`.

025, 022, 023, 024 and the restore half of 016 are fixed — see PM-026 (and PM-025 for
021 and 015). Only 026 remains.

## GPT-026 — [P3] Pixel restore leaves detection geometry from the cropped image

**Verdict:** CONFIRMED-KNOWN. `docs/dev/bulk-image-jobs.md` says: "Known limitation: a
version-restore that swaps in an older image file does not remap detections".

**Observed** (`test_gpt026_detection_geometry_not_restored`):
1. Start from a 100×80 image with a bbox of `[0.2,0.2,0.4,0.4]`, and snapshot it.
2. Crop to (10,8,50,40). The bbox correctly remaps to `[0.2,0.2,0.6,0.6]`.
3. Restore the snapshot. The bbox stays at `[0.2,0.2,0.6,0.6]`.

**Fix notes.** Decide the contract first: either version detection state, or invalidate or flag
detections whose coordinate frame no longer matches.
