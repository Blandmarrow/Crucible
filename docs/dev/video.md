# Video sources

Covers the `Video` model, the `videos/` storage layout and its poster-stem rules, and the
three ingest paths that create a video. It is also the arc's entry point: the table below
routes from a code path to whichever sibling doc owns it. The arc's roadmap was retired
into these files when pass 2 landed, the same way the detection arc's was.

## Where things live

Video is documented across twelve files, so the reliable way in is the code file you are
about to open rather than the topic you have in mind — a reader always knows the former.
Each row names the doc that **owns** that code. A `+` clause is a second genuine owner,
not a see-also, and says which part of the file it owns.

### Backend

| Editing… | Read |
|---|---|
| `backend/media_types.py` | This file — the single ingestible-extension allowlist |
| `backend/models/video.py` | This file § The model |
| `backend/routers/images.py` (its video branch) | This file § Ingest — the gallery-upload path, `_write_video_upload_sync` and the `skipped` response |
| `backend/routers/videos.py` | `docs/dev/video-endpoints.md`; + `docs/dev/video-extract.md` for the extract and probe routes; + `docs/dev/video-reextract.md` for `POST /videos/reextract` and `POST /videos/reextract/preview` |
| `backend/schemas/video.py` | `docs/dev/video-extract.md` (pass 1 bodies), `docs/dev/video-reextract.md` (pass 2); + `docs/dev/video-endpoints.md` for `VideoOut`, `VideoFramesSummary` and `RenameVideoRequest` |
| `backend/services/video_service.py` | `docs/dev/video-decode.md` — the probe ladder, duration search and poster generation; + this file § Poster stems and collisions for `claimed_poster_stems` |
| `backend/services/video_extract.py` | `docs/dev/video-shots.md` — sampling, shot detection, `render_shot`; + `docs/dev/video-extract.md` for the `video_extract` job; + `docs/dev/video-reextract.md` for `render_at_timestamps` |
| `backend/services/video_frames.py` | `docs/dev/video-heuristics.md` — the pure-numpy judgement calls |
| `backend/services/dataset_service.py` (its video half) | This file § Ingest — folder import, `_rescan_videos` and the poster rename-on-collision; + `docs/dev/datasets-page.md` § Dataset duplicate for `duplicate_dataset`'s video loop and `_copy_video_sync` |
| `backend/tests/test_video_*.py` | `docs/dev/video-tests.md` — also the cv2 gate and the skip convention |

### Frontend

| Editing… | Read |
|---|---|
| `frontend/src/api/videos.ts` | `docs/dev/video-endpoints.md` — it is a thin client for the twelve routes and carries no behaviour of its own |
| `frontend/src/components/gallery/VideoStrip.tsx` | `docs/dev/video-ui.md` § VideoStrip |
| `frontend/src/pages/VideoDetailPage.tsx` | `docs/dev/video-ui.md` § VideoDetailPage |
| `frontend/src/components/video/ExtractFramesModal.tsx` | `docs/dev/video-extract-ui.md` § ExtractFramesModal — `ExtractProgressList` lives inline in this file |
| `frontend/src/hooks/useVideoExtractJobs.ts` | `docs/dev/video-extract-ui.md` § Re-attaching to the job |
| `frontend/src/components/video/CropOverlay.tsx` | `docs/dev/video-extract-controls.md` § CropOverlay |
| `frontend/src/components/video/TrimBar.tsx` | `docs/dev/video-extract-controls.md` § TrimBar |
| `frontend/src/components/common/NumberField.tsx` | `docs/dev/video-extract-controls.md` § NumberField for the full draft contract; CLAUDE.md carries the one-line rule |
| `frontend/src/components/video/ReextractFramesForm.tsx` | `docs/dev/video-reextract-ui.md` § ReextractFramesForm |
| `frontend/src/components/video/ReextractFramesModal.tsx` | `docs/dev/video-reextract-ui.md` § ReextractFramesModal — and § The three entry points |
| `frontend/src/utils/videoPlayback.ts` | `docs/dev/video-ui.md` — the shared playback classifier: `playbackErrorMessage` and `browserPlaybackHint` |
| `frontend/src/components/video/UnplayableOverlay.tsx` | `docs/dev/video-ui.md` — it renders what that classifier returns, over the poster |
| `frontend/e2e/video-extract.spec.ts` | `docs/dev/video-extract-controls.md` § End-to-end coverage |
| `frontend/e2e/video-delete.spec.ts` | `docs/dev/video-ui.md` § VideoStrip — the Delete button, the image-selection precedence rule and the split-view pane guard |

