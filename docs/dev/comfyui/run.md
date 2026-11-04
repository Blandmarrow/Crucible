# ComfyUI run: the comfy_generate job

What happens after *Run* is pressed: `POST /comfy/run` and its 409, the run's folder tree
declared before the loop, the per-row loop (submit, poll, download, register, caption), where
each image lands and what it is called, the per-row stats refresh, provenance stamping, the
failure and cancel semantics, and how a cancelled or failed row's prompt is taken back out of
ComfyUI. Plans, rows, `ComfyClient` and the router's other endpoints are in
`docs/dev/comfyui/queue.md`, which also keeps § Gotchas; the page's side of a run (`ComfyRunBar`,
`activeRuns`) is `docs/dev/comfyui/page.md`; LLM prompt generation is `docs/dev/comfyui/prompts.md`.

### The `comfy_generate` job

`POST /comfy/run` (`{plan_id, row_ids?, subfolder, set_caption, label?}`): explicit `row_ids`
run regardless of status; otherwise all `pending` rows in `sort_order`. A 409 guards against a
second pending/running run for the same plan — matched on `job_type` **and**
`config["plan_id"]`, so a prompt-generation job never blocks a run (see
`docs/dev/comfyui/prompts.md`). The pre-flight is `_run_preflight` and the rest of the route is
`_start_run`, which a blueprint's Top up calls with explicit row ids
(`docs/dev/comfyui/structured.md` § Top up). The 409 check and the job's commit hold the
per-plan `_plan_lock`, which blueprint Apply shares. Standard job pattern (nested `_run(job_id)`
closure, own `AsyncSessionLocal`, auto label `ComfyUI: {plan} — N rows`).

**The run's whole folder tree is declared once, before the row loop.**
`run_targets = {join_subfolder(subfolder, r.subfolder or "") for r in run_rows}` (with `""`
discarded) goes to `dataset_service.declare_subfolders`, which fills in every ancestor. Doing
it in the loop is the trap: `declare_subfolders` **commits**, which inside the loop would make
a half-written row durable and break the per-row atomicity the failure handler's rollback
depends on. Before the loop the session holds only reads, so the commit is a no-op on an empty
unit of work, and `expire_on_commit=False` leaves `ds`/`run_rows` usable. It is also the better
behaviour — the tree appears in the gallery sidebar as the run starts rather than filling in
row by row; a cancelled run leaves declared-but-empty folders, which `list_subfolders` already
supports and the user can delete. Discarding `""` matters because `declare_subfolder("")` would
append a nameless entry that `list_subfolders` then merges into every listing forever.
`join_subfolder` rather than `normalize_subfolder` — see `docs/dev/comfyui/queue.md` § Gotchas.

Per row, sequentially (one of ours outstanding at a time — a failed or cancelled row's prompt is
taken out of ComfyUI before the next is submitted, see § Removing our prompt): mark `running` →
`patch_workflow` → submit → poll history every 1 s (30-min deadline, `PER_ROW_TIMEOUT_S`) →
download each output image → write into `{dataset}/images/` named
`{row_folder_leaf}_001.ext` via `unique_filename_with_thumb` → `_register_file_sync` in an executor
(it returns a `RegisteredFile` NamedTuple `(info, gen_meta, provenance)`, read by **attribute, never
unpacked** — that was PM-005; the ComfyUI path ignores `.provenance` and stamps its own)
(metadata + thumbnail; the PNG's embedded `prompt`/`workflow` chunks give `generation_metadata`
provenance for free) → optional caption (effective prompt = row override or template value,
assigned **via ORM attribute** + `_write_txt_sidecar`, `captioned_by="comfyui"`) → row
`completed`, commit per row, then `refresh_stats` for that row (whenever it put image rows in
play — including a failed row whose images were rolled back, where the recount corrects the column).
`result_data` (`{created_image_ids, failed_row_ids, completed, failed}`) at the end.

