# PM-029: a selection spanning datasets was treated as the pane's own dataset

### Symptom

Two failures from one assumption, both reachable from split view, where one gallery
selection holds images from both panes' datasets (`selectionStore.datasetByImageId`):

- **Stale pane (GPT-028).** Select one image in each pane and Delete from the left one. The
  toast said "Deleted 2 images" and the API had deleted both, but the right pane kept
  showing its tile and "1 images" until something unrelated refetched. Move to Dataset and
  the upscale, LUT, crop-to-detection and remove-watermark jobs left the other pane stale
  the same way.
- **Files in the wrong dataset (found auditing GPT-028).** The toolbar's "Move to
  subfolder" with rename on, over the same mixed selection, made `POST
  /images/batch/move-subfolder` rename dataset B's files into dataset A's `images/`
  folder. B's rows kept `dataset_id = B` but their `file_path` pointed inside A. Which
  dataset "won" was whichever row the query returned first. Without rename, only the busy
  guard was wrong.

### Root cause

Both sides took "the dataset" from one place and applied it to every image:

- **Frontend.** The toolbar refreshed `invalidateDatasetContentScope(qc, datasetId)` with
  its own pane's `datasetId` prop, although the backends for these actions accept any
  dataset's ids. The provenance and labels actions already invalidated at bare prefixes
  "because `useSelectionStore` spans datasets", but nothing carried that reason to the
  delete or the jobs.
- **Backend.** The route read `dataset_id`, its images folder, its filename set and its
  busy guard from `rows[0]`. That is the exact shape CLAUDE.md § Key invariants already
  warned about for provenance: "a selection can span datasets … the same goes for the
  batch's busy guard". The rule was written as a provenance rule, so it was not applied
  to a route that does not touch provenance.

### Generalizable rule

- **Any endpoint taking a list of image ids must decide what a multi-dataset list means.**
  It either works per row against each row's own dataset (`batch_delete`,
  `bulk-provenance`, `batch_move_dataset`), refuses a mixed list with a 4xx before
  touching anything (`batch_move_subfolder`), or filters to one `dataset_id` and says so
  (captioning, scoring, detection, bulk caption edit, merge tags). Flag `rows[0].dataset_id`,
  or any per-dataset value read once outside the row loop, in a route whose ids are not
  already scoped to a single `dataset_id`.
- **A selection action's cache refresh covers every dataset the selection touched,**
  captured before `clear()` empties `datasetByImageId`. Flag a toolbar mutation or job
  completion that invalidates only the pane's `datasetId` when its backend accepts
  unscoped ids.

### Why it wasn't caught the first time

Every selection test selected inside one pane. Split view and cross-pane selection are
covered by separate specs, and none combined them with a mutating action. The CLAUDE.md
invariant named its trigger as provenance, so reviewers of non-provenance batch routes had
no prompt to ask the question.

### Fix

- `652d420`: SelectionToolbar derives `selectionDatasetIds` and passes it as mutation
  variables. Delete and move-to-dataset refresh every source dataset (plus the target),
  and the four unscoped job forms refresh each one when the job completes.
  `frontend/e2e/gallery-split-delete.spec.ts` ports the GPT-028 probe.
- `1627ed8`: `batch_move_subfolder` answers 400 for a request spanning datasets. The
  toolbar sends one request per dataset. `backend/tests/test_move_subfolder_mixed_datasets_http.py`.
- The filter-to-one-dataset actions still skip other datasets' ids silently, and their
  counts undercount. This is recorded in `docs/dev/frontend-core.md`. It is not data loss,
  so it is left as is.

### Status & date

MITIGATED — 2026-10-28.
Last reviewed for staleness: 2026-10-28.
