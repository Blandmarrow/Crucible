# Video sources

Covers the `Video` model, the `videos/` storage layout, video metadata extraction, poster
frames, the three ingest paths that can create a video, and the frontend surfaces that
show one. Frame extraction is not built yet — this file grows as that phase lands. The
arc's roadmap lives in `roadmap.md` at the repo root until the arc is complete.

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

**Poster stem collisions are avoided at every site that writes a poster** — a larger set
than the sites that pick a filename, and getting that wrong is what made two rows share one
poster. `video_service.claimed_poster_stems(rows, poster_dir, exclude_id=None)` is the
single source of truth for which poster names are taken. It is pure (callers run the
`select`, the shape `licenses.materialize_by_source` uses) and unions three terms:

- **stored `poster_path` stems** — what is actually claimed. A poster stem is *not*
  guaranteed to equal its video's; see rescan below.
- **`Video.filename` stems** — the reservation for a poster not yet cut. A row whose
  generation failed, or one ingested before posters existed, has nothing on disk and no
  `poster_path`, but claims its own stem the first time anything views it.
- **posters already in the directory** — covers a file whose row is gone.

Two kinds of site consume it. Those that **pick** a filename (upload, folder import,
`PATCH /videos/{id}/rename`) feed it to `utils.unique_filename_with_thumb` as the
occupied-stem set. Those that **adopt** one (`_rescan_videos`, the `GET /videos/{id}/poster`
backfill) cannot rename to dodge a clash, so they feed it to
`utils.unique_poster_path(poster_dir, stem, claimed)` and move the *poster* instead:
`clip.mp4` keeps `clip.webp`, `clip.mkv` gets `clip_001.webp`, and both files keep the
names the user gave them.