Two per-row locals decide where the image lands and what it is called, computed at the top of
the loop body and **never hoisted** — the broad handler's `populate_existing` re-select
repopulates `row.subfolder` after a rollback, so a value read once before the loop goes stale.
`row_subfolder = join_subfolder(subfolder, row.subfolder or "")` is the `Image.subfolder`
stamped on every output. `row_stem` is `slugify_filename` of the row's **own** folder leaf
(`row.subfolder.rsplit("/", 1)[-1]`), falling back to the plan slug when the row has no folder:
the leaf, not the combined path, because naming files after the combined path would silently
rename every future file for a user who only ever sets a run-level base folder. The `own_leaf`
guard is an explicit emptiness check, not `slugify_filename(x) or fallback` — that helper ends
in `or "image"` and can never return something falsy. **The filename and thumb-stem occupancy
sets stay one set per run**: they guard `uq_dataset_filename` and the flat `thumbnails/`
directory, both per-dataset and blind to the virtual subfolder, so rows in `a/close` and
`b/close` correctly yield `close.png` and `close_001.png`. Splitting them per folder is the
obvious wrong move. Covered by `backend/tests/test_comfy_subfolder.py`.

**Stats are refreshed per row, not once at the end.** `Dataset.image_count` is a stored column
that `GET /datasets/{id}` returns verbatim, so the sidebar and gallery counters can only move
while a run is in flight if the column is rewritten as it goes — and a cancel then finds it
already correct rather than stale by however many rows landed. The call sits *before* the row's
`_emit`, so the SSE event the frontend invalidates on arrives after the column is fresh. The
raise paths are covered separately: `enqueue` receives `_run_with_stats`, a wrapper that re-runs
`refresh_stats` against `run_dataset_id` (a plain str captured in the router, not an ORM read)
only when `_run` raises — cancel and the connect-error abort; a normal return is already covered
by the last row's refresh — in its **own** session, outside `_run`'s `async with`, so no second
writer opens while the first session is live, guarded by `except Exception`, which cannot
swallow `CancelledError` and so cannot turn a cancelled job into a failed one. Regression test:
`backend/tests/test_comfy_cancel_stats.py`.

Imported rows get their provenance from `_comfy_output_provenance(plan_row.output_is_synthetic,
plan_row.name)` and their `source_meta` from `_comfy_source_meta(plan_row, row, wf)` — built from `wf`,
the graph actually submitted rather than the plan template, computed once per row and deep-copied per
image. See `docs/dev/provenance.md` § ComfyUI synthetic stamping.

Failure/cancel semantics:

- Per-row failures are caught by a deliberately broad `except Exception` — a narrow error list
  let an unexpected exception skip the very cleanup that exists for it (see
  `docs/dev/postmortems/PM-005-tuple-return-widened-broke-caller.md`). The row is marked
  `failed` with a readable message (400 `node_errors` are flattened by `_format_node_errors`)
  and the run continues. **Per-row atomicity**: `row_files`/`row_image_ids` track every file +
  Image the row produces — files are tracked *before* they are written (a corrupt download can
  fail inside `_write_and_register` after the file is on disk) — and the handler runs
  **DB-error-first**: `session.rollback()` (after a failed flush the session is unusable, so any
  DB work before the rollback would raise inside the handler), then unlink the files, then a
  separately-guarded delete-by-id plus row update and commit, then `refresh(ds)` /
  `refresh(plan_row)` and a `populate_existing=True` re-select of the rows — a rollback expires
  every object, and the loop keeps reading them, so an expired load on an async session would
  raise `MissingGreenlet`. A hard `CancelledError` (server shutdown; normal cancel is
  cooperative and never reaches here with files written) is a separate branch: unlink, delete
  the tracked `Image` objects, reset the row to `pending`, re-raise.
- **A partly accepted graph fails the row.** When only some outputs validate, ComfyUI answers
  `/prompt` with a 200 *and* a non-empty `node_errors`, queues the outputs that passed, and
  drops the rest. Completing the row would file fewer images than its workflow makes, with no
  error, and a validation error recurs on every row. So `submit` raises
  `ComfyPartialRejection`, a `ComfyRowError` carrying the queued `prompt_id`. The run catches
  it before the 400 branch, so `maybe_queued` stays true and § Removing our prompt takes the
  queued part back out.
