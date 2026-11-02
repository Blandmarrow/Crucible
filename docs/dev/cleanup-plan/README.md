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

## Stage 1: the bugs (landed 2026-11-02)

B1–B4 are fixed on `fix/m8-stage1-bugs`, each with a test that failed first. What stays true now
lives in the topic files: `docs/dev/image-processing-models.md` § Upscaling (the gate, chunking,
stats refresh and the detail page's outcome read), `docs/dev/frontend-jobs.md` (upscale and LUT
in the `failed_count` convention, `backend/job_outcome.py`), `docs/dev/shared-utilities.md` (the
gate-only rule and the rename exemption), `docs/dev/inpaint.md` and CLAUDE.md § Key invariants.

Two things went differently from the plan above. Inpaint was *not* the model to copy for B4: it
wrote the resolved path back into the row, which breaks a linked `datasets_dir`, so every site,
inpaint included, now gates and keeps the stored form. And video re-extraction's `_rewrite` was an
eighth in-place writer the review missed; it is gated too. B3's test pins `chunked` with a spy
rather than `setlimit`, since `chunked`'s 10,000 default sits above any limit low enough to test.

### Stage 1 follow-ups

Found while fixing, and by an independent review of the branch on 2026-11-02. Items 1–6 landed
on `fix/m8-stage1-followups`. Each backend fix has a test that failed first; item 2 is a doc
fix, and the frontend halves of 4 and 6 have no test, since the repo has no frontend unit tests
and no e2e journey can run an upscale. What stays true lives in the files named beside them:

1. **Thumbnails gated** at every in-place site CLAUDE.md lists, crop+upscale's job included, as
   inpaint already did. A refusal is a stale tile, not a failed edit. CLAUDE.md § Key
   invariants, `utils.contained_path`'s docstring.
2. **The `failure_summary` claim corrected**, not the code: captioning's cause-based headline
   says more than a file list would. `docs/dev/frontend-jobs.md`.
3. **Chunked:** detection crop's and the detection run's request-time lookups, and the batch
   resize and crop jobs' id loads. Every loop over a client's list now dedupes it first, which
   one `IN` used to do for free (`utils.chunked`'s docstring). The rest of the class is item 9.
4. **The detail-page toast names its cause.** Upscale and LUT write `missing` beside `skipped`.
   `docs/dev/image-processing-models.md` § Upscaling.
5. **Stats refresh on a partway failure** for upscale, LUT and inpaint, through
   `dataset_service.refresh_stats_after_abort` around `_run` rather than a `try/finally`
   inside it. Same section.
6. **The detail-page race closed** with a generation ref per tool. Same section.

Still open:

7. **Crop+upscale's replace job** completes with `processed: 0` when its row vanished mid-run,
   and the detail page still toasts "Crop & upscale applied". An edge case; stage 6 replaces
   that effect.
8. **Only upscale and LUT keep a refusal's reason.** Detection crop, batch resize and crop,
   inpaint and re-extraction say why only in the live `Failed:` message and the log. Stage 3's
   shared loop is the natural place to extend `failure_summary`.
9. **The rest of the unchunked `IN` class**, left out of item 3 by choice. Client id lists
   still reach one `Image.id.in_()` in `quality.py` (`score_quality`, style similarity and the
   reference-image lookups), `captioning.py` (`run_captioning`, `run_pipeline` and the pipeline
   job), `images.py`'s `batch_reorder`, `_apply_bulk_filters` and `batch_move_subfolder`,
   `detection.py`'s `bulk_delete_detections` (whose comment calls a literal `IN` safe because the
   request body bounds it, B3's mistaken assumption), and `caption_service`, `export_service`
   and `tag_consolidation_service`. The comment above
   `SELECT_ALL_ID_CAP` in `images.py` is stale too: it calls `batch_delete` unchunked. Batch move
   and copy to another dataset wait for M4, which changes both routes.
10. **Batch resize and crop, and detection crop, share item 5's gap.** `images.py`'s two jobs and
    `detection.py`'s `crop_to_detection` refresh stats after the loop with no guard for a raise
    out of it; detection crop's `protect_file_before_overwrite` sits outside its per-image `try`.
    Item 5's wrapper, `refresh_stats_after_abort` around `_run`, closes each.

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