That divergence is why stored stems are a separate term from filename stems — afterwards
the two disagree, and a set built from filenames alone would let a later upload named
`clip_001` take a poster another row owns. `utils.poster_path_for(video_path)` derives the
*proposal* (`parent`, not `thumbnail_path_for`'s `parent.parent`, because videos are flat);
for an existing row read `Video.poster_path` and never re-derive it. Moving the poster
rather than the file is the opposite of image rescan's fix for the same bug — nothing
re-derives a poster path, where eleven sites re-derive a thumbnail's. See PM-007 and
`docs/dev/image-files.md` § Importing captions & folder rescan.

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
  printable — a guard, not a formality, against the `0` and `-1` that a container with no
  usable code reports; see that function's docstring for why stripping whitespace does not
  catch them.
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

## Poster frames

`video_service.generate_poster(video_path, poster_path, *, duration_ms, trim_start_ms,
trim_end_ms, size=512)` writes one WebP and returns a bool. `probe_and_poster(path,
poster_path)` is the ingest wrapper: it probes, then posters, and returns
`(info, poster_path_or_None)`. All three ingest paths call the wrapper inside the executor
hop they were already making, so a poster costs no extra round trip.

OpenCV rather than ffmpeg, because this is one seek plus one read and cv2 is already a
dependency. `imageio-ffmpeg` waits for extraction, where `bwdif`/crop genuinely need a
filter chain.

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
looks at them, so the feature needed no migration and no backfill job. It follows that
`has_poster: false` is not a reason for the UI to avoid the endpoint — both `VideoStrip`
and `VideoDetailPage` point at it regardless, the strip falling back to the glyph on the
`<img>` error event. The stem it heals onto comes from `unique_poster_path` against the
claimed set above, not from the video's own name, or healing one of two same-stem rows
would overwrite the other's poster.

A failure parks that video in `routers/videos.py::_poster_failures`, an in-process
`{video_id: monotonic deadline}` map checked before regeneration is attempted
(`POSTER_RETRY_AFTER_SECONDS`, 300). Because the UI points at the endpoint
unconditionally, without it an undecodable video re-runs a full cv2 open on every strip
render of every gallery visit — cheap per call, unbounded in visits. A `poster_failed_at`
column is the durable form and is not worth a migration for a retry hint.

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

Three paths create a `Video`, all sharing `probe_and_poster` and the naming rules above. A
poster failure never fails an ingest — the row is created with `poster_path` NULL and heals
on first view.

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
  skipped, not reported as a failure. It adopts the filenames it finds and disambiguates
  the poster instead, per the stem rules above — the chosen stem is added to `claimed`
  inside the loop, since `unique_poster_path` does not mutate the set.

## Stats

`refresh_stats` writes `Dataset.video_count` and `video_size_bytes` from a third query and
leaves `image_count`, `captioned_count` and `total_size_bytes` images-only. A video is
roughly 100× the size of the frames it yields, so folding it into `total_size_bytes` would
make every dataset card read as bloated, and `image_count` is what a user compares against
an export manifest. Extracted frames need no special-casing — they arrive as ordinary
`Image` rows and count themselves.

Both columns must be listed explicitly in `list_datasets`, which hand-builds each
`DatasetOut` field by field rather than validating from the ORM row. They default to 0 on
the schema, so omitting them reported every dataset as video-free instead of failing —
which is exactly what happened until the dataset card tried to read them.

## Endpoints

`backend/routers/videos.py`, prefix `/videos`: `GET /` (by `dataset_id`), `GET /{id}`,
`GET /{id}/file`, `GET /{id}/poster` (see the backfill above), `PATCH /{id}/rename`,
`DELETE /{id}`. Both file responses go through `utils.safe_dataset_path`, promoted out of
`routers/images.py` so the video routes are not importing a private helper from another
router.

`PATCH /{id}/rename` takes `{new_stem}` and is a near-verbatim mirror of
`routers/images.py::rename_image` — `ensure_not_busy`, the same stem validation and
`slugify_filename`, sibling `Video.filename`s as `db_names`, the dual occupied-poster-stem
set described above, `unique_filename_with_thumb`, then ORM fields → filesystem →
`commit` last so a filesystem failure means no commit runs. Two deliberate differences:
the **suffix is preserved and never user-settable**, because the container extension is a
claim about the bytes and `video_mime` picks the browser's decoder from it; and it uses
`Path.rename`, not `rename_with_sidecar`, since a video has no `.txt` companion. Renaming
to a stem the row already holds steps the counter (`clip` → `clip_001.mp4`) because no
`disk_exclude` is passed — `rename_image` does the same, and the tests pin it so the two
cannot drift apart.

The **file browser preview** widened with this phase: `GET /filesystem/preview` accepts
anything in `MEDIA_EXTENSIONS` and serves the video branch with `video_mime`, so one route
covers both kinds and `FileResponse` supplies the Range/206 a `<video>` needs to seek. It
serves an arbitrary absolute path, which is what that deliberately unsandboxed
local-desktop router already did for images (`docs/dev/workspace.md` § Path safety) — the
wider allowlist adds no new exposure class. `GET /image-meta` stays image-only: a container
carries no EXIF or generation parameters.

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

## Frontend

`frontend/src/utils/duration.ts::formatDuration(ms)` → `"4:12"`, `"1:02:33"`, `"—"` for
NULL. Every video surface formats through it, because NULL is *unknown* and must never
render as `0:00` — that would turn a missing header into a claim about the video.
`videosApi.posterUrlVersioned(id, updatedAt)` mirrors `imagesApi.thumbnailUrlVersioned`:
the poster URL is keyed by id alone, so a regenerated or renamed poster would otherwise
serve stale from cache.

**`components/gallery/VideoStrip.tsx`** — a collapsible horizontal strip above the image
grid, keyed on `["videos", datasetId]` (already invalidated after upload and rescan).
Renders `null` when the dataset has no videos, so an image-only dataset looks untouched.
Collapse state persists per dataset under `VIDEO_STRIP_COLLAPSED_KEY`. Cards show the
poster (or a `Film` glyph), a duration badge and the filename, and open the detail view
via `usePaneNavigate`.

It is mounted in `GalleryPage` **outside** the `<DndContext>` and outside the grid's
scroll container, and that placement is load-bearing: inside the context the cards would
join the grid's collision detection and its subfolder drop targets, and inside the
container they would sit under the drag-to-upload handler. There is no selection here yet
— its only consumer would be batch extraction, and a checkbox that enables nothing is
worse than no checkbox.

**`pages/VideoDetailPage.tsx`** — player, metadata grid, inline rename, read-only
provenance via `LicenseBadge`, a disabled "Extract frames" button and delete. No crop,
upscale, LUT, detection or caption: those belong to the frames. Prev/next needs none of
the gallery's nav-context plumbing — `["videos", datasetId]` is a single unpaginated
query, so the page indexes into it directly and `gallery-nav-*`, `injectNavId` and the
boundary prefetches do not apply. The arrow-key handler carries the usual
active-pane and text-field guards **plus** one for `VIDEO` focus, since the browser binds
arrows to seek there. The delete confirmation states the Phase 0 contract explicitly —
*"Extracted frames are not deleted"* — because `DELETE /videos/{id}` never touches `Image`
rows and that is not what a user expects.

Route registration follows the six-site pattern in `docs/dev/frontend-core.md`
(§ Route-level code splitting). The `/datasets/:id/video/:vid` regex in `routeToView` sits
**above** the generic `dsPageMatch`, same hazard as the image regex: the generic pattern
also matches and would yield an invalid `page: "video"`. `video-detail` is deliberately
absent from `PaneHeader.PAGE_OPTIONS`, exactly as `image-detail` is — the dropdown cannot
supply a `videoId`.

`DatasetsPage` shows a video pill in the card footer and a `N vid` entry in the compact
row, both hidden at zero and both changed together. `FileBrowserPage` renders a `<video
controls preload="metadata">` in its preview panel for a `media_kind === "video"` entry
and skips the image-only `["fs-image-meta", path]` query for it.

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
gate), `test_video_ingest_http.py` (upload routing, stem collisions, stats separation and
the `GET /datasets/` video columns), `test_video_import_rescan_http.py` (the
`include_videos` default and its preflight, flat landing, the rescan pass),
`test_video_serving_http.py` (range requests, the poster backfill and its stem resolution,
the failure backoff, delete), `test_video_poster.py` (midpoint seek, both fallback rungs,
downscale, the atomic write) and `test_video_rename_http.py` (slugify, both collision
classes, the poster move, the busy 409). `test_http_smoke_crud.py` covers
`/filesystem/preview` for both media kinds, and `test_http_smoke_jobs.py` pins the image
side of the same stem-collision rule.

The rescan-collision rule is pinned in three separable parts, since each can break alone:
same-stem containers rescanned together get distinct posters; an ordinary rescan renames
*nothing*; and an upload after a disambiguation cannot take the poster stem whose filename
no longer matches it.

The repo ships no sample media, so `conftest.mp4_bytes()` synthesizes a real `.mp4` with
`cv2.VideoWriter`. Use the `mp4v` fourcc: `avc1` needs an h264 encoder the opencv-python
wheel does not carry and its writer silently fails to open. `VideoWriter` cannot write to a
buffer, hence the temp file.
