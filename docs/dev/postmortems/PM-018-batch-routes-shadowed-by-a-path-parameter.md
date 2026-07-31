# PM-018: two endpoints were unreachable behind a path parameter

### Symptom

`POST /api/v1/images/batch/resize` answered **404 `{"detail":"Image not found"}`** and
`POST /api/v1/images/batch/crop` answered a **422** whose `detail` named `x`, `y`, `width`
and `height` — the fields of the *single-image* crop body, which the request never sent.
Neither endpoint had ever run. Their job types `batch_resize` and `batch_crop` appear in
the `BackgroundJob` vocabulary and in the frontend's API client
(`frontend/src/api/images.ts::batchResize`/`batchCrop`), and no row of either type can ever
have existed.

Nothing a user can click was broken: a grep over `frontend/src` finds no caller of either
client wrapper, so this shipped as two dead endpoints rather than two broken features. The
severity is in what the shadowing *hid*, not in what it broke.

### Root cause

FastAPI matches routes in **declaration order**, and `image_id: str` accepts any segment
including the literal `"batch"`. The two batch handlers were declared at lines 1469 and
1504 of `backend/routers/images.py`, after `/{image_id}/resize` (1117) and `/{image_id}/crop`
(1136). Every `/batch/*` request was therefore answered by the single-image handler with
`image_id="batch"`, and there is no fallthrough — a route that matches and then fails is
never retried against a later route. Resize 404'd from `db.get(Image, "batch")`; crop
validated the body first and 422'd.

The failure is silent in both directions. The shadowed routes still appear in
`app.openapi()` and in `/docs`, so the API surface *looks* complete; and the handlers were
never imported by any test, so their bodies were invisible to coverage as well as to users.

That invisibility is the real cost. Three defects had been sitting dormant in those two
function bodies since they were written:

- **No `ensure_not_busy`.** They overwrite files in place and were the only mutating
  endpoints in `images.py` without the guard — a versioning restore and a batch resize
  could have rewritten the same files concurrently.
- **The pre-PM-013 shape.** The loop ran `protect_file_before_overwrite` → overwrite →
  assign row fields → fallible `generate_thumbnail` → fallible `broadcaster.emit`, with the
  only `commit()` *outside* the loop. A raise on image N discarded the geometry and
  `processing_history` of images 1..N — every one of them already rewritten on disk. PM-013
  swept the codebase for this shape and did not find it, because a shadowed route is not
  in the call graph anyone greps.
- **A static job label** (`label="Batch resize"`) with no `label` override field, against
  CLAUDE.md § Data flow's requirement that every job-creating router build a descriptive
  auto-label.

### Generalizable rule

**Declaration order *is* the routing table.** Flag any literal path segment declared after a
parameterized route on the same router and method that would match it — `/{id}/x` before
`/batch/x`, `/{name}` before `/search`. The parameter type is not a filter: `str` accepts
every literal, and even `int`/UUID converters only produce a 422 for that *one* request
shape rather than falling through.

The second, more general rule: **a shadowed route hides code from review and coverage as
effectively as it hides it from users.** When you find dead or unreachable code, do not stop
at making it reachable — audit its body against every invariant introduced since it was
written. It missed all of them, and it will not have been flagged by any sweep, because
sweeps follow the call graph.

Near misses in this codebase, safe today and one addition from breaking (all 20 routers
audited on 2026-07-31): a future `PATCH /images/batch/rename` would be dead behind
`PATCH /images/{image_id}/rename`; `quality.py`'s `/duplicates/{dataset_id}` and
`/duplicates/resolve` are safe only because their methods differ.

### Why it wasn't caught the first time

- **No test ever called either endpoint.** The request-level harness
  (`backend/tests/conftest.py::api_env`) exists precisely because a hard crash in a router
  once shipped green; these two routes predate it and were never added.
- **A status-code-only assertion would not have caught it either.** The shadowed resize
  returns 404 and the shadowed crop returns 422 — both plausible answers for a malformed
  batch request. The route-order test here therefore asserts the 422's `detail` **names
  `image_ids`**, which only the batch body model can produce.
- **The OpenAPI schema looked right**, so an audit of the documented API surface — the
  obvious way to check "does this endpoint exist" — reports both routes as present.
- **Route ordering has no structural guard.** Every other invariant of this weight in the
  repo has one (`test_video_lineage_mirrors.py` for column mirrors,
  `scripts/check_migrations.py` for schema drift). A walk over `app.routes` asserting that
  no literal segment sits behind a parameterized route matching it would turn "only review
  catches the next one" into CI; it is not written yet.

### Fix

- The contiguous block declaring `/batch/resize` and `/batch/crop` moved above
  `/{image_id}/resize` in `backend/routers/images.py`, with a comment at the new site saying
  declaration order is the routing table and that future `/batch/*` routes belong there.
- The three dormant defects, now that the handlers are live:
  - `_guard_batch_datasets(db, image_ids)` resolves and `ensure_not_busy`-guards every
    dataset the selection touches (the id list has no dataset constraint, so it can span
    several), chunked for SQLite's bind-parameter ceiling. It also names
    `BackgroundJob.dataset_id` when the selection resolves to exactly one dataset.
  - Both loops restructured to the PM-013 shape: per-image `commit()` with nothing fallible
    above it, thumbnail and emit in a post-commit epilogue, `remap_detections_for_crop`
    *before* the commit because it is DB-only work (matching `_run_crop_upscale_replace`),
    a `counts` dict seeded from the LUT/upscale twins, and the `job_queue.cancel_requested`
    pair.
  - `label: str | None` on `BatchResizeRequest`/`BatchCropRequest`, used as
    `body.label or auto_label`.
- Tests: `backend/tests/test_batch_resize_crop_http.py` — six cases covering the route
  order, per-image geometry and history, the epilogue's `thumbnails_stale` count, the
  blast-radius case (one failure does not roll back its predecessors), and the 409.

**Deliberately out of scope**: neither handler refreshes `file_size_bytes`/`phash` after
the overwrite — nor do the single-image `resize`/`crop`, so fixing it means widening two
service signatures with other callers. And these two job types were *not* added to
`TopBar.tsx`'s invalidation sets: no UI can start them, so those entries would be
unreachable code. Wiring a UI is what makes both of those the first thing to do.

### Status & date

MITIGATED — these two are fixed and pinned, and all 20 routers were audited for other live
collisions (none). The class recurs the moment a `/batch/*` or other literal route is
appended below a parameterized sibling; a structural `app.routes` guard would close it and
does not exist yet. Found in code review of the `experimental-video-support` branch;
reproduces on `main`.
Last reviewed for staleness: 2026-07-31.
