# Job queue, SSE and ML-lifetime findings

These are the verified findings for the job worker, progress events and model lifetimes. Read
`docs/dev/backend-infrastructure.md`, `docs/dev/frontend-jobs.md` and `docs/dev/ml-models.md`
first. The probes are `docs/dev/gpt-review/probes/backend/probe_jobs.py`, `probe_caption_unload.py`, `probe_comfy.py` and
`probe_gdino.py`. All of them are mock-level: no GPU, no model download and no real ComfyUI.
Conventions are in `docs/dev/gpt-review/README.md`.

## GPT-012 — [P2] Shutdown with a running job hangs, and queued jobs start mid-shutdown

**Verdict:** CONFIRMED. This is **the root cause of `docs/dev/backend-infrastructure.md` §
"`JobQueue.stop()` can hang — open, unexplained"**.

**Mechanism.**
1. `JobQueue._worker` has a job-level `except asyncio.CancelledError` inside `while True`
   (~`job_queue.py:195-202`).
2. `stop()` (~104-111) delivers a single `task.cancel()` and awaits the task with no timeout.
3. If that cancel lands while a job is running, including during its completion bookkeeping, the
   job handler swallows it.
4. The job is marked `cancelled`, the loop goes back to `await self._queue.get()`, and it waits
   forever. That is exactly the doc's "stuck on the pending future inside `_queue.get()`".

It looks like a race only because a cancel that lands while the worker is idle in `get()`
propagates normally. Production's lifespan (`main.py:79`) awaits this same `stop()`, so a SIGTERM
or the Restart button during any running job wedges shutdown.

**Observed** (`probe_jobs.py::test_012_*`):
- Idle stop returns.
- With an active job, stop is still pending after 1 s, the job is recorded `cancelled`, and a
  queued second job *runs*.
- The hang also happens with no queued job, and with a cancel during the post-`fn` bookkeeping.
- `test_012_executor_cancel_marks_cancelled_thread_runs`: the job is marked cancelled while its
  executor thread keeps running.

The doc's "enqueue-then-tear-down-mid-job passes repeatedly" did not reproduce: mid-job
teardown hung every time.

**Fix notes.**
- Separate queue shutdown from job cancellation. For example, set a `_stopping` flag before
  `cancel()`.
- In the job handler, re-raise if `_stopping` is set, or check `asyncio.current_task().cancelling()`.
- Stop dequeuing once shutdown begins.
- A `wait_for` bound around `stop()` is a backstop, not the fix.
- Keep ordinary per-job cancellation working, with the queue still usable afterwards
  (`raise_if_cancelled`).
- Rewrite the doc section once this is fixed.

## GPT-013 — [P2] A failure while starting a job kills the only worker

**Verdict:** CONFIRMED. NEW.

**Mechanism.** The worker dequeues, then opens a session and writes the `running` status
(~`job_queue.py:135-173`), all *before* the job's `try`. A raise there escapes `_worker`. That
skips `task_done` and cleanup, and nothing restarts the worker. The cancelled-while-queued branch
also runs outside the `try`.

**Observed** (`test_013_session_failure_kills_worker`): one injected `AsyncSessionLocal()` failure,
then the real factory restored.
- The worker task is done, with the exception.
- Both job rows stay `pending` forever.
- `_unfinished_tasks` is 2, and `current_job_id` is stuck.

**Fix notes.**
- Wrap the whole per-item lifecycle, including the start-status write and failure reporting.
- Always balance `task_done`.
- Consider a supervisor that logs and restarts the worker, but never automatically retries
  irreversible work.

## GPT-014 — [P2] A missed terminal SSE event never reconciles in the UI

**Verdict:** CONFIRMED (backend and browser). Partly known: `docs/dev/frontend-jobs.md`
documents the silent drop on a full 200-slot queue, but not the consequence.

**Backend** (`test_014_full_queue_drops_terminal`, `test_014_no_replay_for_late_subscriber`):
- With 200 undrained events, the job and global queues both hold only `running` events, and the
  `completed` event is lost (`progress.py` `put_nowait` → `QueueFull` → `pass`).
- A subscriber that arrives after `completed` gets nothing, because there is no replay and no
  snapshot (`routers/jobs.py:56-76`).
- The realistic trigger is a disconnect gap rather than queue overflow.

**Frontend** (`probes/frontend/gpt014_030.spec.ts`): SSE was aborted with `page.route` while a
240-image import finished.
- After SSE reconnects, the gallery still says 0 images and the TopBar says "Ready", while the API
  has 240.
- Only a reload fixes it.
- The control, an import with SSE unblocked, reconciles on its own.

