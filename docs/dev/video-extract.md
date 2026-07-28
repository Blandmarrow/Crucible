# Video frame extraction

How a video becomes shot-segmented `Image` rows: sampling, shot detection, rendering, and the
two endpoints that drive them. `services/video_extract.py` holds everything needing cv2,
PySceneDetect or ffmpeg, and `routers/videos.py` exposes `POST /videos/{id}/probe` and
`POST /videos/extract`. The pure-numpy judgement calls those pass frames to — cropdetect,
combing, telecine, sharpness, the candidate pick — live in `services/video_frames.py` and are
documented in `docs/dev/video-heuristics.md`. The container-header probe, the duration
measurement and the poster frame stay in `docs/dev/video-decode.md`; the `Video` model, the
storage layout, the poster-stem rules and the rest of the `/videos` surface stay in
`docs/dev/video.md`, which also indexes the tests for all of it.

**Why the modules split this way.** `docs/dev/video-decode.md` once promised extraction would
join the probe and the poster in `video_service.py`. It should not, and the reason is the test
suite. `video_frames.py` needs no video — it takes an `ndarray` and returns a number or a rect
— so every judgement call the feature stands on is testable in milliseconds against synthetic
arrays. `video_extract.py` needs mp4 fixtures and two optional dependencies; one module would
make the fast tests hostage to the slow ones.

**Division of labour: OpenCV decodes, numpy crops, ffmpeg only deinterlaces.** The roadmap
assigned the whole filter chain to the ffmpeg binary, which is more than it needs to do — a
crop is a numpy slice on a frame cv2 has already decoded for shot detection, so the
progressive path (the overwhelming majority of sources) never spawns a subprocess.
`imageio-ffmpeg` earns its place for exactly one thing: `bwdif`, which OpenCV cannot
express. It yields **RGB** where cv2 yields BGR, and that flip is normalised at one boundary
(`_read_positions_ffmpeg`) so nothing downstream knows about it. Its `read_frames` generator
owns a subprocess, so every use is wrapped in `contextlib.closing` — a `break` without a
`close()` orphans the process. Both dependencies are optional: `capabilities()` reports what
is installed, and the request 503s with actionable text rather than letting a job die with
an `ImportError` five minutes later.

Every capture opened here goes through `video_service.apply_orientation` for the same
reason the probe and the poster do — see `docs/dev/video-decode.md` § Container rotation.

## Probe sampling

`video_extract.probe_samples` backs `POST /videos/{id}/probe` (`docs/dev/video.md`
§ Endpoints). Sample positions are inset by half a step from both ends of the trimmed span,
because frame 0 is very often a black leader and the final frame very often a fade.

**Peak RSS is the load-bearing detail.** A decoded 4K frame is 24.9 MB; twelve are ~300 MB.
Everything a frame contributes — its two edge profiles, its combing ratio, its encoded
preview — is extracted **inside** the iteration and the array released before the next seek,
so peak is one frame plus a few hundred KB of strings. Never build a `list[np.ndarray]` and
map over it afterwards. Same failure class as CLAUDE.md's "close PIL Images after
preprocessing".

**Cropdetect and combing run on the full-resolution frame; only the preview is downscaled.**
Resampling averages adjacent rows together, which is precisely the field structure
`combing_ratio` measures — analysing a resized frame would turn every interlaced source
progressive. What the two measure, and what the probe does with the verdicts, is
`docs/dev/video-heuristics.md`.

Caps are enforced server-side, not only by Pydantic: `PROBE_MAX_SAMPLES` (12) and
`PROBE_MAX_PAYLOAD_BYTES` (2 MB, at which the loop breaks and sets `truncated`). Previews
are `data:image/jpeg;base64,…` URLs, never temp files — a file would need a serving
endpoint, a cleanup sweep and a traversal guard on an unauthenticated server, to hold a
preview for the life of a modal. Failed seeks cost one sample each and are reported through
`samples_failed`; broken tails are common and a partial result is still a result.

`crop_confidence` is *agreement*, not certainty: the fraction of samples that saw any matte
and saw this one. A rect derived from one letterboxed shot in eight is exactly the case a
user should override.

## Shot detection (PySceneDetect 0.7.1)

```python
video = open_video(path, backend="opencv")
manager = SceneManager()                 # no StatsManager — frame_skip needs that
manager.add_detector(AdaptiveDetector(adaptive_threshold=sensitivity,
                                      min_scene_len=min_shot_frames, min_content_val=15.0))
manager.crop = (x0, y0, x1, y1)          # INCLUSIVE corners, not x/y/w/h
video.seek(trim_start_secs)
manager.detect_scenes(video=stream, end_time=end_secs, frame_skip=frame_skip)
shots = manager.get_scene_list(start_in_scene=True)
```

**Progress and cancellation come from a delegating `VideoStream` wrapper.**
`detect_scenes`'s own `callback=` fires only on cuts, which on a low-cut file means no
progress at all for the whole run, and `show_progress` is tqdm on stderr. Do **not** poll
`video.frame_number` — that is a live `cv2.VideoCapture.get` on a handle scenedetect's
decode thread is concurrently `grab()`ing, i.e. a data race on a C++ object. The decode
thread's only contact with the stream is `read()`, so a `_CountingStream(VideoStream)` that
increments a plain int (GIL-atomic) gives exact per-frame progress, and returning `False`
from `read()` when cancelled gives a clean EOF. It returns False rather than raising:
`detect_scenes` has a `finally` that drains its queue and joins the decode thread, and an
exception thrown through it can leave an orphaned daemon thread that crashes interpreter
shutdown. `SceneManager.stop()` is published on the `Progress` object as belt-and-braces.