- **3 consecutive connect errors abort the run** with a job-level error; untouched rows stay
  `pending`. Like cancel, this raises straight out of `_run` — which is why the stats refresh
  lives in the `_run_with_stats` wrapper's `finally` and per row, not after the loop.
- Cancel (`DELETE /jobs/{id}`) is cooperative: checked before each row and inside the poll
  loop, which reverts the in-flight row to `pending` and raises; the `CancelledError` branch
  then takes our prompt out of ComfyUI. `DELETE /jobs` only flips a still-*pending* job to
  `cancelled`; a running one just gets the cooperative flag and is marked `cancelled` when the
  worker unwinds, so a row can briefly read `running` until the next check (~1 s).

#### Removing our prompt

ComfyUI is shared: the sync-from-canvas workflow (`docs/dev/comfyui/sync.md`) assumes the
user also queues prompts from the canvas against the same server. A bare `POST /interrupt`
stops *whatever is executing* — which, with a canvas prompt running and ours pending, killed
the user's prompt and left ours queued; and a row timeout that merely raised left our prompt
in ComfyUI's queue with the next row stacked on top. So the job never sends a global
interrupt. `ComfyClient.cancel_prompt(prompt_id, client_id)` reads `GET /queue` (items are
`[number, prompt_id, prompt, extra_data, outputs]`; `extra_data.client_id` is our job id,
passed at submit), deletes ours from the pending queue (`POST /queue {"delete": [...]}`, by
id **and** by client_id — the second catches a cancel that landed mid-submit, before we
learned the id), sends `POST /interrupt {"prompt_id": ours}` only if ours is the one
running, and re-reads to confirm — the **whole** call, every request included, bounded by
`CANCEL_CONFIRM_WINDOW_S` (5 s). It never raises; it returns `not_found | removed |
interrupted | still_running | unreachable`. Only a failed *connection* is `unreachable`;
an unreadable `/queue`, a `ReadTimeout` or any other stall after connecting is
`still_running` — unconfirmed is not gone, and a slow server reported as down let the next
row stack on a prompt still sitting there.

