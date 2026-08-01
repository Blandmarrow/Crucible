# Frontend jobs: SSE and cache invalidation

This file covers the client half of the background-job flow: the `useJobSSE`/`useAllJobsSSE`
hooks that feed `jobStore`, how a running or pending job is labelled in the UI, and — the
part that matters most when adding a feature — which caches a finished job must invalidate.
The server half (job queue, SSE broadcaster, cancellation, stale-job cleanup) is in
`docs/dev/backend-infrastructure.md`; the stores themselves and the rest of the shared
frontend conventions are in `docs/dev/frontend-core.md`; storage keys are in
`docs/dev/persistence.md`.

## The SSE hooks

- **`useJobSSE(jobId)`** — opens `EventSource` for one job, writes progress to `jobStore`. Reconnects automatically after a 3-second delay on error so progress bars don't stall from transient network hiccups; reconnection stops when the component unmounts.
- **`useAllJobsSSE()`** — opened at app root in `TopBar`, drives the global progress bar.

## Job labels

- **Job label display**: `TopBar` running-job pill shows `runningJob.label || runningJob.message || runningJob.job_type`; pending queue chips show `j.label || j.job_type`. At most **3** pending chips render; the rest collapse into a "+N more" span whose tooltip lists their labels, and a "Cancel all" button cancels every pending job **and the running one** (optimistic `updateJob(…, { status: "cancelled", optimistic: true })` + `jobsApi.cancel` each — running jobs stop cooperatively, e.g. `comfy_generate` interrupts ComfyUI and reverts the in-flight row to pending). The running-job pill has its own × to cancel just the active job. `CaptioningPage` live-progress panel shows the label above the done/total counter (`{jobProgress.label && <div>…</div>}`); its pending-queue list shows `qJob.label || fallbackLabel`. `JobProgress` in `frontend/src/types/index.ts` types `label` as `string | null | undefined` since SSE delivers JSON `null` when no label was set.

## Job completion → cache invalidation

