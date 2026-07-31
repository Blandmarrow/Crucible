# PM-013: fallible work between an irreversible file write and its commit

### Scope

The class is **every path that mutates the filesystem irreversibly inside an open
transaction and then does something fallible before committing** — not the pass-2
re-extraction path where it was found. Four routers had it, with one symptom between them,
and one of the four (`routers/upscaling.py`) acquired its instance three commits earlier in
the fix for PM-009: fixing one class planted a fresh instance of another. PM-009's own rule
says to enumerate the set before calling any instance fixed, so all four were fixed
together: `routers/videos.py` (`_rewrite`), `routers/lut.py` and `routers/upscaling.py`
(the replace branches), and `routers/images.py` (`_run_crop_upscale_replace`).

Two adjacent tiers were enumerated and deliberately **not** fixed here:

- **Tier 2 — in-place paths that keep stale geometry and lose the `processing_history` skip
  guard**: `routers/images.py` single crop and the batch resize/crop workers,
  `routers/detection.py`'s crop worker. Milder — no 404, because the filename never changes
  — but a lost history entry means a later re-extraction silently discards a user's edit
  (PM-010's consumer).

  **Amended 2026-07-31:** the two batch workers left this tier, fixed to the Tier-1 shape
  alongside PM-018 (which is what made them reachable at all — they were shadowed routes
  and had never executed). Their instance was also **worse than this entry recorded**: the
  only `commit()` sat *outside* the loop, so the blast radius was not one image's stale
  geometry but every image the run had already overwritten. The note above understates any
  instance where the commit is not per item, and that is the question to ask of a loop, not
  just "what runs between the write and the commit".

  **Amended again 2026-07-31:** `routers/detection.py`'s `crop_to_detection` was the third
  instance of that same worse shape — an un-`try`-wrapped `generate_thumbnail` after the
  `tmp_path.replace`, and the loop's only `commit()` after the loop — so it is **fixed**,
  not milder, and now follows the Tier-1 order with a per-image commit and a
  `counts["thumbnails_stale"]` epilogue.

  **Amended a third time 2026-07-31: Tier 2 is now empty.** `routers/images.py`'s single
  crop is fixed to the Tier-1 shape (commit, then a `try`-wrapped `generate_thumbnail` that
  logs and continues) — and so is **`POST /images/{id}/resize`, which this entry never
  enumerated at all** despite the identical shape: `resize_image` saves over `img.file_path`
  in place and `generate_thumbnail` ran before the only `commit()`. It was worse than the
  crop, because it was not even guarded on `img.thumbnail_path` being set, so a row with no
  thumbnail raised `TypeError` outright and lost the resize's geometry every time. That is
  an enumeration miss by this entry's own generalizable rule — the sweep that produced these
  tiers looked at the *crop* endpoints and their workers and never asked the same question
  of the resize twin sitting forty lines above them. Neither endpoint reports a
  `thumbnails_stale` count: that is a *job* counter `TopBar` reads off `job.result_data`,
  and both return dicts rather than jobs. `backend/tests/test_single_image_edit_epilogue_http.py`
  holds all three cases.
- **Tier 3 — copy-mode branches that cut the thumbnail before the row insert**
  (`routers/lut.py`, `routers/upscaling.py`, `routers/detection.py`, `routers/images.py`'s
  new-file crop worker). A mid-loop raise orphans files with zero rows; nothing 404s.

Also unfixed and pre-existing: `workers/job_queue.py` marks a raising job failed from a
separate session and never writes the `result_data` the job had accumulated, so a job that
dies mid-run reports nothing about what it did before it died.

### Symptom

Re-extracting a video's frames to a *different* format (`POST /videos/reextract` with
`format: "png"` over `.jpg` triage frames) while `thumbnails/` was unwritable or the disk
was full left the dataset with a row naming a file that did not exist:

- On disk: `clip_s0001_00.png`, the full-res frame, correctly written.
- In the DB: a row still naming `clip_s0001_00.jpg`, which had already been unlinked.
- `GET /images/{id}/file` returned **404**. The gallery card broke and the detail pane
  showed nothing.
- A later `rescan_dataset` adopted the orphaned `.png` as a **second, unrelated image**.
- Nothing in the DB recorded why. The job ended `failed` with an `OSError` from
  `generate_thumbnail` in the log, which names the thumbnail, not the row.

The same-suffix case was quieter and just as wrong: no 404, but the row kept the 160 px
triage `width`/`height`/`phash` while the file on disk was full-res, and the `reextract`
entry that would have stopped the next run from re-doing the frame was rolled back with it.

At the LUT, upscale and crop+upscale sites the trigger was the PNG fallback rather than a
requested format change, and the failing step could also be the `unlink` of the superseded
original. `routers/images.py` had the worst ordering of the four: `generate_thumbnail` ran
*before the session was even opened*, so a raise there meant the row was never updated at
all — PM-009's "two files, one row" re-created by a different mechanism.

