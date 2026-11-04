# Frontend jobs: SSE, reconciling and reporting

This file covers the client half of the background-job flow up to the moment a job ends:
the `useJobSSE`/`useAllJobsSSE` hooks that feed `jobStore`, reconciling from the durable rows
after an SSE gap, how a running or pending job is labelled in the UI, and how a finished job
reports trouble: the partial-failure line on the Logs page and the stale-thumbnail warning.
Which caches a finished job must invalidate, and the watcher patterns that do it, are in
`docs/dev/job-invalidation.md`. The server half (job queue, SSE broadcaster, cancellation,
stale-job cleanup) is in `docs/dev/backend-infrastructure.md`; the stores themselves and the
rest of the shared frontend conventions are in `docs/dev/frontend-core.md`; storage keys are in
`docs/dev/persistence.md`.

## The SSE hooks

- **`useJobSSE(jobId)`** — opens `EventSource` for one job, writes progress to `jobStore`. Reconnects automatically after a 3-second delay on error so progress bars don't stall from transient network hiccups; reconnection stops when the component unmounts, and once the job is terminal. On every `onopen` it also fetches the job (`jobsApi.get` + `applyDurable(row, { seed: true })`), since the stream is live-only and the job may have finished while it was down.
- **`useAllJobsSSE()`** — opened at app root in `TopBar`, drives the global progress bar. It runs the reconcile below on mount and on every `onopen`, every 20 s while any job is watched, and whenever the tab becomes visible again. `onerror` re-creates the `EventSource` after 3 s when it finds it `CLOSED`. The browser retries a network drop by itself, but an HTTP error response, such as Vite's 502 while the backend restarts, closes an `EventSource` for good.

## Reconciling from the durable rows

The SSE stream has no replay (`docs/dev/backend-infrastructure.md` § SSE progress). So a job that finishes during a gap, such as a network blip, a server restart or a backgrounded tab, never delivers its terminal event, and no completion effect ever runs. Before GPT-014 the TopBar pill then stayed stuck until a reload. `frontend/src/utils/jobReconcile.ts` closes that gap from `background_jobs`:

- **`jobRowToProgress(row)`** maps a row to the `JobProgress` an event would have carried. `message` is `error_msg` on failure, `video_id`/`plan_id` come from `config`, and `comfy_prompts`' `requested`/`created` come from `result_data` (`created` is its `done`). Optional keys are spread **only when defined**, because `updateJob` merges and an explicit `undefined` would erase a value a live event already delivered. `useVideoExtractJobs`' reload re-seed uses it too, overriding `percent: 0` and its own `video_id`.
- **`applyDurable(row, { seed })`** merges one row. A store entry that is terminal and not optimistic is absorbing: nothing revives or re-fires it. A non-terminal row only seeds an id the store lacks, and only with `seed`, because SSE is fresher for a job it is already streaming. A terminal row always applies, with `optimistic: false`, through `updateJob`.
- **`reconcileJobs()`** is coalesced to one call in flight and at most one queued. It fetches the watched ids (non-terminal or optimistic entries) by `?ids=` without seeding, then `?status=pending,running` with seeding. Errors are swallowed, and ids the server does not know are left as they are.
- **`adoptJob(id)`** is `jobsApi.get` + `applyDurable(seed: true)`, for a job the user just submitted. If it already finished in an SSE gap, its terminal status still lands.

**Two properties make this safe, and both are load-bearing.** First, only ids the store already watches, plus rows active *right now*, are ever touched, so a job that finished before this page load is never synthesized and never toasts. Second, every synthesized event goes through `updateJob`, the same path SSE uses, so `TopBar`'s `processedJobsRef` dedups a terminal status that arrives from both (`docs/dev/job-invalidation.md`). `frontend/e2e/jobs-reconcile.spec.ts` pins the gap and the exactly-once toast.

## Job labels

- **Job label display**: `TopBar` running-job pill shows `runningJob.label || runningJob.message || runningJob.job_type`; pending queue chips show `j.label || j.job_type`. At most **3** pending chips render; the rest collapse into a "+N more" span whose tooltip lists their labels, and a "Cancel all" button cancels every pending job **and the running one** (optimistic `updateJob(…, { status: "cancelled", optimistic: true })` + `jobsApi.cancel` each — running jobs stop cooperatively, e.g. `comfy_generate` interrupts ComfyUI and reverts the in-flight row to pending). The running-job pill has its own × to cancel just the active job. `CaptioningPage` live-progress panel shows the label above the done/total counter (`{jobProgress.label && <div>…</div>}`); its pending-queue list shows `qJob.label || fallbackLabel`. `JobProgress` in `frontend/src/types/index.ts` types `label` as `string | null | undefined` since SSE delivers JSON `null` when no label was set.

## The partial-failure line on the Logs page

