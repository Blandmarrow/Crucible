# PM-009: the PNG-fallback writers stranded the row

### Scope

The class is **every writer that goes through `utils.normalize_image_format`**, not the LUT
path specifically; LUT was merely where it was found first. The original write-up named only
`routers/lut.py`, and a later review found the sibling still broken — see "Fix" below for the
second round. Read "one consumer followed the correction, its neighbour did not" as a
statement about a *set* of call sites, and enumerate that set before calling any instance
fixed.

### Symptom

Applying a LUT in **replace** mode to an image whose format Pillow cannot write back —
`.bmp`, `.gif`, `.tiff`, `.avif`, all members of `media_types.IMAGE_EXTENSIONS` — left the
dataset with two files and one row that agreed with neither:

- `photo.bmp` stayed on disk, unchanged, and `Image.file_path` / `Image.filename` kept
  pointing at it. The gallery and the detail pane went on serving the ungraded picture.
- `photo.png` — the graded result — sat beside it with no row of its own, invisible to the
  app until someone ran Rescan, which would then register it as a *second, unrelated*
  image.
- The thumbnail *was* regenerated from the new PNG, so the grid tile showed the graded
  colours while clicking through to the image showed the original.
- `Image.file_size_bytes` was updated to the PNG's size, so the row reported a size that
  matched neither the file it named nor anything the user could see.

No error, no failed job. The job reported success and the toast said the LUT had been
applied.

### Root cause

`ml/lut_service.apply_lut_sync` writes through `utils.normalize_image_format`, which falls
back to `PNG` for any suffix PIL cannot save — **and returns the path it actually wrote**,
which is why `info["out_path"]` exists in the return value at all. The caller asked for
`photo.bmp`; the helper answered "I wrote `photo.png`".

`routers/lut.py` read that corrected value and used it — for the thumbnail. The replace
branch, sitting a few lines away, updated only `file_size_bytes` and left `file_path`,
`filename` and `format` describing the file that was no longer the image. One consumer of
the corrected path followed the correction; its neighbour did not.

That partial adoption is worse than ignoring the return value entirely. Had nothing read
`info["out_path"]`, the thumbnail would have been regenerated from the stale original and
the dataset would at least have been *consistently* wrong — a state a user notices. Instead
the thumbnail agreed with the new file, the row agreed with the old one, and the
disagreement was only visible by opening the image.

### Generalizable rule

**When a helper returns a value the caller *asked for as an input* — a path, a filename, a
size, an id — because the helper reserves the right to correct it, find every consumer of
the original input and check each one followed the correction.** A single call site reading
the adjusted value proves nothing about its neighbours; it is in fact the signal to go
looking, because it shows the author knew the value could change and applied that knowledge
in one place.

In this codebase the concrete instances are `utils.normalize_image_format` (returns
`(fmt, out_path)`), `utils.unique_filename` / `unique_filename_with_thumb` (return the name
actually free), `utils.unique_poster_path`, and `video_extract._write_frame` (returns the
path written). Grep for a call whose result is destructured and then only partly used.

Sibling of PM-005, which is the same shape one level up: there a helper's tuple return was
*widened* and a second caller kept unpacking the old arity. Both are "the contract moved and
only one caller moved with it".

### Why it wasn't caught the first time

Every fixture in the LUT suite was PNG or JPEG. `normalize_image_format` returns those
unchanged, so `info["out_path"]` always equalled the path passed in and the fallback branch
was **unreachable from the tests** — not under-asserted, never executed. A coverage report
would have shown the branch cold; nobody ran one over that router.

The review question that would have caught it is not "is the LUT applied correctly" but
"which formats can reach this endpoint, and does the save path handle all of them" — and
`IMAGE_EXTENSIONS` is the answer, three of whose members PIL will not write back.

It was found only by building the adjacent feature: pass 2's extension change needed exactly
the same "the stem stays, the suffix moves" reasoning, and writing that up surfaced the LUT
path as the place it had already been got wrong.

The **second** round has two more reasons, both about the sibling. `backend/tests/` had no
upscaling module at all — not a thin one, none — so nothing about that router had ever been
asserted. And the one test that touched `upscale_image_sync` (`test_provenance_http.py`)
monkeypatched it with a fake returning `out_path`, a key production did not return: the fake
was *more correct than the code*, and stood in for it in the only place a test could have
noticed. A stub that implements the contract as it ought to be conceals the fact that the
real helper does not.

### Fix

Commit `a62bb3c`. `routers/lut.py`'s replace branch now follows the written path — updating
`file_path`, `filename`, `format` and `file_size_bytes` together, and unlinking the original
when the suffix changed — and its unregistered-file collision guard was moved to run
**before** the save rather than after: by the time `apply_lut_sync` returns, a file
hand-dropped at the fallback path has already been overwritten, so a check afterwards is
too late to refuse anything.

Test: `backend/tests/test_lut_replace_extension_http.py`, which feeds the replace path a
format PIL cannot write back and asserts the row, the file on disk and the thumbnail all
name the same picture.

**Second round** (V-02 of the `experimental-video-support` review). `upscale_image_sync` ran
the identical correction and **returned no `out_path` at all**, so its three callers were
structurally unable to follow it — a strictly worse position than the LUT router's partial
adoption, and one no amount of reading the callers would reveal. It now returns the key, and
all three follow it: `routers/upscaling.py` in both modes (plus the pre-write collision
guard, and the copy-mode name reserved under the *written* extension), and
`routers/images.py`'s two crop+upscale workers, whose `_croptmp` source carries the original
suffix. The single-image crop endpoint refuses with a 409 instead of skipping, since it has
no batch to keep going. The same copy-mode name-reservation gap was closed in `routers/lut.py`
at the same time rather than in one sibling only — fixing one of a pair is how this recurred.

Test: `backend/tests/test_upscale_png_fallback_http.py` — seven cases across both modes and
both crop workers, faking only the model call so the correction under test is the production
one, and reading the thumbnail's colour back to prove it was cut from the file that was
written.

See `docs/dev/ml-models.md` § Upscaling and § LUT grading, `docs/dev/image-detail.md` for the
crop 409, and `docs/dev/video-reextract.md` § The extension change.

### Status & date

MITIGATED — every call site of both helpers is now fixed and tested, but the class is
reachable by any new caller of a correcting helper; only review catches that. Found in code
review of the `experimental-video-support` branch, not in production.
Last reviewed for staleness: 2026-07-29.
