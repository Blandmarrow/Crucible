# Video decode: probe and poster

The `services/video_service.py` decode surface. Three things live here: `probe_video`,
which reads a container header for dimensions, fps, codec and duration;
`measure_duration_ms`, which finds a real duration by seeking when the header has none; and
`generate_poster`, which cuts the single WebP frame every video card and detail view shows.
Frame extraction is a separate pair of modules — see `docs/dev/video-shots.md` for the
pipeline and `docs/dev/video-heuristics.md` for the numpy judgement calls it uses. The
`Video` model, the `videos/` storage layout, the poster-stem collision rules and ingest stay
in `docs/dev/video.md`, the `/videos` request surface is in `docs/dev/video-endpoints.md`,
the frontend surfaces are in `docs/dev/video-ui.md`, and tests for all of it are indexed in
`docs/dev/video-tests.md`.

## Container rotation

cv2 and ffmpeg disagree by default: ffmpeg autorotates, cv2 does not. Left alone, the two
decode paths would apply the same crop rect to differently-oriented frames, and a poster
would disagree with the frames extracted from the same file. Every capture this codebase
opens therefore goes through `video_service.apply_orientation(cap)`, which sets
`CAP_PROP_ORIENTATION_AUTO` (present in cv2 5.0.0); ffmpeg keeps its default.

The guard there is a **return-value check, not a `try`**. `cv2.VideoCapture.set` reports an
unsupported property by returning False; it does not raise, so the `try/except` this
replaced could never detect the backend it documented — and the broad `except` was in
practice load-bearing for a test double that had no `set` at all. A False is logged at
debug and ignored: on a backend without the property this is a no-op, not a fault, and it
must never be why a video fails to open. Doubles standing in for a `VideoCapture` need a
`set` returning a bool.

## Metadata: the ladder and its guard

`services/video_service.py::probe_video(path)` reads the container header only — no decode
pass — and returns `{width, height, fps, codec, duration_ms, file_size_bytes}`. It is
blocking; every caller runs it through `run_in_executor`. `cv2` is imported lazily inside
the function, matching the convention in `backend/ml/technical_scorer.py`.

- fps, dimensions and codec come straight from `cv2.VideoCapture`, which reads mp4, mkv,
  webm, mov, avi and ts, including HEVC 10-bit and ProRes 422 HQ. It reads `.ts`, which
  `VIDEO_EXTENSIONS` does not admit: the five containers were specified "at minimum", so
  that is an open gap in the allowlist, not a decoder limit.
- `CAP_PROP_FOURCC` gives a stable 4-character code, decoded by
  `media_types.fourcc_to_code` and stored raw on the row. `codec_label` maps it for
  display and falls back to the code itself, so an unrecognised codec renders as `apch`
  rather than as an empty cell. The decode is NULL unless the result is ASCII **and**
  printable — a guard, not a formality, against the `0` and `-1` that a container with no
  usable code reports; see that function's docstring for why stripping whitespace does not
  catch them.
- Duration is `frames / fps`, **only** when `fps > 0` and `0 < frames < 1e9`; otherwise
  `duration_ms` is NULL and renders as "unknown". The guard is not defensive padding: a
  matroska written to a non-seekable pipe — no duration, no cues, which is what
  stream-ripped and partially-copied files look like — reports
  `CAP_PROP_FRAME_COUNT = -230584300921369408` while every other field stays correct, and a
  naive divide stores a duration of −9.2e15 seconds. Parsing the `ffmpeg -i` banner is no
  rescue; it prints `Duration: N/A` for exactly that file. `measure_duration_ms` backfills
  a true one; see the next section.
- **A second ceiling bounds the quotient, not just the frames.** `fps=0.01` with
  `frames=500_000` clears the guard above and yields about 578 days, which would then drive
  the trim bar and every sample position. A `duration_ms` over `MEASURE_MAX_MS` (24 h, the
  same constant the seek search stops at) is logged and stored NULL. The cost is real
  rather than theoretical: a genuine video longer than 24 h reads as "unknown". That is the
  tradeoff `MEASURE_MAX_MS` already made for the search, applied consistently — the
  constants block sits above `probe_video` so both readers can see it.

## Measuring a duration the header does not have

NULL is the honest answer from `probe_video`, but it breaks everything downstream: no
progress percentage, no tail trim, no sample positions. So
`measure_duration_ms(path, *, hint_ms=None, max_ms=24h)` finds the real one by *seeking*,
not decoding — exponential probing on `CAP_PROP_POS_MSEC` + `cap.grab()` to bracket the
end, then bisection, then a short sequential grab walk to land exactly on the last frame.
About thirty grabs. (`imageio_ffmpeg.count_frames_and_secs()` is the alternative and is a
full `-f null -` decode pass, which its own docstring warns is slow.)