In the source:
- `useSSE.ts`: `useAllJobsSSE` only logs `onerror`, and `useJobSSE` reconnects without fetching
  status.
- `jobStore.ts` expires jobs only on a terminal event, with no TTL.
- `TopBar.tsx` never hydrates from `jobsApi`.

**Fix notes.**
- On (re)connect, fetch the durable state of active jobs (`jobsApi.list`/`get`) and synthesize
  terminal handling for jobs that finished in the gap: invalidations and toasts.
- Optionally coalesce or never drop terminal events server-side.
- Fix together with GPT-030 (`docs/dev/gpt-review/frontend.md`), since both live in the
  terminal-handling path.

## GPT-032 — [P2] Caption-model unload during a run fails every remaining image

**Verdict:** CONFIRMED, **worse than claimed**. Partly known: `docs/dev/ml-models.md` notes that
`ModelEntry.in_use` is never set.

**Mechanism.**
- `DELETE /captioning/model/{id}/unload` (`routers/captioning.py:717-718`) and unload-all
  (`routers/models.py`, `evict_all`) are unconditional.
- `model_manager.unload` moves the weights to CPU **and `del entry.model`**.
- The caption job holds the entry with no lease.
- `routers/quality.py`'s unload has a 409 guard for a running `quality_score` job. Captioning has
  no equivalent.
- The Unload button on `CaptioningPage` stays enabled during a run.

**Observed** (`probe_caption_unload.py::test_032_unload_during_inference`, a fake Florence entry
over 2 images):
- An unload during image 1 returns 204.
- Image 1 fails with a device mismatch.
- Image 2 fails with `'ModelEntry' object has no attribute 'model'`.
- The job ends `completed` with `failed_count: 2`.

An unload at *any* point mid-run fails every remaining image.

**Fix notes.**
- Add an in-use lease spanning executor inference, honoured by `unload`, `evict_all` and LRU
  eviction. Make it defer or 409.
- At minimum, add the quality-style job guard to the captioning and unload-all routes.
- Audit the other inference consumers too: detection, tag consolidation (which already sets
  `in_use`), and upscale.

## GPT-033 — [P2/P3] ComfyUI cancel interrupts whatever is running; timeout leaves prompts queued

**Verdict:** CONFIRMED (mock). **The code contradicts the docs.**

**Mechanism.**
- `ComfyClient.interrupt()` (`services/comfy_service.py:99-106`) posts a global `/interrupt` with
  no prompt id.
- The cancel branch in `routers/comfy.py` (~1388-1393) calls it without checking that our prompt
  is the one executing.
- The timeout branch (~1394-1398) raises without removing the submitted prompt, and the next row
  is submitted on top of it.

**Observed** (`probe_comfy.py`, `httpx.MockTransport`):
- With a canvas prompt running and ours queued, cancel interrupts the *canvas* prompt, and ours
  stays queued.
- After a timeout, both of our prompts remain queued, with no `/queue` delete and no interrupt.

**Realistic in single-user use**: the sync-from-canvas workflow (`docs/dev/comfyui-sync.md`)
assumes the user also generates from the ComfyUI canvas against the same server.

**Wrong docs:** `docs/dev/comfyui.md` says "ComfyUI's own queue is never stacked" and calls
`/interrupt` "sufficient here because … nothing of ours ever waits in ComfyUI's queue".

**Fix notes.**
- Check `/queue` before acting: if our prompt is running, interrupt it; if it is pending, delete
  it via `POST /queue {"delete":[id]}`.
- On timeout, delete or interrupt our prompt and surface any cleanup failure.
- Correct the docs.

## GPT-034 — [P3, GPT said P2] Unload-all leaves SAM2's Grounding DINO resident

**Verdict:** CONFIRMED (mock). The behaviour is KNOWN: `docs/dev/detection-inference.md`
documents the process-lifetime cache. What is undocumented is that unload-all does not release
it.

**Observed** (`probe_gdino.py::test_034_unload_all_leaves_gdino`): after `evict_all()`, which
returns `['sam2']` and leaves an empty registry, `sam2_predictor._gdino_cache` still holds the fake
CUDA model and `.cpu()` was never called. It is roughly 0.7 GB in fp32; that figure is an estimate,
not a measurement.

It is unverified whether `_release_to_cpu` actually frees SAM2's own predictor. `SAM2ImagePredictor`
may have no `.cpu()`, and the call would then be swallowed. Check this with `sam2` installed.

**Fix notes.** Register Grounding DINO as an owned child of the sam2 entry, or a registry entry of
its own with an unload hook, so that unload of sam2 or of everything releases it. It needs the same
lease as GPT-032.