Every path above is inline code ending in a file extension, so `scripts/check_docs.py`
verifies it resolves — a moved or deleted module breaks the check rather than silently
rotting the index. Two habits keep that guarantee when adding a row: write the full path
(a token without a `/` is not treated as a path and loses its verification), and prefer a
glob like `backend/tests/test_video_*.py` over a bare directory, since `looks_like_path`
only checks tokens ending in an extension while `path_exists` expands globs.

**Videos are sources; frames are Images.** A video gets its own model, table and folder.
It is deliberately not a row in `images`, which carries ~20 image-specific columns, FK
cascades to detections, and the load-bearing "thumbnails are `.webp` keyed by stem"
invariant — none of which apply to a video file. Frame extraction converts a video
into ordinary `Image` rows at the boundary, so dedup, scoring, captioning, export and
versioning stay entirely media-unaware and needed no changes.

**`backend/media_types.py` is the single allowlist**; `docs/dev/shared-utilities.md` lists
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

One site writes a poster and takes **no** guard, deliberately: `duplicate_dataset`'s video
loop copies both basenames verbatim into fresh destination folders, so it reproduces a
resolution the source has already made rather than computing a new one. The code says not to
convert it into a `claimed_poster_stems`/`unique_poster_path` call. It is an exception to
the sentence above, not a hole in it — but it is the one site a reviewer working from this
checklist would otherwise flag.

That divergence is why stored stems are a separate term from filename stems — afterwards
the two disagree, and a set built from filenames alone would let a later upload named
`clip_001` take a poster another row owns. `utils.poster_path_for(video_path)` derives only
the *proposal*; for an existing row read `Video.poster_path` and never re-derive it
(`docs/dev/shared-utilities.md`). Moving the poster rather than the file is the opposite of
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

Three *ingest* paths create a `Video` from a file on disk, all sharing `probe_and_poster`
(`docs/dev/video-decode.md`) and the naming rules above. A poster failure never fails an
ingest — the row is created with `poster_path` NULL and heals on first view. A **fourth**
path constructs `Video` rows without ingesting anything: `duplicate_dataset` under
`include_videos` clones existing rows, re-probing nothing (`_copy_video_sync` copies bytes
and poster with `shutil.copy2` and every probe column is read off the source row, so a slow
decode is not repeated) and copying both basenames verbatim with no uniquifier — see § What
is free, and what is not.

Each ingest path has a named helper, and the two that copy before probing differ in nothing
now that both unlink on any exception (they did not always: folder import once unlinked only
on the gate error, leaving cv2's lazy `ImportError`, a raw `cv2.error` or a `MemoryError` to
strand an orphan — see `docs/dev/video-decode.md`).

- **Gallery upload** (`POST /images/upload`), via `_write_video_upload_sync`, branches on
  `media_kind_for(suffix)`. The
  endpoint name is a mild misnomer, kept because the gallery presents one file input and
  splitting it would mean two progress states and two error surfaces for one gesture. The
  response gained `videos_added`, `videos` and `skipped`: previously the loop `continue`d
  on an unknown suffix and the response counted only successes, so a rejected file was
  indistinguishable from a stored one. `skipped` entries carry a reason and arrive with a
  201 — one bad file must not fail the rest of a multi-file upload.
- **Folder import** takes `include_videos`, defaulting to False, and copies through
  `_ingest_video_sync`. `_scan_source_files` now
  returns `(images, videos, total_bytes)` from one traversal, so `require_free_space`
  preflights video bytes automatically whenever they are being copied and never counts
  bytes that will not be. Videos land flat regardless of `subfolder` or
  `preserve_structure`. `shutil.copy2`, not `copy_with_sidecar`: a video has no `.txt`
  companion — captions belong to the frames.
- **Rescan** has no such helper — the file is already in place, so it calls
  `probe_and_poster` directly. It needs its own pass, `_rescan_videos`, because the image
  walk is
  `images_dir.rglob("*")` and cannot see `videos/` at all. That walk is a **flat** glob:
  videos are never nested, and flat conveniently skips the `videos/thumbnails/` child that
  a recursive walk would scan for video files. Results come back as `videos_added` and
  `videos_missing`, their own keys rather than folded into `missing`, whose entries are
  image-shaped `{subfolder, filename}`. An undecodable file in `videos/` is logged and
  skipped, not reported as a failure. It adopts the filenames it finds and disambiguates
  the poster instead, per the stem rules above — the chosen stem is added to `claimed`
  inside the loop, since `unique_poster_path` does not mutate the set.

  It is **cancellable**, which the four subtleties above might suggest it is not. It polls
  `job_queue.cancel_requested` per file while still walking to the end so `seen` stays
  complete, and `rescan_dataset` both skips the video pass outright when the image loop was
  already cancelled and re-reads the flag afterwards. Without the poll, cancelling during a
  minutes-long video pass did nothing and the job then reported *success*.

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