### Root cause

`generate_thumbnail` (`services/image_service.py`) catches nothing. It sat between the
`os.replace` that swapped the frame's file and the `commit()` that described the swap, so
its exception propagated out of the job, the session closed without committing, and the
row's `filename`/`file_path` mutations rolled back. The `os.replace` and the `unlink` did
not: a transaction can roll back a row, and nothing rolls back a filesystem.

The pattern is not "thumbnail generation is unreliable". It is that a transaction boundary
was drawn *after* an irreversible side effect, so the transaction's atomicity guarantee
covered only the half of the operation that could be undone. Every step between the two
inherits the power to strand the row, and every one of them is I/O.

### Generalizable rule

**Flag any code path where an irreversible filesystem mutation (`os.replace`, `unlink`,
`shutil.move`, an in-place overwrite) is followed by *anything fallible* before the
`commit()` that records it.** Move the fallible work after the commit, into a `try/except`
that logs and cannot change the item's outcome or feed a failure breaker. Where mutation
and commit cannot be made adjacent, order them so a failure at the commit leaves the
*pre-mutation* file present and still named by the row.

Two corollaries a reviewer should apply directly:

- **`Path.unlink` is fallible too** (`EACCES`/`EROFS`; `PermissionError` on Windows if any
  process holds the file open — and a `/file` request can be streaming that exact path
  while a job runs). Deleting a superseded original counts as fallible work, so it belongs
  in the epilogue, not beside the rename.
- **A failed epilogue is not a failed item.** A frame that was written and committed but
  whose thumbnail did not regenerate has succeeded; counting it failed both lies in
  `result_data` and feeds the consecutive-failure breaker. Record it separately —
  `counts["thumbnails_stale"]` — rather than downgrading the outcome.

The one irreducible window is the `commit()` itself. It cannot be ordered away, so order
everything else around it: with the unlink after the commit, a failing commit leaves the row
naming the original, the original on disk, and `/file` still 200. The residue is an orphan
file at the new suffix — the same rescan-adoptable leftover the docs already accept for a
`SIGKILL`, not a broken row.

Sibling of PM-011, which is the same shape with the order of *evaluation* rather than
durability: a predicate read after the `shutil.move` that invalidated it.

### Why it wasn't caught the first time

Every test asserted the success path. No test made a post-mutation step fail — the suites
covered the failures that happen *before* the swap (a temp that will not re-open, an
unregistered file at the target path, a raise from the COW hook) and stopped there, because
those were the failures the code was written to handle. The steps after the swap were not
thought of as failure sites at all, so their ordering was never a decision anyone made.

The review question that closes the gap is not "does this handle errors" but **"if the very
next line raises, does the filesystem still match the database?"** — asked at each line
between a write and its commit. `generate_thumbnail` reads as infrastructure rather than as
I/O, which is exactly why it needs the question asked about it explicitly.

The `routers/upscaling.py` instance has an extra lesson: it was *introduced* by PM-009's
fix, which added the `filename`/`file_path` reassignment and the `unlink` directly above an
already-present unguarded `generate_thumbnail`. A fix that adds row mutations near existing
I/O should re-ask where that path's transaction boundary now falls.

### Fix

All four sites now follow one order: write, assign every row field, `commit()`, then a
best-effort epilogue that unlinks the superseded original and regenerates the thumbnail,
each wrapped and logged. `AsyncSessionLocal` is built with `expire_on_commit=False`
(`backend/database.py`), which is what makes the post-commit attribute reads in the epilogue
safe; a later `expire_on_commit=True` would break all four silently. `routers/videos.py`
also gained `counts["thumbnails_stale"]`, which flows into `BackgroundJob.result_data`.

Tests: `backend/tests/test_video_reextract_http.py` — a thumbnail that will not regenerate
still commits the rewrite (same-suffix), a thumbnail failure during the PNG rename still
serves the frame (the 404 case), and a commit that fails after the swap leaves the original
in place (which pins the unlink-after-commit ordering and is unwritable against the naive
fix). One case each of the second shape in
`backend/tests/test_lut_replace_extension_http.py` and
`backend/tests/test_upscale_png_fallback_http.py`, the latter covering both the batch
upscale and the crop+upscale replace worker.

See `docs/dev/video-reextract.md` § The `video_reextract` job and § The extension change,
and `docs/dev/ml-models.md` § Upscaling and § LUT grading.

### Status & date

MITIGATED — the four Tier-1 sites are fixed and tested, but the class is reachable by any
new path that writes a file inside a transaction, and Tiers 2 and 3 above are known-unfixed
instances of milder shapes. Only review catches a new one. Found in code review of the
`experimental-video-support` branch (V-03), not in production.
Last reviewed for staleness: 2026-07-29.
