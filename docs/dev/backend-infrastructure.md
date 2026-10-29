# Backend infrastructure: the job queue, SSE & CI

This file covers the job queue and what surrounds it: the CI workflows, the SSE progress broadcaster and the `GET /jobs/` filters, job cancellation, how the job queue stops at shutdown, and the startup cleanup and retention sweeps. What wraps every request — the Host/Origin request guard, production frontend serving, the app-level exception handlers and the shutdown/restart control endpoints — is in `docs/dev/http-edge.md`. The database — engine, schema conventions, indexes, the drift check and the startup backup — is in `docs/dev/database.md`. Venv/dependency/GPU setup (`manage.ps1`/`manage.sh`) moved to `docs/dev/environment-setup.md`.

### CI workflows

**The CI jobs.** Six workflows, all on pull requests: `backend-tests.yml` runs the pytest suite
and the `migration-drift` job (`docs/dev/database.md` § Migration drift check); `backend-lint.yml` runs `ruff check backend` as its own
status check, kept separate so `backend-tests.yml`'s carefully curated dependency list stays
intact; `frontend-build.yml` runs `npm run build` then `npm run typecheck:e2e` (both blocking)
and then `npm run lint` with `continue-on-error: true`, so lint is reported and can never fail
the check while the pre-existing react-hooks backlog stands; `e2e-smoke.yml` runs Playwright,
whose webServer is the Node launcher `frontend/e2e/serve.mjs` (Node rather than bash so it
also starts on Windows); `check-docs.yml` runs `scripts/check_docs.py --self-test` and then
`scripts/check_docs.py` on every PR, unfiltered, where only its FAIL checks block (broken
links and anchors, missing inline-code paths, the `@docs/` footgun, a stale link label); and `check-mark.yml` runs `scripts/check_mark.py`, the brand-mark drift
check (`docs/dev/styling.md`), only when a file it compares changes.

