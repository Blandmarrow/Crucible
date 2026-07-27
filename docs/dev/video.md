# Video sources

Covers the `Video` model, the `videos/` storage layout, video metadata extraction, and
the three ingest paths that can create a video. Frame extraction, the video strip and the
video detail view are not built yet — this file grows as those phases land. The arc's
roadmap lives in `roadmap.md` at the repo root until the arc is complete.

**Videos are sources; frames are Images.** A video gets its own model, table and folder.
It is deliberately not a row in `images`, which carries ~20 image-specific columns, FK
cascades to detections, and the load-bearing "thumbnails are `.webp` keyed by stem"
invariant — none of which apply to a video file. Frame extraction will convert a video
into ordinary `Image` rows at the boundary, so dedup, scoring, captioning, export and
versioning stay entirely media-unaware and need no changes.

**`backend/media_types.py` is the single allowlist.** It exports `IMAGE_EXTENSIONS`,
`VIDEO_EXTENSIONS`, `MEDIA_EXTENSIONS`, `media_kind_for(suffix) -> "image" | "video" | None`,
`video_mime(suffix)` and `codec_label(fourcc)`. Before it, three separate frozensets in
`routers/filesystem.py`, `routers/images.py` and `services/dataset_service.py` decided what
was importable, and they had drifted: only the file browser's carried `.avif`, so a folder
of AVIFs listed fine and imported as nothing. `.avif` resolved upward when they merged.
AVIF is a *build-time* Pillow feature, so the entry is gated on `features.check("avif")`
rather than on the `Pillow>=11.3` pin — a source build without it would otherwise recreate
the same drift one layer down. Naming is `media_kind`-shaped, never `is_video` booleans, so
an eventual audio arc adds a value instead of a second flag; that applies to API fields and
helper signatures only, not to the schema, which splits into two tables on purpose.

**Storage layout.** Video files live flat in `{dataset.folder_path}/videos/`, created
lazily on first ingest so image-only datasets never grow an empty directory. Poster
thumbnails go in `{dataset}/videos/thumbnails/` — a **separate** directory, not the images
thumbnail folder with a distinguishing suffix. Eight code paths build `occupied_thumb_stems`
from `thumb_dir.glob("*.webp")`; a suffix convention would require all eight to learn a
filter, and any one that forgot would be a silent thumbnail clobber. A separate directory
means none of them change.

**Poster stem collisions are avoided against the DB, not only against disk.** Video
filenames go through the same `utils.unique_filename_with_thumb` as images, passed the
`videos/` and `videos/thumbnails/` pair. The occupied-stem set is seeded from existing
`Video.filename` stems as well as from any posters on disk. Image thumbnails are written
during ingest, so globbing their directory is enough for images; a video's poster is
generated in a later phase, so until then that directory is empty and globbing it alone
would let `a.mp4` and `a.mkv` both claim the stem `a` — and whichever poster was written
later would overwrite the other's.

## Metadata: the ladder and its guard

`services/video_service.py::probe_video(path)` reads the container header only — no decode
pass — and returns `{width, height, fps, codec, duration_ms, file_size_bytes}`. It is
blocking; every caller runs it through `run_in_executor`. `cv2` is imported lazily inside
the function, matching the convention in `backend/ml/technical_scorer.py`.

- fps, dimensions and codec come straight from `cv2.VideoCapture`, which reads mp4, mkv,
  webm, mov, avi and ts, including HEVC 10-bit and ProRes 422 HQ.
- `CAP_PROP_FOURCC` gives a stable 4-character code, decoded by
  `media_types.fourcc_to_code` and stored raw on the row. `codec_label` maps it for
  display and falls back to the code itself, so an unrecognised codec renders as `apch`
  rather than as an empty cell. The decode is NULL unless the result is ASCII **and**
  printable, which is a guard and not a formality: a container with no usable code
  reports `0` or `-1`, decoding to four NUL bytes or four `0xFF` bytes. Both are truthy
  strings that stripping whitespace leaves intact, so they would be persisted, returned
  in the API response, and echoed back by `codec_label` — an unrecognised codec must
  degrade to *its own code*, and those are not codes.
