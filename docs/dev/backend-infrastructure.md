# Backend infrastructure: server lifecycle, jobs & SSE

This file covers production frontend serving, server shutdown/restart control, the CI workflows, the SSE progress broadcaster and the `GET /jobs/` filters, job cancellation, and how the job queue stops at shutdown. The database — engine, schema conventions, indexes, the drift check and the startup backup — is in `docs/dev/database.md`. Venv/dependency/GPU setup (`manage.ps1`/`manage.sh`) moved to `docs/dev/environment-setup.md`.

### Frontend serving (production)

`backend/main.py` serves the built React app after all API routers are registered. Two parts:

1. `app.mount("/assets", StaticFiles(directory=frontend_dist/"assets"))` — serves JS/CSS/fonts at their exact paths with correct content-type headers.
2. `@app.get("/{full_path:path}")` catch-all — for any unmatched path, serves the file directly if it exists in `frontend/dist/` (favicon, manifest, etc.), otherwise returns `index.html` so React Router handles client-side navigation.

**Do not replace this with a bare `StaticFiles(html=True)` mount.** Starlette's `html=True` only falls back to `index.html` for directory-style paths (`/`); it returns 404 for deep URLs like `/datasets/abc123` on hard refresh. The catch-all route is required for SPA routing to work correctly.

**The catch-all is containment-gated, and the order matters** (PM-015). `full_path` is raw client input reaching a `FileResponse`, so the handler resolves the candidate and requires `is_relative_to(frontend_dist)` **before** `is_file()`; anything outside falls through to `index.html`, which is the right answer for a path naming nothing in dist anyway. Two vectors made this an arbitrary-file read until 2026-07-30, and both are easy to talk yourself out of:

- **`%2e%2e`.** The usual reasoning is "clients normalize `../` away" — they do, which is why this survived so long, but percent-encoded dots survive that normalization and Starlette decodes the path parameter *after* routing. The handler receives real `..` segments.
- **A leading slash.** `Path.__truediv__` **discards the left operand** when the right is absolute, so `frontend_dist / "/etc/passwd"` *is* `/etc/passwd`. Sent as `//etc/passwd`, it needs no dots at all.

`backend/tests/test_spa_fallback_containment.py` pins both. Note it drives the second one through a hand-built ASGI scope rather than the httpx client: httpx parses `//foo` as a URL *authority* and never sends it as a path, so an httpx-based test for that vector passes against the unguarded handler and proves nothing. uvicorn applies no such reinterpretation. The block lives in `main.mount_spa(app, dist)`, which `main.py` calls only when `frontend/dist` exists; the tests call it against a throwaway `dist` under `tmp_path`, so they run on every pytest job with no frontend build. They used to skip whenever `dist` was absent, which meant every CI run, and the raw-scope helper's `receive()` hung Starlette ≥1.8's `FileResponse` disconnect listener the moment they were enabled. Keep the helper's blocking `receive()`, its `spec_version` and its timeout. See PM-028.

### Request guard: Host and Origin

`backend/request_guard.py::RequestGuardMiddleware` is the app's only middleware, and therefore the first thing every HTTP request and WebSocket handshake meets: the API, the SSE streams and the SPA catch-all alike. It exists because the API is unauthenticated (GPT-007): loopback binding (see `docs/dev/environment-setup.md` § Bind address and `--lan`) keeps other machines out but not other *websites* in the user's browser.

- **Host (DNS rebinding), every method.** A rebound `evil.example` is same-origin to the browser, so only the `Host` header gives it away. Allowed: any IP literal, `localhost` and `*.localhost`, `socket.gethostname()` (plus its first label and `<name>.local`), and the comma-separated `CRUCIBLE_ALLOWED_HOSTS` setting. A missing `Host` passes. No DNS lookups ever. The configured set is re-parsed only when the setting's string changes, so a test that monkeypatches it is honoured.
- **Origin (CSRF), POST/PUT/PATCH/DELETE only.** CORS stops a foreign page *reading* a response, never the request itself running — a cross-site `fetch` could POST `/api/v1/shutdown`. An `Origin` passes iff its host and port equal the request `Host`'s (scheme-agnostic; no port, `:80` and `:443` compare equal). There is no origin allowlist. `Origin: null` is refused. With no `Origin`, the request is refused only when `Sec-Fetch-Site` is `cross-site` or `same-site` (ports are not part of a site, so another localhost port is "same-site"). No browser headers at all — curl, httpx, Playwright's `request` fixture — passes.

