# Video sources

Covers the `Video` model, the `videos/` storage layout and its poster-stem rules, and the
three ingest paths that create a video. The `/videos` request surface is in
`docs/dev/video-endpoints.md` and the arc's tests are indexed in `docs/dev/video-tests.md`,
both split out of this file. The cv2 decode surface itself
— the metadata probe, the duration search and poster generation — is in
`docs/dev/video-decode.md`, frame extraction is in `docs/dev/video-shots.md` and
`docs/dev/video-extract.md` (pass 1) and
`docs/dev/video-reextract.md` (pass 2), and every screen that shows a video is in
`docs/dev/video-ui.md`, `docs/dev/video-extract-ui.md` with
`docs/dev/video-extract-controls.md`, and `docs/dev/video-reextract-ui.md` (the extraction modal, its controls, the re-extract dialog, and the
job re-attach hook). The arc's roadmap was retired into these files when pass 2 landed,
the same way the detection arc's was.

**Videos are sources; frames are Images.** A video gets its own model, table and folder.
It is deliberately not a row in `images`, which carries ~20 image-specific columns, FK
cascades to detections, and the load-bearing "thumbnails are `.webp` keyed by stem"
invariant — none of which apply to a video file. Frame extraction converts a video
into ordinary `Image` rows at the boundary, so dedup, scoring, captioning, export and
versioning stay entirely media-unaware and needed no changes.

**`backend/media_types.py` is the single allowlist**; CLAUDE.md § Shared utilities lists
what it exports. Before it, three separate frozensets in
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
thumbnail folder with a distinguishing suffix. **Ten modules** derive image-thumbnail-stem
occupancy from a `thumb_dir.glob("*.webp")` over that folder. Nine build
`occupied_thumb_stems` to *pick* a free name — `routers/images.py`,
`captioning.py`, `comfy.py`, `lut.py`, `upscaling.py`, `detection.py`, `videos.py`,
`services/version_service.py` and `dataset_service.py` (the count is modules, not call
sites; `routers/images.py` alone holds seven). `routers/videos.py` is in that list because
frame extraction writes `Image` rows: it points `thumb_dir` at the dataset's *images*
thumbnail folder, so it shares the collision domain despite living in the video router.
The tenth is `routers/filesystem.py`'s `rename_path`, added later and easy to miss — its
local is named `occupied`, not `occupied_thumb_stems`, and it consumes the set to **409 a
rename** rather than to uniquify, so its failure mode under a suffix convention would be a
spurious refusal instead of a silent clobber. It still has to learn the filter, which is the
whole point of counting. A suffix convention would require all ten to learn one, and any one
that forgot would be a bug. A separate directory means none of them change.

## Poster stems and collisions

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
`clip_001` take a poster another row owns. `utils.poster_path_for(video_path)` derives only
the *proposal*; for an existing row read `Video.poster_path` and never re-derive it
(CLAUDE.md § Shared utilities). Moving the poster rather than the file is the opposite of
image rescan's fix for the same bug — see CLAUDE.md § Key invariants, PM-007 and
`docs/dev/image-files.md` § Importing captions & folder rescan.

## The model

`backend/models/video.py`. Dataset FK with `ondelete="CASCADE"`, indexed; `filename` unique
per dataset via `uq_dataset_video_filename`; `file_path`, `poster_path`; the metadata
columns the probe fills (`docs/dev/video-decode.md`); and the four provenance columns
mirroring `Image`'s, with the same NULL-means-inherit contract resolved by
`licenses.resolve_provenance`. There is no
`source_meta` — nothing captures scraper sidecars for video, and a deferred JSON column
would import the `MissingGreenlet` trap for no benefit. There is no `subfolder` column
either: videos are flat, and a video's extracted frames get the subfolder treatment instead.