- Duration is `frames / fps`, **only** when `fps > 0` and `0 < frames < 1e9`; otherwise
  `duration_ms` is NULL and renders as "unknown". The guard is not defensive padding: a
  matroska written to a non-seekable pipe — no duration, no cues, which is what
  stream-ripped and partially-copied files look like — reports
  `CAP_PROP_FRAME_COUNT = -230584300921369408` while every other field stays correct, and a
  naive divide stores a duration of −9.2e15 seconds. Parsing the `ffmpeg -i` banner is no
  rescue; it prints `Duration: N/A` for exactly that file. The probe step of frame
  extraction will backfill a true duration, since it is already decoding.

**`isOpened()` is the ingest gate.** It returns False for zero-byte, truncated and
non-video payloads and True only for something decodable, so a `.mp4` extension proving
nothing about the bytes is handled without a second check. `probe_video` raises
`UnreadableVideoError`; callers surface it as a readable rejection rather than a silent
skip. Both ingest helpers copy before probing (probing the destination is what proves the
copy readable), so both delete the destination when the probe fails — an orphan in
`videos/` with no row would be re-registered by every later rescan.

`backend/config.py` sets `OPENCV_FFMPEG_LOGLEVEL=-8` at module scope. Rejecting a file is a
normal outcome here, but each rejection makes OpenCV's ffmpeg backend print
`[mov,mp4,…] moov atom not found` to stderr. `cv2.utils.logging.setLogLevel` does not
suppress those — they come from libavformat, not OpenCV's logger — and the variable is read
when the ffmpeg backend initialises, so setting it after the first `VideoCapture` is too
late. `config.py` is the earliest reliably-imported module; `setdefault` keeps an operator
override intact.

## The model

`backend/models/video.py`. Dataset FK with `ondelete="CASCADE"`, indexed; `filename` unique
per dataset via `uq_dataset_video_filename`; `file_path`, `poster_path`; the metadata
columns above; and the four provenance columns mirroring `Image`'s, with the same
NULL-means-inherit contract resolved by `licenses.resolve_provenance`. There is no
`source_meta` — nothing captures scraper sidecars for video, and a deferred JSON column
would import the `MissingGreenlet` trap for no benefit. There is no `subfolder` column
either: videos are flat, and a video's extracted frames get the subfolder treatment instead.

The extraction-settings columns — `crop_x/y/w/h`, `deinterlace`, `trim_start_ms`,
`trim_end_ms` — are written when a user confirms the probe step of the extraction modal and
replayed verbatim by the full-res second pass, so pass-2 frames match pass-1 geometry
exactly. Four plain integer columns rather than a JSON rect: a JSON column would need the
copy-before-mutate dance for no gain. All four NULL means no crop. Time on this row is
uniformly milliseconds, matching the frame lineage columns that arrive with extraction.

## Ingest

Three paths create a `Video`, all of them sharing `probe_video` and the naming rules above.

- **Gallery upload** (`POST /images/upload`) branches on `media_kind_for(suffix)`. The
  endpoint name is a mild misnomer, kept because the gallery presents one file input and
  splitting it would mean two progress states and two error surfaces for one gesture. The
  response gained `videos_added`, `videos` and `skipped`: previously the loop `continue`d
  on an unknown suffix and the response counted only successes, so a rejected file was
  indistinguishable from a stored one. `skipped` entries carry a reason and arrive with a
  201 — one bad file must not fail the rest of a multi-file upload.
- **Folder import** takes `include_videos`, defaulting to False. `_scan_source_files` now
  returns `(images, videos, total_bytes)` from one traversal, so `require_free_space`
  preflights video bytes automatically whenever they are being copied and never counts
  bytes that will not be. Videos land flat regardless of `subfolder` or
  `preserve_structure`. `shutil.copy2`, not `copy_with_sidecar`: a video has no `.txt`
  companion — captions belong to the frames.
- **Rescan** needs its own pass, `_rescan_videos`, because the image walk is
  `images_dir.rglob("*")` and cannot see `videos/` at all. That walk is a **flat** glob:
  videos are never nested, and flat conveniently skips the `videos/thumbnails/` child that
  a recursive walk would scan for video files. Results come back as `videos_added` and
  `videos_missing`, their own keys rather than folded into `missing`, whose entries are
  image-shaped `{subfolder, filename}`. An undecodable file in `videos/` is logged and
  skipped, not reported as a failure.