Both refusals are a 403 JSON `{"detail": …}` naming the host or origin. A WebSocket handshake gets both checks, Origin included whatever the method, and is refused by closing it (code 1008) before `websocket.accept`, which the server answers as a 403. CORS never covered WebSockets, so the first WS route would otherwise be open to every website.

**No CORS, on purpose.** Every page is served from the origin it calls. That includes the Vite dev server: its proxy forwards the browser's own `Host` (`changeOrigin: false` in `frontend/vite.config.ts`), so a dev page's `Origin` and `Host` agree on `:5173`, locally and from a `dev --lan` client alike. The app once ran `CORSMiddleware` allowing `http://localhost:5173` with credentials, and the guard briefly shared that list. That trusted *whatever* listens on `:5173`, by default any other project's Vite dev server, to send changes and read every response. Do not reintroduce either; a legitimate cross-origin consumer would need a deliberate design, not an allowlist entry.

**Pure ASGI, never `BaseHTTPMiddleware`.** The latter runs the downstream app in a separate task and pipes the body through a memory stream, which is known to interfere with streaming responses, and the progress bar's SSE stream lives for the whole session. The guard only reads `scope["headers"]` and either forwards `receive`/`send` untouched or answers itself. Request-level tests must therefore use an allowed host: `conftest.api_env` and the three files that build their own client use `base_url="http://localhost"`. `backend/tests/test_request_guard_http.py` covers the rules, including a shutdown/restart from a foreign origin that must not reach a (patched) `os.kill`.

### App-level exception handlers

Three `@app.exception_handler` registrations sit in `backend/main.py` between the middleware registrations and the router includes. Both are app-level for the same reason: the exposure they close belongs to a *class* of route, not to one route, and a per-route defence is a rule every future route has to remember.

| Exception | Answer | Why here |
|---|---|---|
| `utils.FileInUseError` | 409 with the message as `detail` | Every site adopting `utils.unlink_retrying`/`rename_retrying` inherits the translation, including the `filesystem.py` routes that are not converted yet. 409 not 500 — the request was well-formed and a later retry can work. See CLAUDE.md § *A file the app is still serving...* and `docs/dev/postmortems/PM-021-served-file-handle-blocked-windows-delete.md` |
| `model_manager.ModelInUseError` | 409 with the message as `detail` | `model_manager.unload` is reachable from several routes (`DELETE /captioning/model/{id}/unload`, among others), and a model a running job holds a lease on must answer "retry when the job finishes" from every one of them rather than 500 — or, before leases, break the job. See `docs/dev/ml-models.md` § Leases |
| `RequestValidationError` | 422 with `input` and `ctx` stripped from every error entry | FastAPI's default handler returns pydantic's entries verbatim, and pydantic puts the value it *refused* in `input` — so a rejected secret came back in the response body |

**The 422 redaction.** `PATCH /settings/secrets` with an over-long `hf_token` echoed the submitted token, directly contradicting the property `docs/dev/settings.md` § API Keys states and `test_settings_secrets.py` asserts. `POST`/`PATCH /providers` had the same shape via `OpenAIProviderCreate/Update.api_key`. Stripping `input` (and `ctx`, which carries the same value for some error types) makes "the plaintext appears in no response" a property of the app rather than of the paths someone remembered to check.

Nothing is lost by the strip: pydantic renders a `value_error` as `"Value error, "` + the `ValueError`'s own text and a constraint failure as `"String should have at most 500 characters"`, so `msg` already restates what `ctx` held, and `loc` still names the field — which is all `frontend/src/utils/apiError.ts` reads (it dispatches on the *shape* of `detail`, never on the status). `jsonable_encoder` is required and matches FastAPI's own default: `loc` arrives as a tuple, and a couple of entries are hand-built outside pydantic. The one cost is that a test passing `r.text` as a pytest failure message no longer prints the rejected value.

