# Video frame extraction

The two endpoints that turn a video into shot-segmented `Image` rows, and the background job
behind one of them: `routers/videos.py` exposes `POST /videos/{id}/probe` and
`POST /videos/extract`. The pipeline they drive — sampling, shot detection, rendering — is
`services/video_extract.py` and is documented in `docs/dev/video-shots.md`. The `Video` model,
the storage layout, the poster-stem rules and the rest of the `/videos` surface stay in
`docs/dev/video.md`, which also indexes the tests for all of it.

**This file is pass 1 only.** Everything here writes cheap triage frames so a long file can
be curated quickly; turning the survivors into full-resolution training data is pass 2, in
`docs/dev/video-reextract.md`. The two share `_write_frame` (`docs/dev/video-shots.md`
§ Rendering a shot) and nothing else — pass 2 re-seeks a recorded timestamp and never detects
a shot or picks a frame.

## The endpoints

`POST /videos/{id}/probe` is a **plain request, not a job**. A twelve-sample probe measures
at 4.4 s on a 1080p HEVC source — decode-dominated, and *not* the ~7.5 ms per seek this was
once costed at (`docs/dev/video-shots.md` § Probe sampling) — so it is a few seconds of
request-path cost, and a job would add a row, an SSE subscription and a re-attach path to
something the user is already waiting on with the modal open. It runs `probe_samples` through `run_in_executor` inside an
`asyncio.wait_for(25 s)` → 504.

That `wait_for` is legitimate, unlike the one CLAUDE.md forbids around a stdlib `re` match:
cv2 releases the GIL for every grab and retrieve, so the loop keeps getting scheduled and the
timeout genuinely fires — the abandoned executor thread finishes one frame and discards it.
`re` never releases the GIL, which is why wrapping *that* can never fire.

**The probe's only write is metadata correction** — `duration_ms`, plus `width`/`height`/
`fps` if they were NULL. Crop, deinterlace and trims are deliberately *not* written here:
the modal re-probes on every trim-handle drag, and a preview must not commit.

`capabilities` rides on this response *and* has its own `GET /videos/capabilities`, because
a video that will not probe still extracts — without the standalone route a probe failure
leaves the deinterlace checkbox and the shot-detection warning with nothing to consult, and a
host lacking imageio-ffmpeg would offer a filter that 503s. It is pure and answers in
microseconds, so the frontend holds it in one long-`staleTime` `["extract-capabilities"]`
query and prefers it over `probe.capabilities`. **Declared above `GET /videos/{video_id}`**:
FastAPI matches in declaration order, so below it the literal segment is read as a video id
and 404s. A test pins that ordering.

`POST /videos/extract` takes 1–50 `video_ids` and creates **one job per video**.

