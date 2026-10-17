# File/DB mutation findings

These are the verified findings where a file operation and its DB row can disagree. Read CLAUDE.md
§ Key invariants first (PM-013, PM-021, the containment guards), and `docs/dev/bulk-image-jobs.md` /
`docs/dev/image-files.md` / `docs/dev/file-browser.md`. All of these findings were reproduced with
`docs/dev/gpt-review/probes/backend/probe_file_mutations.py`. Conventions are in `docs/dev/gpt-review/README.md`.

## GPT-009 — [P2] A sidecar rename failure strands the renamed image behind the old DB path

**Verdict:** CONFIRMED. NEW. This is a PM-013 violation inside a helper, and it is not Windows-only:
any failure of the second rename has the same effect.

**Mechanism.**
- `utils.rename_with_sidecar` (~`utils.py:593-610`) does `old.rename(new)` and then a separate
  `.txt` rename, yet its docstring says "atomically".
- The callers assign the new path fields, call the helper, and only then commit:
  `routers/images.py` rename (~1921-1929) and `routers/filesystem.py` rename (~575-588).

**Observed** (`test_gpt009_gallery_rename_sidecar_fail_strands_row`,
`test_gpt009_filesystem_rename_sidecar_fail_strands_row`): with only the `.txt` rename raising a
lock error, the gallery route returned 500 and the filesystem route returned 403. In both cases:
- the image is at its new name;
- the caption is still at the old name;
- the row names the nonexistent old path;
- `/images/{id}/file` returns 404.

**Fix notes.**
- Make the pair recoverable: if the sidecar rename fails, roll the image rename back, or commit
  the image rename first and treat the sidecar as a logged, post-commit epilogue.
- Correct the docstring.
- Check every other caller of `rename_with_sidecar` (`bulk_rename`, move paths). Each one inherits
  this.

## GPT-011 — [P3, GPT said P2] `..` path aliases bypass filesystem-delete bookkeeping and snapshot protection

**Verdict:** CONFIRMED. NEW. Downgraded because the UI sends canonical paths, so this is reachable
only through a direct API call. That said, the API is unauthenticated, and the effect is silent
data loss whose restore reports nothing.

**Mechanism.**
- `sanitize_abs_path` (`utils.py:97-109`) returns `Path(path)` without collapsing `..`.
- `filesystem.delete_path` derives its DB scope from the raw string (`old_prefix`), while
  `shutil.rmtree` resolves the alias.

**Observed** (`test_gpt011_dotdot_alias_bypasses_row_scope_and_snapshot_protection`, with
`versioning_mode=auto`):
- Deleting `{ds}/images/../images` returned 200 and the bytes were gone.
- The row stayed, so `/file` returns 404.
- `restore_snapshot` reported 0 restored, 0 unavailable and 0 failed, and the file stays gone.
- The canonical-path control removes the row and restores the file correctly.

**Fix notes.**
- Canonicalize once, with `resolve()`, before both the row matching and the mutation, and use the
  same identity for both.
- Consider doing it inside `sanitize_abs_path` for every filesystem route.
- Test symlinked and case-variant paths too.

## GPT-016 — [P2 restore half / P3 resize-crop half] Pixel edits and restore keep stale `file_size_bytes`/`phash`

**Verdict:** CONFIRMED.
- **The resize/crop half is KNOWN:** `docs/dev/bulk-image-jobs.md` § batch resize/crop says
  "neither handler refreshes `file_size_bytes`/`phash` after the overwrite (nor do the
  single-image twins)".
- **The restore half is NEW** and belongs with the versioning branch.
- The single replace-crop *does* refresh both, at `images.py` ~1561-1564. Only resize, batch
  resize and batch crop do not.

**Observed:**
- `test_gpt016_resize_leaves_stale_size_and_phash`: the stored size is 563 while the actual size
  is 601. phash happened to match, because it is scale-robust.
- `test_gpt016_batch_crop_leaves_stale_size_and_phash`: the size is 563 against 507, and the phash
  is `f8d5…` against `be9e…`.
- `test_gpt016_restore_keeps_stale_phash_and_recreated_row_has_none`, a crop followed by a
  restore: the disk is back to the original phash `f8d5…`, but the DB keeps the cropped `eded…`.
  A row that restore re-creates gets `phash=None`, which drops it out of duplicate scans.

The comment at `backend/tests/test_video_lineage_mirrors.py:86` ("phash: recomputed from the
restored file") is false.

**Fix notes.**
- Measure the size and phash from the written output, and assign them with the geometry and
  history *before* the commit. They are cheap and pure, but they must not add a fallible step
  between the overwrite and the commit (PM-013); compute them from the in-memory output, or
  treat a failure as non-fatal.
- In restore, recompute or mirror phash.
- Refresh dataset totals as well.

## GPT-008 — [P3, GPT said P2] A locked image delete commits the row removal, then 500s

**Verdict:** CONFIRMED-KNOWN. CLAUDE.md, in the PM-021 bullet, already lists `images.py` as
"wholly unconverted". The lock was simulated with `PermissionError(winerror=32)`.

**Observed** (`test_gpt008_locked_image_delete_commits_row_then_500`):
- DELETE returns 500.
- The row is gone and the file remains.
- **`image_count` stays 1, because `refresh_stats` runs after the failing unlink.** That part is
  new.
- A retry returns 404.

`batch_delete` and `bulk_delete_filtered` have the same raw post-commit unlink; they were not
probed.

**Fix notes.**
- Convert the three image-delete sites to `utils.unlink_retrying`, using the PM-021 pattern from
  `videos.delete_video`, where the raise sits before the row goes.
- Make the stats refresh independent of the unlink outcome.

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