The router runs it only while ComfyUI may still hold the row's prompt — from submit until
`/history` reports it finished (`maybe_queued and not history_seen`); a 400-rejected or
never-connected submit queued nothing and skips it, while a partly accepted one did queue
something and is removed. Bounded by `REMOTE_CLEANUP_BUDGET_S`
(6 s, just above the client's window), or `REMOTE_CLEANUP_HARD_CANCEL_BUDGET_S` (1.5 s) when
`current_task().cancelling() > 0` — a hard cancel from `JobQueue.stop()`, which then allows
only `STOP_UNWIND_SECONDS`; the cooperative path fits inside `STOP_GRACE_SECONDS`
(`docs/dev/backend-infrastructure.md`). In the `CancelledError` branch it runs **last**, after
the row reset commits, inside `except Exception` with a bare `raise` (PM-027). In the generic
branch a non-clean outcome is appended to `row.error_msg`, and `still_running` on a non-connect
error **aborts the run** (remaining rows stay `pending`) rather than stack the next row on a
prompt we could not remove; `unreachable` does not abort, leaving the 3-strike connect breaker
to decide as before.

Residual risk, accepted: an old ComfyUI ignores the interrupt body and interrupts globally,
so if our prompt finishes in the milliseconds between the `GET /queue` and the interrupt,
whatever runs next is stopped.

### Testing the run

**One fake ComfyUI, two adapters**: `backend/tests/fake_comfy.py`. `FakeComfy.handler` holds
all the behaviour. pytest hands `transport(fake)` (an `httpx.MockTransport`) to `ComfyClient`
by patching `backend.routers.comfy.ComfyClient` with a `functools.partial`. The Playwright
suite runs the same handler on a real socket, because its backend is a separate process the
transport seam cannot reach: `frontend/e2e/serve-comfy.mjs` starts
`python -m backend.tests.fake_comfy` as a second `webServer` on `E2E_COMFY_PORT` (default
8198), and `comfy-run.spec.ts` points `comfyui_url` at it through `setComfyUrlViaApi`. The
socket adapter is stdlib `http.server`, since `requirements-ci.txt` has no uvicorn.

**The module imports no `conftest`**, which pulls in `backend.config`, the database and the
app. The e2e server must not open a database to pretend to be ComfyUI, so the fake keeps its
own `make_png`.

`/prompt` refuses what ComfyUI would: a graph with no output node (400 `prompt_no_outputs`)
and a link to a node the graph lacks (400 with `node_errors`), so a `patch_workflow`
regression that breaks a link fails a test. On `complete=True`, a submit yields one image per
`SaveImage` (`type: "output"`) and `PreviewImage` (`type: "temp"`) node of the graph actually
submitted, and a `SaveImage`'s `filename_prefix` splits into subfolder and file prefix. The
history entry has ComfyUI's shape (`prompt`, `outputs`, `status`). `/view` serves a PNG with
the chunks real `SaveImage` writes: `prompt`, the submitted graph, always, and one per key of
`extra_data.extra_pnginfo`. **`ComfyClient.submit` sends no `extra_data`**, so a run's images
carry no `workflow` chunk, and `generation_metadata` holds the prompt text but no editor graph.
`polls_before_done` keeps a prompt running for that many polls, which is how the e2e server
lets the page show `running`. **A marker in the prompt text picks a failure**, so a browser
spec chooses one per row with no control endpoint: `[[fake:reject]]` is a 400 with
`node_errors`, `[[fake:fail]]` is an `execution_error` in history, and `[[fake:corrupt]]`
serves the last output in *graph* order as junk bytes. With `output_node_ids` in graph order,
the import then fails after earlier outputs were written. `[[fake:partial]]` is a 200 whose
`node_errors` names the last output node in graph order, which never runs while the rest is
queued (a 400 when it is the only output). `POST /free` is recorded for the VRAM handoff to
test against.

**`FakeComfy(varied=True)` makes images a scorer can tell apart.** By default every image is a flat 
32x32 colour, so it is blurry, uniform and a pHash duplicate of every other. With `varied=True` the 
seed is the first integer input named `seed` or `noise_seed` on any node of the submitted graph 
(`graph_seed`); with none, the global image counter stands in. `varied_image(seed, size)` draws 
a smooth random 6x6 colour grid plus 9 to 12 hard-edged rectangles at `varied_size` (default 
128): the same seed gives the same pixels, and different seeds pass the default blur, noise and 
uniformity gates and sit at least 12 pHash bits apart (600 seeds checked), outside the 
duplicate threshold of 8. Pure noise would fail the noise upper bound, so there is none. 
`[[fake:blurry]]` in the prompt drops the rectangles so the image fails the blur gate. 
`test_fake_comfy_varied.py` pins this; the cv2 half is `needs_cv2`.

`backend/tests/test_comfy_run_body.py` drives the run body through it: multi-output import
(one from a ComfyUI subfolder) with generation metadata and provenance, a PreviewImage
source, an execution error, a rejected, a refused and a partly accepted graph (the last with
its queued prompt removed), the per-row rollback and per-row
folders, plus a real-socket round trip that guards the e2e adapter.
`test_comfy_queue_cleanup.py` uses it for the cancel paths.

Not modelled: websockets and progress previews, `/upload/image`, `/object_info`, the bridge's
snapshot route, input types and values (only links are checked), and an interrupted prompt's
history entry. The older run tests (`test_provenance_http._FakeComfyClient`,
`test_comfy_cancel_stats`, and `test_comfy_subfolder`, which imports the former) still replace
`ComfyClient` wholesale. Moving them onto `FakeComfy` is an open follow-up.