- **Job completion → cache invalidation**: pages that trigger background jobs (e.g. `QualityPage`, `SelectionToolbar`, `ImageDetailPage`; `CaptioningPage`, `DetectionRunForm`, `ReextractFramesForm`, `GeneratePromptsModal` and `useVideoExtractJobs` do the same) watch their job ID in `jobStore` via `useEffect` and call `qc.invalidateQueries` when status becomes `"completed"`. Always follow this pattern when adding new job-triggering UI.
  - **Single job**: track `jobId: string | null`, subscribe with `useJobStore((s) => s.activeJobs.get(jobId ?? ""))`, and clear the id on a terminal status. This is the default for one-at-a-time operations (captioning, scoring).
  - **Queued/repeatable jobs (id-list pattern)**: when the UI must let the user launch another run before the previous finishes (detection — `SelectionToolbar`, `ImageDetailPage`, and `DetectionRunForm` all use this), track `jobIds: string[]` instead. Subscribe to the whole map (`useJobStore((s) => s.activeJobs)`) and, in the effect, iterate the tracked ids: for each that reached a terminal status, run its invalidations/toasts (`completed` → success, `failed` → error with `progress.message`, `cancelled` → silent) and drop it from the list via `setJobIds(prev => prev.filter(...))`. On job start, append the new id (`setJobIds(prev => [...prev, data.job_id])`) rather than replacing. Subscribing to the whole map re-renders per progress event, same as a per-id selector (each event is a new progress object), so there is no perf cost. The job backend queue is serial, so queued runs execute one after another.

  Additionally, `TopBar` watches all jobs globally — this catches the case where the user navigates away before the job finishes and the page-local watcher is no longer mounted. It fires on **any terminal status** — `TERMINAL_JOB_STATUSES` from `constants/jobs.ts`, i.e. `completed`, `failed`, `cancelled` — deduped per job id via `processedJobsRef`, not on `completed` alone: these jobs commit per item, so a cancelled run has still changed real images and the counters must catch up. It skips statuses the frontend wrote itself: cancel buttons write `optimistic: true` (see `JobProgress`), and both `useSSE` handlers force `optimistic: false` onto every server event (jobStore merges partials, so the flag would otherwise survive). Otherwise the click would consume the job's one invalidation before the backend finishes cancelling — a row can still land in between — and the real terminal SSE event would dedup into a no-op. Waiting is safe: the backend emits a terminal event even for jobs cancelled while pending. The `comfy_prompts` branch applies the same guard to its toasts.

  For the `IMAGE_MODIFYING_JOB_TYPES` — `batch_upscale`, `batch_lut`, `crop_upscale`, `crop_to_detection`, `quality_score`, `caption`, `caption_pipeline`, `comfy_generate`, `video_extract`, `video_reextract` — it invalidates `["images", dataset_id]`, the singular `["dataset", dataset_id]` (the dataset summary counts the Sidebar and gallery "All" counter read), and all four stats queries (`["dataset-stats"]`, `["tag-stats"]`, `["score-values"]`, `["tag-cooccurrence"]`). `quality_score` **and** `video_reextract` additionally invalidate `["duplicates", dataset_id]` — the first because scoring is what computes duplicate groups, the second because pass 2 re-derives `phash` from the full-resolution frame.

  `video_extract` additionally invalidates three keys the generic set does not cover: `["subfolders", dataset_id]` (all three re-extraction modes can create one), `["videos", dataset_id]` plus `["video", video_id]` (the extract endpoint commits the confirmed crop/deinterlace/trims onto the `Video` row), and `["video-frames", video_id]` (the extraction history). The last two are keyed off the payload's `video_id` — see `docs/dev/video-extract.md`.

  `video_reextract` has its own branch, with a deliberately *smaller* extra set than pass 1's: the singular `["image"]` (an open `ImageDetailPage` would otherwise keep showing the triage dimensions and thumbnail — this key is otherwise invalidated only for detection) and `["video-frames", video_id]` (the re-extract dialog can be closed mid-run, so nothing else would refresh `VideoDetailPage`'s extraction-history panel). Deliberately **not** `["subfolders", dataset_id]` or `["video", video_id]`: pass 2 creates no subfolder and touches no `Video` row, it only rewrites the frames that row lists. See `docs/dev/video-reextract-ui.md` for the dialog side of this and `docs/dev/video-reextract.md` for what the job actually rewrites.

  `DATASET_MODIFYING_JOB_TYPES` (`duplicate`, `import`) invalidate `["datasets"]`, and `import` additionally the images/dataset/stats keys for its own dataset — plus `["videos", dataset_id]`, because an import under `include_videos` creates `Video` rows. That last key is the one the page-local handlers cannot cover: an import started from `DatasetsPage` or the file browser finishes while the user is standing in the gallery, whose own completion effect never ran. `GalleryPage`'s rescan and import effects and `DatasetsPage`'s `invalidateDatasetCaches` invalidate it too, for the same reason on their own pages — `rescan` adopts clips out of `videos/` and is not in any job-type set here at all. Omitting it did not look broken: the header badge reads `dataset.video_count` off `["dataset", id]`, so the count updated while `VideoStrip` kept its pre-rescan list for the 30 s `staleTime`, leaving the page claiming videos it would not show and no route to **Extract frames**. All synchronous mutation `onSuccess` handlers (delete, save caption, bulk edit, bulk delete, move/copy to dataset) also invalidate the four stats queries directly; the row-count writers among them — both deletes, the two `GalleryPage` completion effects, `invalidateDatasetCaches`, and the duplicates panel's bulk resolve, which deletes hundreds of rows per run — do it through `invalidateDatasetContentScope` from `constants/queryKeys.ts` rather than listing the eight keys by hand. See `docs/dev/frontend-core.md` § `constants/` for the list and `docs/dev/image-similarity.md` for the resolve path.

  A separate live branch runs on each `done` increment of a `LIVE_IMAGE_JOB_TYPES` job (`caption`, `caption_pipeline`, `comfy_generate`, `video_extract`, `video_reextract`) so the gallery fills in mid-run; `comfy_generate` additionally invalidates `["subfolders", id]` and `["dataset", id]` there, and `video_extract` invalidates `["subfolders", id]` only — frames land in a subfolder that may not exist yet, but its `refresh_stats` is terminal-only, so `["dataset", id]` would be one refetch per shot returning an unchanged number. `video_reextract` adds no dataset-scoped key of its own at all: it rewrites frames in folders that already exist, so it has no subfolder to announce. Neither "only" is quite the whole story, though — the live branch **ends** with an invalidation that is not gated on job type at all: `if (progress.image_id) qc.invalidateQueries({ queryKey: ["caption", progress.image_id] })`. Both video workers emit `image_id` on their per-commit events — the `extracting` phase's emit and the `rewriting` one, cited by phase rather than by line because the phase names are stable and the numbers are not — so each done-increment of either job also refreshes that image's caption cache. Harmless and arguably right — the row did change — but it means the per-job lists above are the *job-type-specific* additions, not the complete set. That last one is deliberately not extended to the caption jobs: `Dataset.image_count`/`captioned_count` are stored columns, and only the ComfyUI worker rewrites them per row (see `docs/dev/comfyui.md`), so for the others it would be one refetch per image returning an unchanged number.

  **The live gate is a per-job monotonic high-water mark on `done`** (`captionDoneRef`), which
  makes `done` a contract rather than a display value: a job whose `done` changes meaning
  between phases silently disables its own live invalidation, because the phase with the larger
  numbers ratchets the mark past anything a later phase can emit. So a worker in
  `LIVE_IMAGE_JOB_TYPES` must count *one* thing for its whole run, and that thing must be rows
  a refetch would see — not work performed. `video_extract` is the worked example, and the
  reason the rule is written down: `docs/dev/video-extract.md` § the `video_extract` job, and
  `docs/dev/postmortems/PM-008-video-extract-progress-counter.md`. The corollary in `TopBar`'s
  own pill is that a phase counting nothing must report `total: 0` and be rendered without the
  `N / M` span at all.

