# Video sampling, shot detection and rendering

The extraction pipeline itself: how `services/video_extract.py` samples a file for the probe
modal, cuts it into shots with PySceneDetect, and renders each shot into image files. The two
endpoints and the background job that drive all of this are in `docs/dev/video-extract.md`;
the pure-numpy judgement calls this module feeds frames to — cropdetect, combing, telecine,
sharpness, the candidate pick — live in `services/video_frames.py` and are documented in
`docs/dev/video-heuristics.md`. The container-header probe, the duration measurement and the
poster frame are a different module entirely, in `docs/dev/video-decode.md`. Pass 2
(`docs/dev/video-reextract.md`) shares `_write_frame` below and nothing else.

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

**The no-duration variant of that fallback is one *zero-width* window**, so every pick in it
resolves to the same position: `frames_per_shot > 1` would write N byte-identical files
sharing one `source_timestamp_ms` and `source_shot_index`, a synthetic duplicate cluster to
dedup. The job clamps it to 1 (saying so over SSE) for a single `end_ms <= start_ms` shot,
before step 4 derives `total_items`.

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

The crop → resize → save → thumbnail tail is **`_write_frame`**, shared with pass 2 so the
two cannot drift on format or quality. The crop is clamped there against `frame.shape`,
which is the authority — headers lie, and container rotation swaps the axes — and the
format comes from the output suffix through `utils.normalize_image_format` +
`image_save_kwargs`, whose JPEG kwargs are pass 1's existing `quality=95, subsampling=0`.
As in `generate_poster`, `ImageOps.exif_transpose` is deliberately absent and is not a
violation of the "always transpose first" invariant: the input is a decoded ndarray, not a
file with an EXIF block.