**Every read of `CAP_PROP_POS_MSEC` here follows its `grab()`, never precedes it** — the
property reports the position of the frame *just grabbed*, so reading first answers with
the previous frame and every position comes back one period early. In the tail walk the
order decides something further: recording before the grab discards whatever the final
successful grab reached, losing a second period on top. Do not "simplify" either back.

**The rule is that nothing downstream of the probe ever sees a NULL duration**: the probe
endpoint measures and persists it, and the extraction job measures and persists it if no
probe ran.

Three details are load-bearing:

- **It returns None for a stream that will not *seek*,** not only for one that will not
  open. A non-seekable stream ignores the `set()` and answers every probe with the next
  sequential frame, which reads as "reachable" forever. The check is that the reached
  position lags far behind the requested one — but only once the probe has grown past
  `NON_SEEKABLE_PROBE_FLOOR_MS` (2 s), because early on, when the probe is still tens of
  milliseconds, a *correct* seek to frame 0 also sits below half of it. Without that floor
  a small `hint_ms` makes every seekable file measure as None.
- **`hint_ms` is clamped to `max_ms` before the first probe.** The exponential loop's
  condition `probe <= max_ms` is evaluated *before* probing, so an over-ceiling hint used to
  skip the phase entirely and return None without a single seek — and an over-ceiling hint
  is exactly what a poisoned header supplies. A first probe clamped to the ceiling is
  unreachable on any real file, so `hi` is set immediately and bisection converges to the
  40 ms tolerance in about 21 probes, inside `MEASURE_MAX_PROBES` (40).
- **The reached position is the last frame's own timestamp**, so the duration is one frame
  period later — the last frame is displayed, not instantaneous, and `+ period` is what
  turns "when the last frame starts" into "when the video ends". `hi` — a position at which
  the grab failed, which bisection drives down to just past that same timestamp — is
  deliberately *not* used as a ceiling; clamping to it returns a duration exactly 1/fps
  short on every file.
- **The answer is the decodable extent, not the header's claim,** and on a well-formed file
  the two agree: the fixtures measure exactly 3600 / 2000 / 1000 ms. A difference is a real
  one — a broken tail the header still counts — not an artefact of the search. (Before the
  read order was fixed, the 90-frame fixture measured 3560, and that 40 ms was documented
  here as cv2 decoding one frame fewer than `VideoWriter` emitted. It was not.)

An unmeasurable file is not a failure. It degrades to head-only samples, `end_time=None`,
indeterminate progress and an explicit warning.

**`isOpened()` is the ingest gate.** It returns False for zero-byte, truncated and
non-video payloads and True only for something decodable, so a `.mp4` extension proving
nothing about the bytes is handled without a second check. `probe_video` raises
`UnreadableVideoError`; callers surface it as a readable rejection rather than a silent
skip. Both ingest helpers copy before probing (probing the destination is what proves the
copy readable), so both delete the destination when the probe fails — an orphan in
`videos/` with no row would be re-registered by every later rescan.

## Poster frames

`video_service.generate_poster(video_path, poster_path, *, duration_ms, trim_start_ms,
trim_end_ms, size=512)` writes one WebP and returns a bool. `probe_and_poster(path,
poster_path)` is the ingest wrapper: it probes, then posters, and returns
`(info, poster_path_or_None)`. All three ingest paths call the wrapper inside the executor
hop they were already making, so a poster costs no extra round trip.

OpenCV rather than ffmpeg: one seek plus one read, and cv2 is already a dependency.
`imageio-ffmpeg` waits for extraction, where `bwdif`/crop genuinely need a filter chain.

- **Seek target** is the midpoint of the *trimmed* span, `trim_start_ms + (duration_ms −
  trim_start_ms − trim_end_ms) / 2`, not frame 0 — frame 0 of a real clip is very often a
  black leader, which makes every card in the strip look identical. Trims are all 0 until
  extraction writes them; threading them through now means a video whose trim points are
  set later re-posters onto a frame inside the kept range for free.
- **Fallback ladder**: seek and read; on failure rewind to `POS_MSEC = 0` and read the
  first frame; on failure return False. Both rungs are reachable — a NULL duration has no
  midpoint, and a header whose duration overshoots the stream seeks past the end. Failure
  is always a False and never a raise (the CLAUDE.md invariant): `has_poster` stays false
  and the UI draws a film glyph.
