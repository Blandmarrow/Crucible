# File/DB mutation findings

These are the verified findings where a file operation and its DB row can disagree. Read CLAUDE.md
§ Key invariants first (PM-013, PM-021, the containment guards), and `docs/dev/bulk-image-jobs.md` /
`docs/dev/image-files.md` / `docs/dev/file-browser.md`. All of these findings were reproduced with
`docs/dev/gpt-review/probes/backend/probe_file_mutations.py`. Conventions are in `docs/dev/gpt-review/README.md`.

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
