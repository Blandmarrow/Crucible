# Pass 2: which frames a re-extraction rewrites

The request side of pass 2: what a re-extraction promises, and which frames it will touch.
`POST /videos/reextract/preview` and `POST /videos/reextract` in `routers/videos.py` share
one resolver, `_resolve_reextract_targets`, so the preview's accounting and the job's cannot
diverge; the scope cap `REEXTRACT_MAX_FRAMES` lives in `schemas/video.py`. Nothing here writes
a file; that is the job's half. The `video_reextract` job those endpoints enqueue —
the decoder, `_rewrite`'s step order and a replace-mode extension change — is in
`docs/dev/video/reextract.md`; the dialog that drives them is in
`docs/dev/video/reextract-ui.md`; the tests for both halves are indexed in
`docs/dev/video/tests.md` § Re-extraction.

## The contract

**The timestamp is the artifact.** `Image.source_timestamp_ms` is authoritative: pass 2
re-seeks it rather than upscaling the triage JPEG, which is why `backend/tests/
test_video_reextract.py::test_re_seeking_a_recorded_timestamp_lands_on_the_same_moment`
is the test to keep if only one survives review. Off-by-one-keyframe seeking silently
yields the *wrong frame* — every count the job reports still says success.

**Frames extracted before V-06 carry a timestamp one frame period early.** `read_positions`
used to read `CAP_PROP_POS_MSEC` *before* the grab, so each frame was labelled with its
predecessor's timestamp; pass 1 and pass 2 shared the reader, so the pairing was
self-consistent and nothing visibly broke. Re-extracting one of those frames now returns
the frame immediately *before* the picture on disk (~42 ms at 24 fps). The rows were
deliberately not migrated: a migration cannot be complete by construction (`source_video_id`
is `SET NULL` when the video is deleted, so a frame whose source is gone has no recoverable
fps), the offset is `1000/fps` rather than a constant and is undefined for VFR, and
`source_timestamp_ms` is itself the user-visible authoritative artifact. Expect the drift on
old frames; it is not a seek bug.

**Geometry replays verbatim.** `Video.crop_x/y/w/h` and `Video.deinterlace` hold the
*normalized* values the extract endpoint stored (`docs/dev/video/extract.md` § The
endpoints), so pass 2 applies them as-is. Trims are irrelevant to a direct seek.

**Quality scores are left alone but no longer left silent.** Nothing is recomputed, matching
`batch_upscale`/`batch_lut` replace mode — but the rewrite goes through
`backend/utils.py::record_in_place`, which sets `Image.scores_stale` alongside the
`processing_history` entry **for any frame that carries a score**, so a re-extracted frame
that had been scored wears a badge in the gallery and on its detail page until a re-score
clears it (`docs/dev/quality/scores-stale.md`). A triage frame is commonly *unscored* —
the whole point of pass 1 is to get frames cheap and cull them later — so in practice
re-extraction often leaves the bit `False` and writes only the history entry. That entry is
unconditional, and it is the one this doc's skip rule below depends on. The
job still says so through `result_data["note"]` (`REEXTRACT_NOTE`), which the completion
toast repeats and `ReextractFramesForm` mirrors, but the wording now points at the badge
instead of asking the user to remember. `phash` *is* re-derived: dedup depends
on it. It is scale-invariant, so the value often does not change, which is why the test
poisons it first rather than asserting it moved.

**Output format is the user's.** JPEG (default) or PNG for a lossless capture; resolution
is native by default (`long_edge=0`, which `_write_frame` —
`docs/dev/video/reextract.md` § The shared write half — already reads as "no downscale"; `render_shot` neither reads nor interprets 0, it defaults
to 1024 and passes the value through)
with an optional `max_long_edge` cap.

## Target resolution — one function, two callers

`_resolve_reextract_targets` backs both endpoints, so the modal's accounting and the job's
cannot diverge. Exactly one scope: `image_ids` for a gallery selection (which can span videos
and datasets) or `video_id`, optionally narrowed by `subfolder`.

Three rules apply to the scope itself, all enforced before either endpoint returns so
preview and enqueue refuse identically — two of them inside `_reextract_rows`, and the
`image_ids` half of the third on the request schema, where a Pydantic `max_length` answers
**422** rather than the 400 its `video_id` half raises:

- **An unknown `video_id` is a 404**, not an empty result. An empty result means "this video
  has no eligible frames" — a real answer the modal renders — and returning it for a video
  that does not exist makes the two indistinguishable. It is raised *before* the busy guard,
  preserving the ordering `test_a_reextract_with_nothing_to_do_does_not_409` pins.
