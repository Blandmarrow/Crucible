# Structural cleanup plan: four bugs, then targeted consolidation

From an architecture review on 2026-10-31, at commit c6ce4af. Order and progress are in
[roadmap.md](../roadmap.md) § M8. This is a recommendation the owner asked to have tracked, not
a ruling, so nothing here has an entry in the decision log yet. Line numbers are from that
commit and will drift, so find code by the function named beside them.

**Lifecycle**: this file is transient. As each stage lands, move what stays true into the topic
file that owns the code. When the milestone finishes, delete this folder and its
`TRANSIENT_DIRS` entry in `scripts/check_docs.py`.

## The finding

The codebase does not need a broad restructure. Imports point the right way, with one
router-to-router import (`labels.py` to `images.py`) and no cycles found. The long functions are
mostly single transactions.

The real cost is **duplicated logic**, and moving code between files does not fix that. About 11
of the 32 postmortems, by one reader's judgement, come from several copies of a rule where one
missed a fix: PM-003, 005, 007, 009, 010, 013, 014, 015, 023, 026, 029. None is clearly caused
by a file being long. The project's existing answer is the right pattern: one writer plus a
guard, as with `utils.record_in_place`. Prefer that to a file split wherever both would work.

## Stage 1: the bugs

All four were confirmed by reading the code. None has been reproduced at runtime, so reproduce
each one with a failing test first. No port branch changes these lines, so this stage can land
before M4.

### B1: a failed upscale or LUT on the detail page shows success

- **Where:** the "Standalone upscale job tracking" and "LUT job tracking" effects in
  `frontend/src/pages/ImageDetailPage.tsx` (lines 942 and 968).
- **Why:** the backend catches a per-image failure, adds it to `counts`, and the job still ends
  `completed` (`backend/routers/upscaling.py:219`, `backend/routers/lut.py:212`). On a one-image
  job a failed run therefore shows a green "Upscaling complete". A skipped image reads the same
  way. This is PM-023's class again.
- **Fix:** read `result_data` from the finished job and report failed and skipped counts, as
  `frontend/src/components/upscale/UpscaleForm.tsx` already does. Stage 6 later removes the copy.
- **Also check:** the "Crop+upscale job tracking" effect beside them. Whether that job can
  complete with a failure inside was not checked.

### B2: upscale, LUT and inpaint leave dataset counts stale

- **Where:** `backend/routers/upscaling.py`, `backend/routers/lut.py` and
  `backend/routers/inpaint.py` never call `refresh_stats`.
- **Why it shows:** `Dataset.image_count` and `total_size_bytes` are stored columns. Copy mode
  adds rows and replace mode changes sizes, so both stay wrong until another operation refreshes
  them. The frontend's `refreshStats` client has no caller.
- **Fix:** refresh after the loop, following `images._refresh_stats_after_edit`. It must sit in
  an epilogue that cannot change the outcome (PM-013), run on a cancelled job too, and cover
  every dataset the selection touched (PM-029).

### B3: whole-dataset upscale and LUT exceed SQLite's bind limit

- **Where:** `backend/routers/upscaling.py:75` and `backend/routers/lut.py:74` pass the whole id
  list to one `Image.id.in_()`. The client-list lookups at lines 36 and 35, and
  `backend/routers/inpaint.py:171`, have the same shape.
- **Why:** the limit in this venv measures 32,766 variables, so a larger run should fail on that
  query. `inpaint.py` and `detection.py` already chunk their image loads.
- **Fix:** `utils.chunked`. To test without inserting 33k rows, lower the limit on the test
  connection with `sqlite3.Connection.setlimit`.

### B4: in-place edits write through a path nothing has checked

- **Where:** seven sites use a raw `Path(img.file_path)`: `run_upscale`'s job
  (`backend/routers/upscaling.py:147`), `run_lut`'s job (`backend/routers/lut.py:134`),
  `crop_to_detection`'s job (`backend/routers/detection.py:992`), and in
  `backend/routers/images.py` the batch resize (1333), batch crop (1449), single resize (1532)
  and single crop (1580).
- **Why it matters:** the likely route is accidental. A rescan registers a symlink or junction in
  `{dataset}/images/` under its in-tree path, as `_store_row_bytes` in
  `backend/services/version_service.py` documents. A replace-mode edit then saves through the
  link and overwrites a file outside the datasets folder. The versioning backup refuses the same
  path, logs a warning and lets the overwrite continue, so no snapshot holds the original. The
  user sees no error. It is not a known remote exploit: no route found lets a client set
  `file_path`, though not every writer was audited.
- **Fix:** copy `backend/routers/inpaint.py:314`. Call `contained_path` per row, count a refusal
  as failed, and use the resolved path from then on. The single-image routes use
  `safe_dataset_path` for a 403. Add the sites to the list in CLAUDE.md § Key invariants.
- **Land after B1, or with it:** a refused image counts as failed, and the detail page shows a
  failed count as success until B1 is fixed.
