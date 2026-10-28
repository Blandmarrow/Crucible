# File/DB mutation findings

These are the verified findings where a file operation and its DB row can disagree. Read CLAUDE.md
§ Key invariants first (PM-013, PM-021, the containment guards), and `docs/dev/bulk-image-jobs.md` /
`docs/dev/image-files.md` / `docs/dev/file-browser.md`. All of these findings were reproduced with
`docs/dev/gpt-review/probes/backend/probe_file_mutations.py`. Conventions are in `docs/dev/gpt-review/README.md`.

## GPT-020 — [P3] A positive resize can round a dimension to zero and 500

**Verdict:** CONFIRMED. NEW. Nothing is corrupted, because it fails before the save.

**Observed** (`test_gpt020_zero_dimension_resize`):
- A 100×1 PNG at `scale=0.5` returns 500, from Pillow's `height and width must be > 0`.
- `{width:1,height:1,maintain_ar:true}` also returns 500.
- Batch resize counts the image as `failed:1` and the job completes.

**Fix notes.** In `image_service` resize, clamp the derived dimensions to ≥1, or reject them with a
4xx before the version hook. Apply the same rule to the single and batch routes.