The extraction-settings columns — `crop_x/y/w/h`, `deinterlace`, `trim_start_ms`,
`trim_end_ms` — are written by `POST /videos/extract` (`docs/dev/video-extract.md` § The
endpoints) and replayed verbatim by the full-res second pass, so pass-2 frames match pass-1
geometry exactly. Four plain integer columns rather than a JSON rect: a JSON column would
need the copy-before-mutate dance for no gain. All four NULL means no crop. Time on this row
is uniformly milliseconds, matching the `Image` frame-lineage columns.

## Ingest

Three paths create a `Video`, all sharing `probe_and_poster` (`docs/dev/video-decode.md`)
and the naming rules above. A poster failure never fails an ingest — the row is created
with `poster_path` NULL and heals on first view.

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

  **Everything the probe can raise that is *not* `UnreadableVideoError` — an `ImportError`
  from the lazy `import cv2`, a raw `cv2.error`, a `MemoryError` — is caught per file** and
  reported under `videos_failed`/`videos_failed_count`, which `rescan_dataset` folds into
  the shared `failed`/`failed_count` via `_fold_video_failures` (their own key names only so
  the `**vids` splat cannot clobber the image loop's tally; the response shape is unchanged).
  `rescan_dataset` also **commits the image pass before calling this** — the video pass runs
  before the single trailing commit, so anything escaping it discarded every `Image` row just
  staged while that pass's collision renames and thumbnails were already permanent on disk.
  The per-file guard alone does not cover it: the pre-loop `select(Video)` and
  `videos_dir.glob` can still raise.

## Stats

`refresh_stats` writes `Dataset.video_count` and `video_size_bytes` from a third query and
leaves `image_count`, `captioned_count` and `total_size_bytes` images-only. A video is
roughly 100× the size of the frames it yields, so folding it into `total_size_bytes` would
make every dataset card read as bloated, and `image_count` is what a user compares against
an export manifest. Extracted frames need no special-casing — they arrive as ordinary
`Image` rows and count themselves.

Both columns must be listed explicitly in `list_datasets`, which hand-builds each
`DatasetOut` field by field rather than validating from the ORM row; they default to 0, so
omitting them reported every dataset as video-free instead of failing (CLAUDE.md § Key
invariants).

## Frontend

Four files, all split out of this one as the frontend grew; they are read together but sized
apart. `VideoStrip` and its selection, `VideoDetailPage`, the extraction history and the frame
lineage line are in `docs/dev/video-ui.md`. The two-step `ExtractFramesModal` and the job
re-attach hook are in `docs/dev/video-extract-ui.md`, with its `CropOverlay`/`TrimBar`/
`NumberField` controls in `docs/dev/video-extract-controls.md`. Pass 2's
`ReextractFramesForm`/`ReextractFramesModal` and their three entry points are in
`docs/dev/video-reextract-ui.md`.

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
- `duplicate_dataset` carries videos only under `include_videos` (default off, preflighted,
  refused for a snapshot), remapping each copied frame's `source_video_id` onto the clone's
  own video — see `docs/dev/datasets-page.md` § Dataset duplicate.
- The file browser (`docs/dev/file-browser.md`) reports `media_kind` per entry instead of the
  old `is_image` boolean, and its move/rename/delete endpoints sync `Video` rows alongside
  `Image` rows — otherwise a video moved through the browser leaves a dangling row. That
  sync rewrites paths and nothing else, so `/filesystem/move` **refuses with a 409** to move
  a registered file — or a folder holding one — outside its own dataset, and refuses to let
  a registered video leave `{ds}/videos/` even within it (anywhere else is outside the flat
  glob `_rescan_videos` walks, so the row reads as missing forever). `/filesystem/delete`
  now removes the poster with the video and refreshes `video_count`/`video_size_bytes`.
  `materialize_provenance`, `refresh_stats` and
  NULLing `Image.source_video_id` are the things a cross-dataset move owes and that endpoint
  does none of; closing the gap by refusal leaves `batch_move_dataset` as the only path that
  re-homes an image. It was also the only code path that could ever change a
  `Video.dataset_id` — videos now have none at all, since `batch_move_dataset` moves `Image`
  rows only. A video changes dataset by being re-uploaded, or not at all.