- **Other users' setups:** the owner keeps images separate and does not use links, but other users
  may (owner, 2026-10-31), so make the refusal explain itself. Emit a `Failed:` progress message
  that says the file resolves outside the datasets folder, not just a count and a log line, and
  add a short note to the user docs, which say nothing about links today. From reading the code,
  not run:
  - A link inside a dataset that resolves outside `datasets_dir`, or a dataset folder linked
    elsewhere: `serve_file` already answers 403 through `safe_dataset_path`, so the full image
    cannot be opened today. The guard adds refused edits to a setup that is already partly broken.
  - `datasets_dir` itself behind a link: unaffected, because the guard resolves both sides, and
    `test_a_symlinked_datasets_dir_still_matches_rows` covers that setup for delete. This is the
    arrangement to point users at for keeping data on another drive.
- **Also assess:** the single-image rename reads a raw path too (`images.py:1988`). It was not
  assessed.

## Stages 2 to 4: no port conflict

No port branch touches `upscaling.py`, `lut.py`, `inpaint.py`, `detection.py`, the upscale and
LUT forms or `DatasetsPage.tsx`.

- **Stage 2: one helper for a job's image scope.** Upscale, LUT, inpaint and `crop_to_detection`
  each repeat "`image_ids`, or dataset plus subfolder plus quality flags". The flag filter exists
  in about 7 places. Put the helper in a new module, not `utils.py`, which M4 and M6 both change.
  Leave `images._apply_bulk_filters` in place for now: M4 adds a caller, so it moves in stage 8.
- **Stage 3: a shared job loop for upscale and LUT.** Their job bodies are about 95% identical.
  The real differences are the sync function, the suffix, the `record_in_place` arguments and
  upscale's width and height. Leave inpaint and `crop_to_detection` out unless they fit without
  flags.
- **Stage 4: `DatasetsPage` dialogs.** Five independent forms move out, about 40% of the file.

## Stages 5 to 8: after M4 and M7

These files carry port changes. M4 changes `GalleryPage` by 187 lines and `images.py` by 161;
`ImageDetailPage` and `SelectionToolbar` are changed by both M4 and M7.

- **Stage 5: `SelectionToolbar` dialogs into forms**, following `UpscaleForm` and `LutForm`. It is
  about 80 lines of toolbar plus 14 inline dialogs, and about 85% moves out. Keep `anyModalOpen`
  (fed to the Delete key handler) and `jobFormRunning` working by having each form report upward.
- **Stage 6: the detail page's inline tools.** Replace upscale and LUT with the existing forms,
  then move out the AI caption panel and the detect modal's fields. The forms need a new callback,
  because the page opens the new image after a copy-mode run. The arrow and Delete handlers read
  many of these states, so an extracted tool must report whether it is open.
- **Stage 7: `StatsPage` into files.** About 60% is already separate functions, so this is a move
  with no state change.
- **Stage 8: split `backend/routers/images.py` by section**, and move `_apply_bulk_filters` out. It
  has the most changes and fix commits of any file, and its sections are mostly independent.

## Any time

- Add a coverage config with `concurrency = greenlet,thread`, and update
  `docs/dev/backend-tests.md`. The documented `--cov=backend` loses lines after an `await`:
  `images.py` reads 50% instead of 87%.
- Correct CLAUDE.md § Data flow, which says logic lives in the service layer. Job bodies are
  closures in routers, and that stays true after this plan.

## Leave alone

| Code | Why |
|---|---|
| `version_service.restore_snapshot` | One ordered transaction; splitting names the coupling but does not remove it |
| `dataset_service.duplicate_dataset` | The same, though its two copy strategies could be extracted |
| `GalleryPage` filter and persistence core | One ordered unit, and the site of PM-012 |
| `CaptioningPage`, `ExportPage`, `SettingsPage` | Cohesive, or already split by tab |
| `utils.py` | Well indexed, and a split changes every importer |
| Moving all job bodies into services | High churn, and does not fix the duplication by itself |

## What will bite

- `backend/tests/test_labels_survive_rebuild_paths.py` and `test_video_lineage_mirrors.py` walk
  named functions, so moving a job body fails or silently changes what they count. Update them
  in the same commit and check they still see the moved code.
- Job bodies import `AsyncSessionLocal` inside the function because `conftest.py` rebinds it. A
  module-level import in extracted code bypasses the test database.
- Tests patch router attributes, such as `upscale_image_sync` and `apply_lut_sync` on the router
  modules. Moved code breaks those patches.
- Module-level state must not be duplicated: `_stats_cache`, `dataset_busy._busy`, and
  `_poster_locks` and `_poster_failures` in `videos.py`.
- Gallery's persistence blob, `liveStateRef` and the unmount save are one ordered unit. The
  detail page's `navRefreshSeq` effect relies on a deliberate gap in its dependencies.

## Safety net at the review

Backend: 1,346 passed, 9 skipped, 75% coverage excluding tests and migrations. `images.py` is at
87% and `videos.py` at 94%, but `comfy.py` is at 58% and `captioning.py` at 63%, so add tests
before moving those two. Frontend: no unit tests, only 32 Playwright specs, not run during the
review. They are the only check on a dialog extraction beyond the build, so keep each extraction
small and run `modal-a11y.spec.ts` after it.