**The Windows job.** `backend-tests.yml` also runs the same suite on `windows-latest` as
`backend-tests-windows` — the platform most users run, and the one whose filesystem rules
(PM-021's open-file unlink, privileged symlinks, `\` separators) the Linux job cannot see. It
mirrors `backend-tests`' installs under `shell: bash` and is **`continue-on-error: true` until
it has run green once**; then that line comes out and it blocks like the Linux job. The
workflow also carries `workflow_dispatch`, so it can be run on a branch from the Actions tab
before a PR exists. Its first two steps parse `manage.ps1` under Windows PowerShell 5.1 and
PowerShell 7 (`Parser::ParseFile`, failing on any error); nothing else in CI can, since the Linux
runners have no PowerShell. Those two steps sit under the same `continue-on-error` and do not
run the script. Tests that need a real symlink carry `conftest.needs_symlinks`
(`docs/dev/video/tests.md` § cv2 in CI, and the skip convention).

### SSE progress

`ProgressBroadcaster` (singleton in `workers/progress.py`) maintains per-job `asyncio.Queue`s. Emitting a progress event pushes to the job-specific channel and the `"all"` channel. A 25-second heartbeat keeps proxies from closing idle connections. Per-job streams (`GET /jobs/stream/{job_id}`) close when status becomes one of `TERMINAL_STATUSES` (`completed`, `failed`, `cancelled`). The global stream (`GET /jobs/stream/all/events`) uses `stop_on_terminal=False` and stays open for the session lifetime. All progress events include `dataset_id` (nullable for jobs with no associated dataset) and `label` (the job's display name, nullable — always taken directly from `job.label` on the in-memory `BackgroundJob` object).

**A terminal event is never dropped** (GPT-014). Subscriber queues are unbounded, and `emit` enforces the `SUBSCRIBER_BACKLOG_CAP` of 200 for **non-terminal** events only. A reader that stops draining loses progress ticks, which the next tick supersedes. It never loses the event that ends a job, which nothing supersedes, so the overshoot is at most one event per job. The old 200-slot `maxsize` dropped whatever came next, the terminal event included.

**There is no replay and no snapshot.** A subscriber sees only what is emitted while it is subscribed, so a client that was disconnected (a network gap, a restart, a backgrounded tab) catches up from the durable rows instead. See `docs/dev/frontend-jobs.md` § Reconciling from the durable rows. `backend/tests/test_progress_broadcaster.py` pins both halves.

### Listing jobs: the `GET /jobs/` filters

`GET /jobs/` returns rows newest first, with `limit` (1–500, default 50). Two optional filters exist for the reconcile, and they are ANDed:

- `status`, a comma-separated subset of `pending,running,completed,failed,cancelled`. An unknown value is a 422.
- `ids`, comma-separated job ids, at most 500 (422 above). An `ids` naming nothing matches nothing, never "no filter".

`frontend/src/api/jobs.ts` exposes them as `jobsApi.list(limit, { status?, ids? })`, and the reconcile sends ids in chunks of 100 to keep the request line short. `backend/tests/test_jobs_list_filters_http.py` covers both filters, both together, and the two 422s.

### Job cancellation

`DELETE /jobs/{job_id}` calls `job_queue.request_cancel(job_id)`, which adds the id to an in-memory `set` on the single-process, single-worker `JobQueue`. Running loops poll this set cooperatively — no DB access from `ml/` modules.

- **Only a `pending` job gets `status="cancelled"` written at request time** — the worker's dequeue check reads that status to skip it, and nothing is running to report a different outcome. A **running** job's status is left alone and the worker writes the real final status when the job actually stops.
- **Why the running job's row is not marked terminal immediately**: cancellation is cooperative, so a running job keeps working until its next poll (for `comfy_prompts`, a whole LLM call — up to the provider's `timeout_s`, default 300 s). Marking the row terminal at request time put a `cancelled` badge on the Logs page for a job still doing work, and was wrong outright when the job went on to finish what was asked. It also freed the "one job at a time" guards (`comfy_generate`/`comfy_prompts` per plan, `rescan` per dataset) while the old job was still writing.
- **Two check helpers**: `raise_if_cancelled(job_id)` raises `asyncio.CancelledError` (used by loops that commit per item or produce additive file output, where nothing needs preserving); `cancel_requested(job_id)` is a non-raising check (used by loops that `break` → commit partial progress → then `raise_if_cancelled` after the commit so the worker still marks the job `cancelled`).
- **Scope**: all per-item jobs are cancellable and **keep partial progress** (work finished before the stop is committed). A job cancelled while still queued is skipped by the worker before it starts. Snapshot, restore, and branch-checkout jobs are intentionally **not** cancellable (they must complete atomically to leave the version store consistent). The flag is cleared in `_run_item`'s `finally`, which the pending-skip path runs through too. A shutdown uses this same flag to ask the running job to stop (§ Shutdown: stopping the job queue).

### Shutdown: stopping the job queue

`main.py`'s lifespan awaits `job_queue.stop()` on every shutdown, which is what SIGTERM, Ctrl+C and both TopBar power buttons reach (the routes behind the buttons are `docs/dev/http-edge.md` § Server control endpoints). It used to hang forever whenever a job was running (GPT-012, `docs/dev/postmortems/PM-027-shutdown-cancel-swallowed-by-job-handler.md`). `stop()` delivered one `task.cancel()`, and the worker's per-job `except CancelledError` could not tell it from a cooperative `raise_if_cancelled`. So it recorded the job `cancelled`, went back to `await self._queue.get()`, and waited there; the next queued job even started mid-shutdown. The cancel propagated only when it landed while the worker was idle in `get()`, which is why it looked like a race. It never was one: a running job wedged shutdown every time.

**The state machine.** Two flags keep the two kinds of stop apart. `_stopping` means the queue is shutting down, and the cancel *flag* (`request_cancel`) means a job should stop. `stop(grace=None, unwind=STOP_UNWIND_SECONDS)`:

1. sets `_stopping`, so the worker loop (`while not self._stopping`) dequeues nothing more. Queued jobs never start and stay `pending` for the next startup's sweep (below);
2. if the worker is idle, sends `task.cancel()`, which propagates out of `get()`;
3. otherwise asks the running job to stop through the cooperative flag, and records it as `_stop_interrupted` unless the user had already cancelled it. It then waits up to `grace` with `asyncio.wait({task}, timeout=…)`. That must not be `wait_for`, which cancels on timeout and would be a second, uncounted cancel;
4. past the grace, sends **exactly one** `task.cancel()` and waits `unwind` more;
5. if the task is still running (a job that swallows `CancelledError`), logs an error and abandons it. **`stop()` always returns**, within about grace + unwind.

**The per-job handler** reads `asyncio.current_task().cancelling() > 0`, which is zero after a cooperative raise and one after a task cancel. Either way the job stop asked to stop is recorded `failed` with `INTERRUPTED_MSG`, any other job `cancelled`, and a task cancel is then **re-raised** so the worker exits. A cancel that lands in the completion bookkeeping propagates out of `_finish` and leaves the row `running`. `Task.cancelling()` exists from Python 3.11; CI runs 3.12, which still has no `asyncio.Queue.shutdown()`.

**The per-type grace.** `STOP_GRACE_SECONDS` is 10 s. `NON_COOPERATIVE_GRACE` gives 120 s to `create_snapshot`, `create_branch` (its job form always snapshots), `restore_snapshot` and `checkout_branch`. These never poll the flag (see § Job cancellation), so their grace is pure waiting, and forcing one mid-run leaves a half-written snapshot or a half-applied dataset. An explicit `grace` overrides both, which is what `api_env`'s teardown does with `stop(grace=2.0)`.

**Every item is wrapped, too** (GPT-013). The dequeue, the session open and the `running` write used to sit outside the job's `try`, so one failure there killed the only worker and every later job sat `pending` forever. Now `_run_item` covers the whole lifecycle. A failed start fails just that job ("Could not start job: …"), and a failed final write gets one fallback write as `failed`. The terminal event is emitted either way, and `task_done()` sits in the worker's `finally`. A done-callback logs at CRITICAL if the worker task ends while not stopping; there is no auto-restart, because the only exits left are ones a restart would not cure.

Two limits follow, both by design:

- **An executor thread cannot be stopped.** A forced cancel abandons the `run_in_executor` future, and the job is recorded interrupted, but the thread runs on: the interpreter's exit waits for one in-flight blocking call such as an inference or a write.
- **The shutdown's own terminal events reach nobody.** sse-starlette closes the streams as shutdown begins, before the lifespan's `stop()` runs. The UI learns how those jobs ended from the client reconcile after it reconnects (`docs/dev/frontend-jobs.md` § Reconciling from the durable rows).

`backend/tests/test_job_queue_lifecycle.py` pins all of it: idle and cooperative stops, the forced stop, a cancel in the completion bookkeeping, the executor limit, a user cancel that stays `cancelled`, the long grace, restart after stop, and the GPT-013 start failure.

### Stale job cleanup at startup

The queue is in-memory, so a shutdown/restart orphans any `running`/`pending` job rows — nothing will ever resume them, and they render as stuck-forever in the UI. `mark_interrupted_jobs()` (in `workers/job_queue.py`, called from the `lifespan` hook before `job_queue.start()`) marks them all `failed` with `INTERRUPTED_MSG`, "Interrupted by server shutdown or restart". That is the same constant `stop()` writes for the job it interrupts, so the two read identically on the Logs page. After a bounded shutdown the sweep catches only the queued jobs and the rare row a forced cancel left `running`.

### Job retention sweep

`background_jobs` otherwise only grows — every captioning/export/detection/import run appends a row and nothing removes them. `sweep_old_jobs()` (`workers/job_queue.py`, called from `lifespan` immediately **after** `mark_interrupted_jobs()`, wrapped so a failure can't block startup) deletes rows that are `completed`/`failed`/`cancelled` **and** older than `JOB_RETENTION_DAYS` (30) **and** outside the newest `JOB_RETENTION_MIN_KEEP` (500) rows overall. The min-keep floor is a second `created_at` bound derived from an `OFFSET 499 LIMIT 1` query, so an install idle for a year still shows its last runs; ordering matters — the interrupted-jobs pass runs first so nothing eligible is still labelled `running`. Unfinished rows are never touched. Consumers are safe by construction: `routers/jobs.py` lists at most 500 newest, LogsPage requests 200, and the comfy duplicate-job guards query only `pending`/`running`. `BackgroundJob.created_at` is indexed (`ix_background_jobs_created_at`, migration `d8200045f01b`) for both the sweep's filter and the listing's `ORDER BY`. User-facing consequence — the 30-day Logs history horizon — is documented in `docs/workspace.md` (§ Logs).

### Per-image start events for slow jobs

`batch_upscale` and `batch_lut` emit a progress event (`"Upscaling {filename}…"` / `"Applying LUT to {filename}…"`, `done=i`) *before* each image is processed, not only after. On CPU-only machines (e.g. the dev container — no GPU) a single upscale can take minutes; without the pre-start event a small job sits at "Starting…" the whole time, looking hung. Note cancellation is cooperative and only checked between images, so cancelling mid-image doesn't interrupt the current upscale.