**The decode-fixup write happens in the endpoint** — not the probe, for the reason above,
and not the job, because the values have to survive a cancelled or failed run ("add to the
existing subfolder" reads them back off the row) and a write inside a job cannot return a
400. The endpoint has the request session, already holds the busy guard, and is the only
place that can validate a rect against each video's real dimensions.

`clear_crop` is not redundant with `crop: None`. Without it, None is ambiguous between
"leave the row's stored crop alone" (a batch where only the trims changed) and "the user
cleared it", and a re-extraction would replay a stale rect forever.

A batch whose videos have **different dimensions** and a crop is a **400 naming them**, not
a silent per-video skip: a series where half the episodes are letterboxed and half are not
is a silently inconsistent dataset. It also refuses a batch mixing probed and unprobed
videos, since a `(None, None)` entry makes the dimension set non-uniform.

Videos already covered by a pending or running extraction come back under `skipped`
(`rescan_folder`'s precedent) and the rest still enqueue — **resolved first, before any
validation or write**, so a video extracting nothing keeps its stored fixups and a rect that
will never be applied cannot 400 the batch. That check is
`_videos_with_running_extractions`, and it matches **both** job types: a `replace` here
deletes the very rows a running pass 2 is rewriting.

An id that **no longer resolves** joins them as
`{video_id, filename: "", reason: "no longer exists"}` — pass 2's resolver contract, so one
deleted video does not cost a fifty-video batch its run; the 404 survives for the case where
*nothing* resolved. `ExtractProgressList` renders `s.filename || s.video_id`, these skips
having no filename.

**A crop is normalized, not rejected.** Only genuine overflow (`x + w > width`, per row,
skipping NULL dimensions) is a 400; the rest goes through `clamp_crop` once, and the
**normalized** tuple is what lands in `Video.crop_*` — the stored rect is the rect that will
be applied. Testing `clamp_crop(rect) != rect` instead rejects rects that plainly fit, since
it snaps to even coordinates and returns `None` for a full-frame rect — which means "the whole
frame": NULL in all four columns, no crop, not an error.

The **deinterlace 503 gate tests the effective value per row** — `body.deinterlace` if given,
else `Video.deinterlace`, which is what the job reads. Gating on the request alone lets a
stored filter through, and the job then fails with "N frame(s) failed to decode": a missing
package misreported as a decode fault.

**Trims covering the whole known `duration_ms` are a 400**, on the effective value for the
same reason. Both fields are capped at `TRIM_MAX_MS` (24 h) in `schemas/video.py`, on
`VideoProbeRequest` too: an unbounded `ge=0` int reaches `commit()` as `OverflowError: Python
int too large to convert to SQLite INTEGER`, an unhandled 500. A large-but-storable pair is
worse, because a stored trim is **sticky** — detection collapses to one zero-width window,
every render fails, the breaker calls it a decode fault, and `generate_poster` reads the same
columns and stops regenerating, none of it naming the trim.

**Subfolder modes are resolved in the router**, so the response can name the target —
mirroring `crop_to_detection` normalizing `dest_subfolder` up front. `add` takes the given
subfolder or this video's most recent one; `new_subfolder` steps `{slug}`, `{slug}_2`, …
**against every subfolder in the dataset** (declared and occupied) *and* against names
claimed earlier in the same request, since nothing either job writes is visible to the other
at enqueue time — stepping against one video's own history alone would let video B's "new"
subfolder land inside one video A already fills. `replace` reuses the prior subfolder, and
having no prior extraction is not an error, just a first run. Both branches test the prior
subfolder with `is not None`: `""` is legitimate (frames at the dataset root), and reading it
as "no prior extraction" turns a `replace` into add-to-a-new-subfolder, leaving the frames it
exists to remove.

## The `video_extract` job

`total_items` starts at 0 and is set once detection knows the shot count, following
`import_folder`'s precedent. The auto-label is `f"Extract: {stem[:60]} — {n} frame(s)/shot"`
— the `[:60]` is not cosmetic, since `label` is a `String(200)`.

**Step order is load-bearing, and the `replace` delete is deliberately fifth:**

1. `measure_duration_ms` if NULL; persist.
2. `require_free_space(dir, 0)` — the cheapest possible failure.
3. Shot detection — the long phase, and the most likely to fail.
4. `require_free_space` again, now that `len(shots) × frames_per_shot` makes the estimate
   real.
5. **The `replace`-mode delete**, only once the video is known to decode, the shot list is
   non-empty and the disk has room. A replace that destroys the previous extraction and then
   fails to produce a replacement is the worst outcome this feature can produce.
6. The extraction loop, one executor hop per shot.
7. `result_data`, final commit, `raise_if_cancelled`.
8. `refresh_stats` — reached **only on a normal return**.

Step 8 sitting last means every aborting path steps over it — `raise_if_cancelled`, the
circuit breaker, all three preflights — each of which can leave committed frames the dataset
card and Stats page then undercount. The fix is a wrapper, not a reordering:
`_make_extract_runner`'s runner refreshes stats on the way out of any raise, copying
`comfy_generate`'s `_run_with_stats` down to its three load-bearing details — import
`AsyncSessionLocal` inside the coroutine (the harness patches it at module level), catch
`BaseException` but guard the refresh with `except Exception`, and wrap the *call* so the
run's session closes first.

The delete is scoped to `source_video_id == video.id` *within* the target subfolder, so a
subfolder the user also hand-filled does not lose the hand-filled part, and it goes through
the normal path — `mark_image_deleted_in_versions`, then the row, then the file, its `.txt`
sidecar and its thumbnail — never a raw unlink. That is what lets a pre-existing snapshot
restore the frames, and it is what makes step 5 acceptable at all. The row delete goes through
`utils.chunked` — a triage subfolder is exactly the id list that runs past SQLite's
`SQLITE_MAX_VARIABLE_NUMBER` on the stock Windows build — and its test pins the *call*, not
the crash: the limit is a compile-time option this container raises out of reach.

**`dataset_busy` is taken around step 5 only.** Extraction as a whole does not take it: jobs
are already serialized by the queue, and holding the flag for twenty minutes would 409 every
caption edit for no safety gain. But a replace deletes N rows, N files and N thumbnails,
which is exactly the class the flag fences, and that takes seconds.

Filenames are `{video_slug}_s{shot:04d}_{pick:02d}.jpg` — shot-grouped, lexically sortable,
stable across a later in-place overwrite, and carrying no timestamp because the DB is
authoritative. The name is a *proposal*; it still goes through `unique_filename_with_thumb`.
Files land **flat** in `{dataset}/images/` with thumbnails flat in `{dataset}/thumbnails/` —
`Image.subfolder` is a DB-only grouping, never a directory — so one `db_names` set and one
stem glob cover the whole job. Rows get `is_auto_named=True` and provenance from
`copy_provenance(video)`, whose `getattr` default correctly yields `source_meta: None` for a
`Video` (which has no such column, on purpose).

Commits land every 25 frames, so the gallery fills live: detection-crop's single terminal
commit is wrong for a job that can run twenty minutes, and folder import's 200 is tuned for
a loop that does far less per item.

**`get_image_info` returning `{}` is a failure, never a row.** It swallows every exception,
so an empty dict means the file just written will not re-open; the file and thumbnail are
unlinked and the frame counted as failed. Constructing an `Image` from it would put a
NULL-dimension row in the table, which silently breaks grid layout, the dimension filters,
dedup and the detection remap with nothing pointing at the cause.

A circuit breaker aborts at **10 consecutive shots that wrote nothing**, or above a 50%
failure rate after 20 shots, with a message naming the timecode. The unit is one *shot*, never
one frame: counted in frames, one exception from `render_shot` added `frames_per_shot` at
once, so any legal `frames_per_shot >= 10` tripped the threshold on shot one — the breaker's
sensitivity must not depend on a user-facing tuning knob. Only `consecutive_failures` is
per-shot (set after each shot's frame loop from whether it wrote anything);
`counts["failed"]` keeps its per-frame meaning, which is what the rate breaker reads and how
slow degradation is caught. Pass 2 uses the constant per *frame*, where the two coincide.
Frames already written stay — they are real — and
the job ends `failed`, not `cancelled`, because nobody asked for this. Disk is re-checked
every 100 frames written, off a counter reset at each check — `written % 100` is evaluated
once per shot, which `frames_per_shot > 1` steps straight over. It commits first, for the
reason the breaker commits before raising: the check raises out of the job, and anything
flushed but uncommitted would be a file on disk with no `Image` row.

SSE carries `phase` ∈ {`detecting`, `replacing`, `extracting`} mapped onto one monotone
percent (0–20 / 20–25 / 25–100), because a bar that restarts at zero reads as a job that
restarted, plus `shot`/`shots` and `image_id` (mirroring `crop_to_detection`, so a frontend
can invalidate per-image caches). Detection emits at most every 0.5 s and extraction once
per shot — `broadcaster` queues are `maxsize=200` and drop on overflow.

**`done` means one thing for the whole job: frames a gallery refetch would actually see.**
Every phase that writes no frames — `detecting` and `replacing` alike — pins `done: 0,
total: 0` **explicitly, never by omission**, because `jobStore` merges partials by job id and
an omitted key inherits whatever the client last held (mid-replace, that is the previous
run's final count). Detection's decoded-frame count and its ETA ride on `message` instead.
Extraction then carries the
**committed** count (`written − written_since_commit`), which steps once per
`EXTRACT_COMMIT_EVERY` rather than once per shot, while `_emit`'s `fraction` stays on
*planned* frames so the bar keeps its per-shot smoothness. Both halves are required by
`TopBar`'s live gallery invalidation, a per-job monotonic high-water mark on `done`
(`docs/dev/frontend-jobs.md`): counting decoded frames during detection fires it twice a
second while nothing has been written and then leaves it silent for the only phase that
writes anything, and counting *planned* frames during extraction refetches identical data 24
times out of 25. `backend/tests/test_video_extract_http.py` pins the invariant — no
`detecting` payload with a non-zero `done`, and `done` non-decreasing across the run — rather
than the field names. See `docs/dev/postmortems/PM-008-video-extract-progress-counter.md`.

It also carries **`video_id`**, which is `_emit`'s only keyword-only, no-default parameter
so that no call site can forget it. A batch runs one job per video and the frontend holds
every event in one `jobStore`, so without the key a payload cannot be routed to the right
video — exactly `comfy_prompts`' `plan_id`, and for the same reason. `jobStore` merges
partials by job id, so the key survives onto the queue's terminal event, which does not
carry it.

## Frame lineage

Extracted frames carry `source_video_id`, `source_timestamp_ms` and `source_shot_index`.
CLAUDE.md § Key invariants states the mirroring rule those columns live under; the eight
sites that must carry them are pinned by `backend/tests/test_video_lineage_mirrors.py`,
whose structural test fails for the *next* unmirrored `Image` column.

`source_shot_index` is **0-based**, and matches the `_sNNNN_` in the frame's own filename.
`ImageDetailPage` therefore renders *"shot 0"* deliberately; it is not off by one against the
*"Shot 1 of N"* progress message, which numbers shots for a human watching a bar.

All three are exposed on `ImageOut`, and `source_video_id` alone on `ImageListItem` — the
gallery card needs no timestamp or shot index, and that payload is paid per row on every
page. Both schemas are `from_attributes` over a `select(Image)`, so no construction site
changed. `ImageDetailPage` renders them as a lineage line (`docs/dev/video-ui.md`).
