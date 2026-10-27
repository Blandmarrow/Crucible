# File/DB mutation findings

These are the verified findings where a file operation and its DB row can disagree. Read CLAUDE.md
§ Key invariants first (PM-013, PM-021, the containment guards), and `docs/dev/bulk-image-jobs.md` /
`docs/dev/image-files.md` / `docs/dev/file-browser.md`. All of these findings were reproduced with
`docs/dev/gpt-review/probes/backend/probe_file_mutations.py`. Conventions are in `docs/dev/gpt-review/README.md`.

## GPT-016 — [P3] Resize and batch crop keep stale `file_size_bytes`/`phash`

**Verdict:** CONFIRMED-KNOWN. `docs/dev/bulk-image-jobs.md` § batch resize/crop says
"neither handler refreshes `file_size_bytes`/`phash` after the overwrite (nor do the
single-image twins)". The single replace-crop *does* refresh both, at `images.py` ~1561-1564.
Only resize, batch resize and batch crop do not. (The restore half is fixed: restore now
measures phash, size, format and geometry from the file it wrote — see
`docs/dev/versioning-service.md`.)

**Observed:**
- `test_gpt016_resize_leaves_stale_size_and_phash`: the stored size is 563 while the actual size
  is 601. phash happened to match, because it is scale-robust.
- `test_gpt016_batch_crop_leaves_stale_size_and_phash`: the size is 563 against 507, and the phash
  is `f8d5…` against `be9e…`.

**Fix notes.**
- Measure the size and phash from the written output, and assign them with the geometry and
  history *before* the commit. They are cheap and pure, but they must not add a fallible step
  between the overwrite and the commit (PM-013); compute them from the in-memory output, or
  treat a failure as non-fatal.
- Refresh dataset totals as well.

## GPT-010 — [P3, GPT said P2] Crop remap is fallible DB work between the pixel overwrite and the commit

**Verdict:** PARTIAL. The ordering is real, but it is a deliberate design:
`docs/dev/bulk-image-jobs.md` says remap is "DB-only work, so it stays *above* the commit". That
reasoning treats a raise as a clean rollback. A raise is not clean, because the file overwrite
cannot be rolled back.

**Observed:**
- `test_gpt010_single_crop_remap_failure_leaves_pixels_ahead_of_row`, with an injected OSError:
  the request returns 500, the disk holds 16×16 while the DB says 32×32, and there is no
  `processing_history` entry. Losing that entry defeats pass 2's edit-protection, which is exactly
  the PM-010 consequence.
- `test_gpt010_batch_crop_remap_failure_fails_job_after_overwrite` behaves the same on the batch
  route.
- `test_gpt010_realistic_trigger_malformed_mask_json`: a malformed stored mask raises `TypeError`.
  The ORM listener blocks such a value on normal writes, so a realistic trigger needs a SQLite lock
  or an I/O error.

The other sites are `images.py` (~1445, ~1670) and `routers/detection.py` (~996). They were
inspected but not probed.

**Fix notes.** Compute the remap *before* the irreversible overwrite: SELECT the detections,
compute the new geometry, then overwrite, assign, and commit. Fix all four sites together.

## GPT-017 — [P3, GPT said P2] Crop remap invents a mask for a polygon wholly outside the crop

**Verdict:** CONFIRMED. This is a corner of a documented, accepted limitation:
`docs/dev/bulk-image-jobs.md` says "Polygon vertices are remapped+clamped (edge polygons flatten
against the border — accepted)".

**Observed:**
- `test_gpt017_polygon_outside_crop_gets_invented_mask`:
  - On a 100×100 image, the triangle (0,0),(60,0),(0,60) and the crop (40,40,60,60) do not
    intersect.
  - The helper returns `[[0,0],[0.3333,0],[0,0.3333]]`.
  - The HTTP crop persists `mask_area=0.0555`.
- The control `test_gpt017_control_polygon_on_one_side_is_dropped` passes. Only diagonal shapes
  whose bbox overlaps the crop are affected.

**Fix notes.** Replace the vertex clamp in `backend/ml/mask_utils.py` with real polygon clipping
(Sutherland–Hodgman against the crop rect). Drop empty results, and handle degenerate and split
outputs.

## GPT-020 — [P3] A positive resize can round a dimension to zero and 500

**Verdict:** CONFIRMED. NEW. Nothing is corrupted, because it fails before the save.

**Observed** (`test_gpt020_zero_dimension_resize`):
- A 100×1 PNG at `scale=0.5` returns 500, from Pillow's `height and width must be > 0`.
- `{width:1,height:1,maintain_ar:true}` also returns 500.
- Batch resize counts the image as `failed:1` and the job completes.

**Fix notes.** In `image_service` resize, clamp the derived dimensions to ≥1, or reject them with a
4xx before the version hook. Apply the same rule to the single and batch routes.