- **Both scopes are capped at `REEXTRACT_MAX_FRAMES` (5000)** — the schema's `max_length` for
  `image_ids`, an explicit 400 naming the count for `video_id`, which resolves its ids from
  the DB. The constant lives in `schemas/video.py` and the router imports it, so the two
  cannot drift (and a schema must never import from a router). It bounds three things at
  once: the `BackgroundJob.config` id blob, the job's `select(Image)` entity load, and the
  preview's `skipped` array.
- **`image_ids` is deduped** with `dict.fromkeys` before anything counts it. The `IN` query
  collapses duplicates anyway, so an id sent twice reported `eligible=2` against one row: a
  phantom entry in `cfg`, a phantom skip, and a bar that topped out at `1 / 2`.

Per image, in order, each skip carrying a human reason:

| Condition | Reason |
|---|---|
| row missing | `"no longer exists"` |
| `source_video_id is None` | `"not extracted from a video"` |
| `source_timestamp_ms is None` | `"no recorded timestamp"` |
| `Video` row missing / different dataset | `"source video is gone"` |
| video file absent on disk | `"source video file is missing"` |
| `processing_history` holds any op **other than** `reextract` | `"already edited in place"` |

That last rung is `backend/models/image.py`'s mandate: the **replace** mode of
crop/upscale/LUT/detection-crop mutates a row in place, so a frame keeps its lineage while
the pixels stop being the extracted frame. The `reextract` exclusion is load-bearing —
without it pass 2 refuses to run a second time, because its own history entry looks like
third-party editing. Anything that is not a dict counts as an unknown edit and skips.

**The ladder runs twice, and the second run is what makes it a guard rather than a
snapshot.** `_resolve_reextract_targets` evaluates the whole table above at preview and
enqueue time; nothing holds a `dataset_busy` lock over the frames for the run, so a
replace-mode crop, upscale, LUT or detection crop landing between enqueue and a frame's turn
would otherwise be overwritten by a job that checked before the edit existed. The worker
therefore re-applies the `processing_history` rung alone — against a live re-read of the
column, immediately before the overwrite — and counts what it refuses as `skipped_edited`
rather than `failed`: the frame was not delivered, but nothing about the video failed. The
other rungs are not re-checked; a missing row and a NULL timestamp are still the only other
things the worker re-filters on.

**The guard is only as good as what writes `processing_history`.** Upscale, LUT and
detection-crop always recorded an entry; the five in-place paths in `routers/images.py` —
`resize`, replace-mode `crop`, the `crop_upscale` replace job, `batch_resize` and
`batch_crop` — did not, so a frame cropped in place stayed silently eligible and pass 2
would have discarded the crop (`docs/dev/postmortems/PM-010`). They now all go through
`images._record_in_place(img, op,
**params)`, which is the single writer: list-concat reassignment, never `.append()`
(CLAUDE.md § Key invariants), plus the `updated_at` bump. Add a call there in any future
path that overwrites an image file. (`POST /images/batch/crop` and `/batch/resize` were
unreachable until 2026-07-31 — `POST /images/{image_id}/crop` was declared first and read
`batch` as an image id — so their entries were written but had never executed. The block now
sits above the parameterized routes and both are covered by
`backend/tests/test_batch_resize_crop_http.py`; see
`docs/dev/postmortems/PM-018-batch-routes-shadowed-by-a-path-parameter.md`.)

**In-flight dedupe lives in the resolver**, not the enqueue path, so the preview is honest
about it too — and it now runs in **both directions**: `_videos_with_running_extractions`
matches `video_extract` *and* `video_reextract`, because a pass-1 `replace` deletes the very
rows pass 2 is rewriting. `extract_frames` reads the same helper.

The endpoint alone adds `ensure_not_busy` per dataset (the job does not hold `busy`,
following `batch_upscale`), the deinterlace 503 gate on the *stored* filter — there is no
request field to override it, and without the gate the job dies inside every frame and
reports a missing package as a decode fault — and a disk preflight through the shared
`routers/videos.py::estimate_frame_bytes(width, height, *, fmt, long_edge)`, which budgets
`cropped_w × cropped_h × BYTES_PER_PIXEL[fmt]` (0.5 JPEG, 2.0 PNG) plus a flat
`FRAME_OVERHEAD_BYTES`. Conservative because a temp file coexists with the original during
each swap. The estimator is shared with pass 1, which used to budget a flat 300 KB per frame
— ~60× low at `long_edge=8192`, and pass 1's preflight runs *before* replace mode's delete,
so the underestimate let a doomed job start and destroy the previous frames on the way down
(`docs/dev/video/extract.md` § The `video_extract` job, step 4).