Scope is rejected *values* only. A secret reaching a 500 traceback or a log line is a different exposure and neither handler touches it.

### Server control endpoints

Three endpoints are registered directly in `backend/main.py` (not via a router), immediately before the frontend-serving block:

| Endpoint | Behaviour |
|---|---|
| `POST /api/v1/shutdown` | Touches `.shutdown` sentinel, then spawns a daemon thread that sends `SIGTERM` to the current process, triggering uvicorn's graceful shutdown. The sentinel lets the manage script distinguish a clean shutdown from a crash. |
| `GET /api/v1/health` | Returns `{"status": "ok", "start_time": <float>}`. `start_time` is `time.time()` captured once at module load (`_START_TIME`). Used by the frontend to detect when a restarted server is a *new* process. |
| `POST /api/v1/restart` | Removes `.shutdown` (if present), creates `.restart` sentinel file (`_RESTART_SENTINEL = Path(__file__).parent.parent / ".restart"`), then sends `SIGTERM`. The manage-script restart loop detects the sentinel after uvicorn exits and re-launches the server. |

**Restart loop** — `Cmd-Start`/`cmd_start` in `manage.ps1`/`manage.sh` wrap the uvicorn call in a `while` loop. After uvicorn exits they check for `.restart`; if present they delete it and restart, otherwise they break. `Cmd-Dev`/`cmd_dev` have the same loop (bash uses a background subshell so the frontend Vite server can run concurrently). Stale `.restart` and `.shutdown` files left by a crash are deleted at the top of each start before the loop begins. The uvicorn call is written as `python -m uvicorn ... || true` in `manage.sh` so that `set -euo pipefail` does not abort the loop on a non-zero exit before the sentinel check runs.

**Terminal auto-close on shutdown** — after the restart loop exits (no `.restart` sentinel), `Cmd-Start` in `manage.ps1` checks for `.shutdown`. If found, it deletes the file and calls `exit 0`. `Crucible.bat` only runs `pause` when the PowerShell exit code is non-zero, so a clean shutdown closes the terminal window automatically while a crash keeps it open so the user can read the error output.

**Frontend restart flow** (`TopBar.tsx` `handleRestart`): (1) fetch `/api/v1/health` to record the current `start_time`; (2) POST `/api/v1/restart`; (3) poll `/api/v1/health` with `{ cache: "no-store" }` every second until the response carries a *different* `start_time` (confirming a new process, not the dying old one); (4) `window.location.reload()`. While this runs, a full-screen overlay shows the animated `CrucibleMark`. Both the restart and shutdown buttons are disabled while `restarting || shuttingDown`.

**The restart poll has no deadline, deliberately.** It previously gave up after 60 s and reset the UI "so the user can retry". That silently strands the page: a cold start importing torch can exceed 60 s, and a server not running under the restart loop (see above) needs a manual relaunch — in both cases the server comes back *after* the poll has stopped, so the page never reloads and looks hung. Polling now continues indefinitely; after `RESTART_SLOW_MS` (25 s) the overlay adds an explanation and a manual "Reload now" button rather than giving up. `restarting` is never reset to `false` — every exit from this state is a page load. Do not reintroduce a cutoff.

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

`main.py`'s lifespan awaits `job_queue.stop()` on every shutdown, which is what SIGTERM, Ctrl+C and both TopBar power buttons reach. It used to hang forever whenever a job was running (GPT-012, `docs/dev/postmortems/PM-027-shutdown-cancel-swallowed-by-job-handler.md`). `stop()` delivered one `task.cancel()`, and the worker's per-job `except CancelledError` could not tell it from a cooperative `raise_if_cancelled`. So it recorded the job `cancelled`, went back to `await self._queue.get()`, and waited there; the next queued job even started mid-shutdown. The cancel propagated only when it landed while the worker was idle in `get()`, which is why it looked like a race. It never was one: a running job wedged shutdown every time.

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

