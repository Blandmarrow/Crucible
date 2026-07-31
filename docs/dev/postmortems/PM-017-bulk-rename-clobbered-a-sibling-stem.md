# PM-017: bulk rename destroyed a sibling's thumbnail and caption

### Symptom

Renumber a dataset holding two images of different extensions, reorder them, and renumber
again: one row's gallery tile silently starts showing the *other* row's picture, and one
row's caption is replaced by the other's text. Both rows are still present, both filenames
are correct, and every existing assertion about naming passes.

Concretely, with `a.png` and `b.jpg` renumbered to stem `img`:

- pass 1 → `img.png` (row A) and `img_001.jpg` (row B), thumbnails `img.webp` /
  `img_001.webp`, sidecars `img.txt` / `img_001.txt`
- reorder, pass 2 → row B targets `img.jpg`, row A targets `img_001.png`
- row B's rename runs first and moves `img_001.webp` onto `img.webp` and `img_001.txt`
  onto `img.txt`, both of which are row A's live files
- row A's rename then moves what is now row B's `.webp` and `.txt` on to `img_001.*`

The result is one thumbnail and one caption for two images, plus a row whose
`thumbnail_path` names a file that no longer exists. It is permanent: `serve_thumbnail`
(`backend/routers/images.py`) regenerates a thumbnail only when the file is **missing**,
never when it is stale, so the wrong picture is served indefinitely. On Windows the same
sequence raises `FileExistsError` from `Path.rename` and aborts the batch halfway instead.

### Root cause

`bulk_rename` renames in two phases: any row whose target is still occupied by another
batch member goes to a temp name first, everything else renames directly. The deferral
predicate tested one thing —

```python
if str(new_path) in batch_old_paths:
```

— the **image path**, extension included. But a rename moves three files, and the other two
are keyed on the **stem** alone: the thumbnail at `{ds}/thumbnails/{stem}.webp` and the
caption sidecar at `{stem}.txt`. A collision that differs only in extension (`img.jpg`
taking the stem that `img.png` currently holds) is therefore invisible to the predicate,
takes the direct branch, and destroys both derived artifacts of a live sibling.

The planner cannot prevent this on its own, and deliberately does not try. `bulk_rename` is
the one sanctioned exception to `unique_filename_with_thumb`'s "never exclude the stems of
images being renamed" contract: without excluding the batch's own `.webp` stems and its own
files on disk, a second Renumber of `image.jpg … image_007.jpg` sees all eight stems
occupied and restarts its counter at `image_008` instead of `001`. The exclusions are what
make Renumber idempotent — and the two-phase rename is what is supposed to pay for them.

The provenance is a fix regressing its immediate predecessor. Commit `543891e` (2026-06-02,
"Fix thumbnail collision") introduced `unique_filename_with_thumb` and its stem rule; commit
`37bba00` (2026-06-03, "Manual reordering & incremental renaming") added the Renumber
feature and, in the same change, both the `disk_exclude` argument and the
`batch_thumb_stems` exclusion. The author needed both to make the counter restart, and
extended the deferral to cover the resulting within-batch collisions — but extended it over
the artifact whose name the function is about, not over the artifacts the rename moves.

### Generalizable rule

**When a rename moves derived artifacts, the collision test must cover every artifact it
moves — not the file the operation is named after.** Any guard phrased over a *path* is
wrong the moment one of the things it protects is keyed on a *stem*, a hash, an id prefix,
or anything else coarser than the full filename: two distinct paths then map onto one
derived path and the guard cannot see it.

Two review questions follow:

- For each file this operation writes or moves, what is its key? If any key is coarser than
  the one the guard tests, the guard has a blind spot exactly the width of that difference.
- If a path deliberately weakens a shared naming guard (an `exclude` argument, a skipped
  uniquifier), what pays for it? Find that mechanism and check it covers *all* of what the
  guard covered, not just the case that motivated the weakening.

A related smell, also present here: temp names built from a **user-supplied** string
(`"__renaming__" + old_path.name`) rather than from a row id. A user renaming to the stem
`__renaming__image` could collide with a live temp file mid-batch. Derive scratch names
from something the user cannot type; the DB half of this same function already did.

### Why it wasn't caught the first time

Three separate reasons, all worth closing:

- **The tests asserted names, not bytes.** `test_bulk_rename_mixed_suffix_thumb_stems_stay_distinct`
  checked the set of thumbnail *stems* against the set of image stems. The clobber satisfies
  that assertion perfectly — every filename is correct; only the *contents* are wrong. A
  placement test that never opens a file cannot see a clobber.
- **The existing swap test used one extension.** `test_bulk_rename_swap_permutation_no_clobber`
  permutes two `.png` files, so every collision it produces is also an image-path collision
  and the old predicate handles it. The cross-extension case is the whole defect and no
  fixture had two extensions in a permuting batch.
- **The sidecar was never in scope.** `rename_with_sidecar` renames `{stem}.txt` on its own,
  independently of whatever the image rename found at the target, and its docstring said
  nothing about the caller's obligation to prove the target free. A helper that can silently
  destroy data needs that stated where its callers read it.

### Fix

- `backend/routers/images.py::bulk_rename` — the deferral predicate is now a three-set test:
  defer when the target **image path**, **thumbnail path** or **`.txt` sidecar path** is any
  batch member's current one. One predicate, both artifacts.
- Temp names derive from the row id (`__renaming__{img_id}`), matching the DB half two
  blocks above.
- `occupied_thumb_stems |= {Path(n).stem for n in db_names}` — a non-batch row whose `.webp`
  is missing now protects its own stem, since the next view regenerates it there.
- The 776-784 comment now says the exclusions are a *sanctioned exception* and names what
  pays for them; `utils.unique_filename_with_thumb`'s docstring cross-references it.
- `utils.rename_with_sidecar` gained a docstring paragraph stating the caller's obligation.
  **No behaviour change** — `os.replace` would only make Windows destroy data as quietly as
  POSIX does, and an `exists()` guard adds a TOCTOU race to a contract the caller can meet
  exactly.
- Tests (`backend/tests/test_rename_collisions_http.py`):
  `test_bulk_renumber_twice_keeps_every_row_its_own_three_artifacts` is the repro and asserts
  **bytes** for all three artifacts; `test_bulk_renumber_restarts_its_counter_on_a_second_pass`
  pins the counter restart the exclusions buy, so they cannot be simplified away.

### Status & date

MITIGATED — the predicate now covers every artifact `bulk_rename` moves, and both halves are
pinned by tests. The class is still reachable by any *new* rename path that guards a path
while moving something keyed on a stem; only review catches that. Found in code review of
the `experimental-video-support` branch; reproduces on `main`.
Last reviewed for staleness: 2026-07-31.