- **Encode**: BGR→RGB, `PIL.Image.fromarray`, `thumbnail((size, size), LANCZOS)`, WebP at
  quality 85. Mirrors `image_service.generate_thumbnail`, which hardcodes 256 for grid
  thumbs; 512 here because a 16:9 poster is 512×288 and strip cards render at ~240 px on a
  2× display. **`ImageOps.exif_transpose` is deliberately absent** and is not a violation
  of the CLAUDE.md "always transpose first" invariant — the input is a decoded ndarray
  handed over by cv2, not a file with an EXIF block, so it is outside that rule's scope.
- **Written atomically**: a uniquely-named temp file in the destination directory, then
  `os.replace`, matching `version_service._store_object`. Two concurrent lazy backfills for
  one video are ordinary (two strip cards, or a strip and a detail view); without this one
  request serves a half-written file the other is still writing. The poster directory is
  `mkdir(parents=True, exist_ok=True)`'d here — Phase 0 computed it for stem globbing but
  never created it.

**Lazy backfill.** `GET /videos/{id}/poster` cuts a poster on demand when `poster_path` is
NULL *or* the file is gone, commits the path, then serves it; it 404s only when the video
itself will not decode. This is the `generation_metadata` backfill pattern from
`GET /images/{image_id}`. Rows created before posters existed heal the first time anything
looks at them — no migration, no backfill job. So `has_poster: false` is no reason for the
UI to avoid the endpoint — both `VideoStrip`
and `VideoDetailPage` point at it regardless, the strip falling back to the glyph on the
`<img>` error event. The stem it heals onto comes from `unique_poster_path` against the
claimed set (`docs/dev/video.md` § Poster stems and collisions), not from the video's own
name, or healing one of two same-stem rows would overwrite the other's poster.

**The backfill is serialised per dataset.** `routers/videos.py::_poster_lock(dataset_id)` —
a per-key `asyncio.Lock` registry shaped after `backend/ml/model_manager.py`'s per-model
one — brackets resolve → generate → commit. The dataset is the right key because it is the
scope the claimed-stem query covers: two same-stem videos (`clip.mp4` and `clip.mkv`, which
rescan registers side by side) each resolve their poster stem against what their *siblings*
claim, and `VideoStrip` paints every card at once, so unserialised both read the same empty
directory, both pick `clip.webp`, and the second write clobbers the first — two rows
pointing at one picture.

Inside the lock the row is **re-read**, preceded by `await db.rollback()`. That rollback is
load-bearing rather than cosmetic: the initial `db.get` already opened this session's read
transaction, so a re-read inside it would still see the pre-lock snapshot and miss a
sibling request's commit. The fast path — a poster the row names and disk still has — stays
outside the lock entirely. The accepted cost is that a legacy dataset of un-postered rows
heals sequentially on first paint; in exchange each video decodes once rather than once per
card, and peak RSS is one decoded frame instead of N. Reserving the stem by creating the
file first was rejected: a failed generate then leaves a 0-byte `.webp` that the claimed-set
glob counts forever.

A failure parks that video in `routers/videos.py::_poster_failures`, an in-process
`{video_id: monotonic deadline}` map checked before regeneration is attempted
(`POSTER_RETRY_AFTER_SECONDS`, 300). Because the UI points at the endpoint
unconditionally, without it an undecodable video re-runs a full cv2 open on every strip
render of every gallery visit — cheap per call, unbounded in visits. A `poster_failed_at`
column is the durable form and is not worth a migration for a retry hint.

**An exception out of `generate_poster` is a False like any other**: caught at the endpoint,
logged, parked, 404. Letting it escape as a 500 bypassed the negative cache — so the
unbounded re-decode above happened on exactly the videos most likely to trigger it — and
reported an app fault in the JS error console for what is by design a nicety, never a gate.
It is `except Exception`, not `BaseException`: a client disconnect raises `CancelledError`
and must not park the row for five minutes.

`backend/config.py` sets `OPENCV_FFMPEG_LOGLEVEL=-8` at module scope. Rejecting a file is a
normal outcome here, but each rejection makes OpenCV's ffmpeg backend print
`[mov,mp4,…] moov atom not found` to stderr. `cv2.utils.logging.setLogLevel` does not
suppress those — they come from libavformat, not OpenCV's logger — and the variable is read
when the ffmpeg backend initialises, so setting it after the first `VideoCapture` is too
late. `config.py` is the earliest reliably-imported module; `setdefault` keeps an operator
override intact.