**The empty-list trap.** `get_scene_list()` returns `[]` — not one scene — when no cuts were
found. Code that believes it writes zero frames and reports success. `start_in_scene=True`
is passed **and** a spanning shot is synthesized if the list is still empty.

**Then the useless-single-shot case.** One two-hour shot with `frames_per_shot=1` yields one
frame. When there is exactly one shot longer than `SINGLE_SHOT_FALLBACK_MS` (120 s),
detection falls back to a uniform-interval sampler. That is the same code path as the
"scenedetect is not installed" fallback and the "no cuts found" one, written once, and all
three report `method="uniform"` in `result_data` **and say so over SSE** — a user who asked
for shot detection and silently got time slicing has been handed a different feature.

**A file that will not open raises instead of falling back.** Slicing a stream that does not
exist into windows produces a shot list whose every render fails: a job that "completes"
having written nothing. `detect_shots` re-raises as `UnreadableVideoError` and the job
fails with the reason.

**Cost, honestly.** `auto_downscale` cheapens the *analysis*, but the downscale happens
after `video.read()`, so scenedetect still full-resolution-decodes every frame. A two-hour
24 fps 4K file is ~173k decodes before one frame is written — realistically 8–25 minutes
with the bar on "detecting". The levers, in order: `frame_skip` (legal here only because no
`StatsManager` is attached), the `max_shots` cap (default 5000), and `min_shot_ms` (default
600). The job emits an ETA from `frames_read` and elapsed time once there are ~5 s of data.

One behaviour differs from what the plan assumed and is worth knowing: an out-of-range
`manager.crop` only prints `Warning: crop ends outside of video boundary` and carries on —
it does not raise. The rect is clamped by `clamp_crop` before it gets there anyway, but do
not rely on scenedetect to reject a bad one.

## Rendering a shot

`render_shot` writes one shot's frames and takes `dests` — `[(image_path, thumbnail_path),
…]`, one per frame wanted — because only the async side can see the DB names a filename has
to dodge.

Candidates for the sharpest-in-window pick are taken **close together** (120 ms apart), not
spread across the shot: the goal is the sharpest frame *at the moment being sampled*, and a
candidate two seconds away is a different composition. Within that window the reader seeks
once and then walks with `cap.grab()`, `retrieve()`ing only at candidate positions —
repeated seeking is the obvious alternative and is worse, because a container that snaps
seeks to keyframes would hand back the same frame five times and silently reduce
sharpest-of-five to a coin toss with one side.

**Two decode passes per written frame, on purpose.** The first walks the window scoring each
candidate and releasing it; the second re-fetches only the winner. The single-pass
alternative holds every candidate in memory — five 4K frames is 125 MB, and both
`frames_per_shot` and `candidates` are user-settable — and cannot be short-circuited,
because `pick_index`'s luma-outlier rejection is defined against the median of the whole
candidate set (`docs/dev/video-heuristics.md`) and so can change the winner retroactively.
A second seek-and-decode costs about 7.5 ms.

The crop is clamped against `frame.shape`, which is the authority — headers lie, and
container rotation swaps the axes. As in `generate_poster`, `ImageOps.exif_transpose` is
deliberately absent and is not a violation of the "always transpose first" invariant: the
input is a decoded ndarray, not a file with an EXIF block.

## The endpoints

`POST /videos/{id}/probe` is a **plain request, not a job**. A seek-and-decode measures at
~7.5 ms, so twelve samples is a request-path cost, and a job would add a row, an SSE
subscription and a re-attach path to something that finishes before the modal has finished
animating. It runs `probe_samples` through `run_in_executor` inside an
`asyncio.wait_for(25 s)` → 504.

That `wait_for` is legitimate, unlike the one CLAUDE.md forbids around a stdlib `re` match,
and the difference is not stylistic: cv2 releases the GIL for every grab and retrieve, so the
event loop keeps getting scheduled and the timeout genuinely fires — the abandoned executor
thread finishes one frame and discards it. `re` never releases the GIL, which is why wrapping
*that* can never fire.

**The probe's only write is metadata correction** — `duration_ms`, plus `width`/`height`/
`fps` if they were NULL. Crop, deinterlace and trims are deliberately *not* written here:
the modal re-probes on every trim-handle drag, and a preview must not commit. `capabilities`
rides on this response rather than a separate route, since the modal always probes before
offering any control they gate.

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
will never be applied cannot 400 the batch.

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
restore the frames, and it is what makes step 5 acceptable at all.

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

A circuit breaker aborts at 10 consecutive failures, or above a 50% failure rate after 20
shots, with a message naming the timecode. Frames already written stay — they are real — and
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
Detection pins `done: 0, total: 0` — explicitly, never by omission, because `jobStore` merges
partials by job id and an omitted key inherits whatever the client last held — and the
decoded-frame count with its ETA rides on `message` instead. Extraction then carries the
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