## The stale-thumbnail warning, and the ordering rule behind it

`THUMBNAIL_EPILOGUE_JOB_TYPES` — `batch_lut`, `batch_upscale`, `crop_upscale`,
`crop_to_detection`, `video_reextract` — are the five jobs that re-cut an image thumbnail as
a best-effort post-commit epilogue (PM-013). Each reports `result_data["thumbnails_stale"]`,
and **one branch in `TopBar`** turns that into the only warning the user gets. It lives there,
not in the six forms that start these jobs (`LutForm`, `UpscaleForm`, `BulkEditPage`,
`SelectionToolbar`, `ImageDetailPage`'s three handlers, `ReextractFramesForm`), because
`TopBar` is always mounted — a 400-frame re-extraction is exactly the job you walk away
from — and because it already watches every terminal job deduped by `processedJobsRef`.
Those forms keep their own outcome toasts and are deliberately silent about the count;
the warning stacks beside the outcome, as `ReextractFramesForm` already does with its
`note` toast. The repair it points at is **Bulk Edit → Thumbnails**
(`docs/dev/bulk-image-jobs.md` § Rebuilding thumbnails).

Three details are load-bearing:

- **Transport is `jobsApi.get(jobId)`, not the SSE payload.** `broadcaster.emit` silently
  drops events when a subscriber's 200-slot queue fills (`backend/workers/progress.py`), so
  a piggybacked count would under-report exactly on the large runs where it matters.
- **`cancelled` is included, `failed` is not.** All four workers commit their counts above
  `raise_if_cancelled`, so a cancelled run has a durable number. A *failed* one does not:
  `workers/job_queue.py` marks the job failed from a **separate** session and never persists
  the worker's dict, so there is nothing to read.
- **The read happens after the `processedJobsRef` add**, which is what makes
  `crop_upscale`'s two terminal events toast once.

**The general rule this branch depends on: a worker that emits its own terminal status must
commit anything a completion handler will fetch above that emit.** `crop_upscale` is the
worked example — `_run_crop_upscale_replace` emits `status: "completed"` from inside the
worker, *outside* its `async with AsyncSessionLocal()` block and before `job_queue` marks
the row, so `TopBar`'s branch fires on that first event and fetches immediately. Its
`result_data` write therefore sits inside that block. `backend/tests/test_upscale_png_fallback_http.py::test_crop_upscale_commits_result_data_before_its_own_completed_event`
is the guard: a spy snapshots the row from a fresh session at the moment the event goes out.

**`regenerate_thumbnails` invalidates `["images", dataset_id]` and the singular `["image"]`,
and is deliberately *not* in `IMAGE_MODIFYING_JOB_TYPES`** — it changes no count, size or
score, so the four stats queries would all come back identical. The image queries do have to
refetch, though, and for a reason worth stating: the tiles are cache-busted by
`imagesApi.thumbnailUrlVersioned`'s `?v=${Date.parse(updatedAt)}`, so what must refetch is
the row carrying that timestamp, not the image. **Known limit**: `DatasetsPage`'s dataset-card
preview strip builds its thumbnail URLs with no `?v=` at all, so those particular tiles will
not cache-bust after a repair. Pre-existing, and unrelated to the job.

## Detection and per-image invalidation

- **Detection cache invalidation — one helper**: the dataset-scoped detection caches (`["detection-labels", id]`, `["detection-models", id]`, `["detection-stats", id]`) are always invalidated together via `invalidateDetectionQueries(qc, datasetId)` in `frontend/src/utils/detectionQueries.ts`. This is the single sanctioned way to refresh them (mirror of the backend "import the shared helper, never copy the logic" convention) — never hand-write the three `invalidateQueries` calls. The `detection-stats` key prefix-matches its live `[..., subfolder]` form. Call sites: `DetectionsPanel` (relabel/delete/merge), `DetectionBulkDeleteForm` (also invalidates `["detection-bulk-count", id]` + `["image"]`), `TopBar` on `detection` **and** `crop_to_detection` job completion (replace-mode crops now remap detection geometry), `DetectionRunForm` (on its own job completion — the id-list pattern named above), and `ImageDetailPage` detect/manual/refine completions + `createManualMutation.onSuccess`. Callers needing a per-image refresh add `["image", imageId]` alongside the helper.
- **Per-image cache invalidation (captioning)**: the `["caption", image_id]` invalidation is named for captioning because that is what it was built for, but the gate is broader — it fires for **any** `LIVE_IMAGE_JOB_TYPES` job whose payload carries `image_id`, which now includes both video job types (above). Caption SSE events carry `image_id`. While a caption job is running, `TopBar` invalidates `["images", dataset_id]` and `["caption", image_id]` on every `done` increment — this keeps the gallery and `ImageDetailPage` caption panel updated in real-time even when the user has navigated away from `CaptioningPage`. `CaptioningPage` does the same when it is mounted (harmless duplicate). The four stats queries are not invalidated per-image during captioning; instead `StatsPage` polls them every 5 s while a relevant job is active (see `docs/dev/statistics.md`).