## Stats

`refresh_stats` writes `Dataset.video_count` and `video_size_bytes` from a third query and
leaves `image_count`, `captioned_count` and `total_size_bytes` images-only. A video is
roughly 100× the size of the frames it yields, so folding it into `total_size_bytes` would
make every dataset card read as bloated, and `image_count` is what a user compares against
an export manifest. Extracted frames need no special-casing — they arrive as ordinary
`Image` rows and count themselves.

## Endpoints

`backend/routers/videos.py`, prefix `/videos`: `GET /` (by `dataset_id`), `GET /{id}`,
`GET /{id}/file`, `GET /{id}/poster` (404 until posters exist), `DELETE /{id}`. Both file
responses go through `utils.safe_dataset_path`, promoted out of `routers/images.py` so the
video routes are not importing a private helper from another router.

Range support is not written by the code — it comes from returning a `FileResponse`, which
sets `accept-ranges: bytes` and handles `Range`, `If-Range` and 416 itself. Swapping it for
a `StreamingResponse` or a hand-rolled read would silently remove `<video>` seeking while
playback still appeared to work, so `test_video_serving_http.py` pins the 206 and 416 paths.
`video_mime` supplies the Content-Type because `mimetypes.guess_type` is unreliable for
`.mkv` and `.avi`, and the browser picks its decoder from that header.

`DELETE` removes the file, the poster and the row, then refreshes stats. It never touches
`Image` rows: frames extracted from a video are curated data, and deleting a source must
not destroy them.

It unlinks **before** committing the row delete — the reverse of `delete_image`, and
deliberate. If the commit fails, the row survives pointing at nothing, which
`_rescan_videos` reports under `videos_missing` and the user can retry. Committing first
and then failing the unlink would leave an orphan in `videos/` that the next rescan
silently re-registers, undoing the delete.

## What is free, and what is not

- `delete_dataset` rmtrees the whole dataset folder and the FK cascades, so videos go with
  it. The cascade is asserted against the DDL rather than end to end, because the test
  harness builds its schema with `create_all` on its own engine and never gets the
  `PRAGMA foreign_keys=ON` that `backend/database.py` installs on the app engine.
- Export iterates `Image` rows and never walks the dataset folder, so videos cannot leak
  into an export.
- Versioning only ever touches `images/`, `thumbnails/` and `.versions/objects/` and never
  walks the dataset root, so "snapshots do not capture videos" needs no code to enforce.
  That has a useful consequence: restoring a pre-extraction snapshot deletes the frames but
  leaves the video and its saved decode parameters intact, so re-running extraction
  reproduces them exactly.
- `duplicate_dataset` copies only `Image` rows, so a duplicate has no videos.
- The file browser (`docs/dev/workspace.md`) reports `media_kind` per entry instead of the
  old `is_image` boolean, and its move/rename/delete endpoints sync `Video` rows alongside
  `Image` rows — otherwise a video moved through the browser leaves a dangling row. That
  sync is not complete, and the gap predates videos: a cross-dataset `/move` sets
  `dataset_id` without `materialize_provenance` and without `refresh_stats`, so the moved
  row silently re-inherits the destination's defaults and both datasets' counts stay stale
  until the next refresh. Already true for `Image`; now true for `Video` too.

## Tests

`test_media_types.py` (allowlist properties, the AVIF gate, codec fallback),
`test_video_probe.py` (the metadata ladder, every untrustworthy frame count, the ingest
gate), `test_video_ingest_http.py` (upload routing, stem collisions, stats separation),
`test_video_import_rescan_http.py` (the `include_videos` default and its preflight, flat
landing, the rescan pass) and `test_video_serving_http.py` (range requests, delete).

The repo ships no sample media, so `conftest.mp4_bytes()` synthesizes a real `.mp4` with
`cv2.VideoWriter`. Use the `mp4v` fourcc: `avc1` needs an h264 encoder the opencv-python
wheel does not carry and its writer silently fails to open. `VideoWriter` cannot write to a
buffer, hence the temp file.