A per-item job loop that swallows a failure into a local list still *returns*, so
`job_queue.py` marks the row `completed` with `error_msg` unset — the Logs page renders it
green and the diagnosis lives only in uvicorn's stdout. The surface for that case is
`partialFailure(job)` in `LogsPage.tsx`: a `--warn` line under the `error_msg` block for any
job whose `result_data.failed_count > 0`, showing `result_data.failure_summary` when the
backend wrote one and `"N items failed"` otherwise. It is keyed on the **generic**
`failed_count` name that the import summary already uses, so a new job type lights up by
writing that key and nothing else. `result_data` is the durable half; the matching
`caption_summary` SSE event carries `failed_count` and `failure_summary` too, which is what
`CaptioningPage`'s amber badge reads while the run is still on screen — the two exist because
the SSE event is gone on reload and absent for a job the user never watched. Adding the two
keys to `JobProgress` was the whole frontend cost: the single merge site is the object spread
in `jobStore.ts`, so extra event keys already flow through untouched. See
`docs/dev/captioning.md` for what the caption loops write, and
`docs/dev/postmortems/PM-023-timeout-and-the-green-job.md`.

**Upscale and LUT write the two keys too**, beside their own `failed` count, so a run that lost images reaches the Logs page and the summary survives a reload. They build it with `backend/job_outcome.py::failure_summary(failed_count, failures, *, action)` (≤ 300 characters, up to three file names and the first error; `FAILURE_DETAIL_CAP` bounds the kept list), as restore and checkout do below. Captioning is the one writer that does not: `captioning.py::_failure_headline` names the *cause* — a provider timeout, an empty reply, a caption stripped whole — with the setting that fixes it, where a file list would say nothing useful. `ImageDetailPage` reads it into its error toast when a one-image upscale or LUT processed nothing — see `docs/dev/image-edit-job.md` § The detail page's outcome read — and `UpscaleForm`/`LutForm` append it to their warning.

**Restore and checkout use the same convention** (GPT-025, PM-026). Both routes write the
service's outcome dict to `result_data` — `failed_count`, `failure_summary` and a capped
`failed: [{file, error}]` list among the counts — and the job stays `completed`. Their
completion toasts go through `utils/restoreToast.ts::showRestoreSummaryToast(jobId,
doneMessage)`, modelled on `importToast.ts`: it fetches the job and shows "`<done>` with
problems: `<summary>` (see Logs)" when `failed_count > 0`, else the plain success toast
(also the fallback when the fetch fails). It is fire-and-forget because both callers —
`RestoreConfirmModal` and `BranchSelector` — unmount or reset the moment the job completes.

## The stale-thumbnail warning, and the ordering rule behind it

`THUMBNAIL_EPILOGUE_JOB_TYPES` — `batch_lut`, `batch_upscale`, `crop_upscale`,
`crop_to_detection`, `batch_inpaint`, `video_reextract`, `restore_snapshot`,
`checkout_branch` — are the eight jobs that re-cut an image thumbnail as
a best-effort post-commit epilogue (PM-013); restore and checkout do it in Pass 3b. Each reports `result_data["thumbnails_stale"]`,
and **one branch in `TopBar`** turns that into the only warning the user gets. It lives there,
not in the forms that start these jobs (`LutForm`, `UpscaleForm`, `InpaintForm`,
`BulkEditPage`, `SelectionToolbar`, `ImageDetailPage`'s three handlers,
`ReextractFramesForm`, `RestoreConfirmModal`, `BranchSelector`), because
`TopBar` is always mounted — a 400-frame re-extraction is exactly the job you walk away
from — and because it already watches every terminal job deduped by `processedJobsRef`
(`docs/dev/job-invalidation.md` § Job completion → cache invalidation).
Those forms keep their own outcome toasts and are deliberately silent about the count;
the warning stacks beside the outcome, as `ReextractFramesForm` already does with its
`note` toast. The repair it points at is **Bulk Edit → Thumbnails**
(`docs/dev/bulk-image-jobs.md` § Rebuilding thumbnails).

Three details are load-bearing:

- **Transport is `jobsApi.get(jobId)`, not the SSE payload.** A terminal status can also
  be synthesized from the durable row after an SSE gap (§ Reconciling from the durable
  rows), and a count piggybacked on the live event would be missing from that one. The
  broadcaster used to drop events outright when a subscriber's 200-slot queue filled;
  it no longer drops a terminal one, but the fetch stays the transport.
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
and is deliberately *not* in `IMAGE_MODIFYING_JOB_TYPES`**
(`docs/dev/job-invalidation.md`) — it changes no count, size or
score, so the four stats queries would all come back identical. The image queries do have to
refetch, though, and for a reason worth stating: the tiles are cache-busted by
`imagesApi.thumbnailUrlVersioned`'s `?v=${Date.parse(updatedAt)}`, so what must refetch is
the row carrying that timestamp, not the image. **Known limit**: `DatasetsPage`'s dataset-card
preview strip builds its thumbnail URLs with no `?v=` at all, so those particular tiles will
not cache-bust after a repair. Pre-existing, and unrelated to the job.
