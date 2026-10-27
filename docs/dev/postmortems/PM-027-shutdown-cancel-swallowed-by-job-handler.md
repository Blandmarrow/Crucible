# PM-027: the job handler swallowed the shutdown cancel

### Symptom

Shutting down or restarting the server while a background job ran hung forever. That
covered SIGTERM, Ctrl+C, and both TopBar power buttons, whose manage-script restart loop
waits for uvicorn to exit. The lifespan sat in `await job_queue.stop()`, the event loop idle in
`select()`. Worse, the next queued job **started** mid-shutdown, and the job that was running
was recorded `cancelled` as if the user had stopped it. An external review found it (GPT-012),
along with a sibling (GPT-013): one failure while *starting* a job, in the session open or
the `running` write, killed the only worker for good, and every later job sat `pending`
forever.

The SSE half (GPT-014, GPT-030) surfaced in the same pipeline. A job that finished while
the stream was down left a stuck TopBar pill until a reload, and a folder import that
failed in the worker showed nothing but "Import started".

### Root cause

`JobQueue.stop()` delivered one `task.cancel()` to the worker and awaited it with no
bound. The worker's loop was `while True: get(); … try: await fn() except CancelledError:
mark cancelled`. That per-job `except` exists for cooperative cancellation, because
`raise_if_cancelled` raises `CancelledError` by hand. It could not tell that from a cancel of
the **task**, so it absorbed the shutdown, marked the job, and went back to
`await self._queue.get()` to wait for work that would never come. The cancel propagated
only when it happened to land while the worker was already idle in `get()`.

That is why the earlier write-up in `docs/dev/backend-infrastructure.md` read it as a
**race**: "reproduced 4 runs in 5", "enqueue-then-tear-down-mid-job passes repeatedly". The
second claim did not reproduce. The probe hung on every mid-job teardown, and the 1-in-5
pass was a teardown that happened to find the worker idle. Its proposed cure, a `wait_for`
around the await, would have bounded the hang, but at the cost of a second cancel that the
same handler would also have swallowed.

GPT-013 has the same shape one level up: the per-item lifecycle was not wrapped, so the
only loop in the process had exits nobody intended.

### Generalizable rule

**In a long-lived loop, a per-item `except CancelledError` must re-raise when the task
itself is being cancelled.** Check `asyncio.current_task().cancelling() > 0`. It is zero
after a hand-raised `CancelledError` and non-zero after `task.cancel()`.

- **Keep the stop flag separate from job cancellation.** A `_stopping` flag set before
  anything is cancelled stops the loop from taking new work, whatever the handler does.
- **Give `stop()` a bound that always returns.** Use `asyncio.wait({task}, timeout=…)`,
  never `wait_for`, whose timeout is itself a cancel. Send exactly one `cancel()` after a
  grace period, then abandon what will not die.
- **Wrap the whole per-item lifecycle** of a singleton worker, bookkeeping included, and
  balance `task_done()` in a `finally`. A worker that can die on one item is a queue that
  silently stops.

A related rule for the client: **a live-only event stream needs a reconcile from durable
state.** Every terminal transition must be recoverable after a gap, and it must re-enter
the same handler the live event uses, so dedup holds.

### Why it wasn't caught the first time

The test harness rebuilt the queue and re-`start()`ed the singleton per test. That made
the hang look like a harness artefact ("whether production can hit it is unverified"), and
the guidance became "always `wait_for_job` before returning", which hid the bug from every
test that followed it. No test stopped the queue with a job in flight and asserted that
`stop()` returned. The review question that would have caught it: *what happens to this
`except CancelledError` when the task running it is cancelled?*

### Fix

On branch `fix/gpt-review`.

- **`JobQueue.stop(grace, unwind)`** (GPT-012) sets `_stopping`, asks the running job to stop
  through the cooperative flag, and waits a grace: 10 s, or 120 s for the snapshot,
  branch, restore and checkout jobs, which never poll the flag. It then sends one
  `task.cancel()` and waits 5 s more. It always returns, and queued jobs stay `pending` for
  `mark_interrupted_jobs`.
- **The handler re-raises a task cancel.** The job `stop()` interrupted is recorded `failed`
  with `INTERRUPTED_MSG`, the message the startup sweep shares.
- **The item lifecycle is wrapped** (GPT-013). A failed start fails just that job, a failed
  final write falls back to `failed`, and a worker death is logged CRITICAL.
- **SSE** (GPT-014): a terminal event is never dropped from a full subscriber queue, and
  `GET /jobs/` gained `status`/`ids` filters. `frontend/src/utils/jobReconcile.ts` catches
  the store up from the durable rows on reconnect, on a poll and on tab focus.
- **Imports** (GPT-030): TopBar reports every outcome for all three entry points, which adopt
  their job on submit.

Guarded by:

- `backend/tests/test_job_queue_lifecycle.py`
- `backend/tests/test_progress_broadcaster.py`
- `backend/tests/test_jobs_list_filters_http.py`
- `frontend/e2e/jobs-reconcile.spec.ts`

The mechanism is in `docs/dev/backend-infrastructure.md` § Shutdown: stopping the job queue,
and the client half in `docs/dev/frontend-jobs.md` § Reconciling from the durable rows.

### Status & date

MITIGATED. What remains is by design:

- An executor thread cannot be stopped, so the interpreter's exit can wait for one
  in-flight blocking call.
- A job that swallows `CancelledError` and keeps looping is abandoned after the unwind.
- The terminal events of the shutdown itself reach no client, because sse-starlette
  closes the streams first. The reconcile covers them on reconnect.

Last reviewed for staleness: 2026-10-27.
