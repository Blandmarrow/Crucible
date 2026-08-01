# Video endpoints

The `/videos` request surface: `backend/routers/videos.py`'s twelve registered routes, what
each answers, and the ordering and containment rules the destructive ones follow. Four of the
twelve are documented elsewhere because they belong to the extraction arc rather than to this
router's own surface — `POST /{id}/probe` and `POST /extract` in `docs/dev/video-extract.md`,
`POST /reextract/preview` and `POST /reextract` in `docs/dev/video-reextract.md`. The `Video`
model, the `videos/` storage layout, the poster-stem rules and the three ingest paths are in
`docs/dev/video.md`; the decode work behind `GET /{id}/poster` is in `docs/dev/video-decode.md`;
the screens that call all of this are in `docs/dev/video-ui.md`. Tests are indexed in
`docs/dev/video-tests.md`.

## The twelve routes

`backend/routers/videos.py`, prefix `/videos`, all twelve registered routes: `GET /` (by
`dataset_id`), `GET /capabilities`, `GET /{id}`,
`GET /{id}/file`, `GET /{id}/poster` (the lazy backfill and its retry backoff are in
`docs/dev/video-decode.md` § Poster frames), `GET /{id}/frames-summary`,
`POST /{id}/probe` and `POST /extract` (both
in `docs/dev/video-extract.md` § The endpoints),
`POST /reextract/preview` and `POST /reextract` (pass 2 — `docs/dev/video-reextract.md`),
`PATCH /{id}/rename`, `DELETE /{id}`.
`GET /capabilities` reports what this install can decode independently of any one video, and
is **declared above `GET /{video_id}`** on purpose: FastAPI matches in declaration order, so
below it the literal segment would be read as a video id and 404 (`docs/dev/video-extract.md`
§ The endpoints, where the route and its `["extract-capabilities"]` consumer are documented).
Both file responses go through `utils.safe_dataset_path`, promoted out of
`routers/images.py` so the video routes are not importing a private helper from another
router. `GET /{id}/file` and `PATCH /{id}/rename` both answer 404 "File not found on disk"
for a row whose file is gone.

## Rename

`PATCH /{id}/rename` takes `{new_stem}` and is a near-verbatim mirror of
`routers/images.py::rename_image` — `ensure_not_busy`, the same stem validation and
`slugify_filename`, sibling `Video.filename`s as `db_names`, the three-term
occupied-poster-stem set described in `docs/dev/video.md` § Poster stems and collisions,
`unique_filename_with_thumb`, then ORM fields → the file rename →
`commit` → the poster move as a **post-commit epilogue**. A failed rename means no commit;
a failed poster move is logged and cannot undo one, and `poster_path` names the new poster
either way so the lazy backfill recuts it. A row whose file vanished underneath it 404s
before any of that — after the containment predicate, so an out-of-tree path still 403s.
Two deliberate differences:
the **suffix is preserved and never user-settable**, because the container extension is a
claim about the bytes and `video_mime` picks the browser's decoder from it; and it uses
`Path.rename`, not `rename_with_sidecar`, since a video has no `.txt` companion. Renaming
to a stem the row already holds steps the counter (`clip` → `clip_001.mp4`) because no
`disk_exclude` is passed — `rename_image` does the same, and the tests pin it so the two
cannot drift apart.

## Frames summary

`GET /{id}/frames-summary` answers `{total, groups: [{subfolder, count,
last_extracted_at}]}` — one `GROUP BY Image.subfolder` over `source_video_id`, ordered by
`MAX(created_at)` descending so the most recent extraction leads, 404 on an unknown video.
`""` is a real group (frames at the dataset root), never coalesced into "no subfolder". It
is the server-side counterpart of the rowcount `delete_video` logs, and it feeds three
surfaces that all need the number *before* anything is destroyed: the extraction history
panel, the delete-confirm count, and the modal's "Replace (deletes N previous frames)"
label. It answers a different question from `GET /images/`'s `source_video_id` filter, which
shipped later: a group here is *where an extraction landed* and stops being useful the
moment a frame is moved out, while the filter finds every frame a video ever produced
wherever curation has since filed it (`docs/dev/video-ui.md`).

## Serving bytes, and the file browser preview

The **file browser preview** widened with this phase: `GET /filesystem/preview` accepts
anything `media_kind_for` recognises — i.e. all of `MEDIA_EXTENSIONS`, though that name is
what the router branches on and the set itself is never imported there; `None` is a 400 —
and serves the video branch with `video_mime`, so one route
covers both kinds and `FileResponse` supplies the Range/206 a `<video>` needs to seek. It
serves an arbitrary absolute path, which is what that deliberately unsandboxed
local-desktop router already did for images (`docs/dev/file-browser.md` § Path safety) — the
wider allowlist adds no new exposure class. `GET /image-meta` stays image-only: a container
carries no EXIF or generation parameters.

Range support is not written by the code — it comes from returning a `FileResponse`, which
sets `accept-ranges: bytes` and handles `Range`, `If-Range` and 416 itself. Swapping it for
a `StreamingResponse` or a hand-rolled read would silently remove `<video>` seeking while
playback still appeared to work, so `test_video_serving_http.py` pins the 206 and 416 paths.
`video_mime` supplies the Content-Type because `mimetypes.guess_type` is unreliable for
`.mkv` and `.avi`, and the browser picks its decoder from that header.

## Delete

`DELETE` removes the file, the poster and the row, then refreshes stats. It never touches
`Image` rows: frames extracted from a video are curated data, and deleting a source must
not destroy them. It does clear their `Image.source_video_id`, with an explicit `UPDATE`
rather than by relying on the FK's `ON DELETE SET NULL` — the test harness builds its
schema with `create_all` and never gets the `PRAGMA foreign_keys=ON` that
`backend/database.py` installs, so the FK's behaviour is untestable here; it stays as
belt-and-braces. `source_timestamp_ms` and `source_shot_index` survive, so a frame keeps
knowing where in a video it came from once the video is gone.

It unlinks **before** committing the row delete — the reverse of `delete_image`, and
deliberate. If the commit fails, the row survives pointing at nothing, which
`_rescan_videos` reports under `videos_missing` and the user can retry. Committing first
and then failing the unlink would leave an orphan in `videos/` that the next rescan
silently re-registers, undoing the delete.

## Locked files: the 409

Both mutations go through `utils.unlink_retrying` / `rename_retrying` rather than
`Path.unlink` / `Path.rename`. On Windows a file another handle holds open cannot be
unlinked or renamed at all, and the handle is usually **this app's own**: `FileResponse`
keeps the file open for the whole body send, and a browser under `preload="metadata"` holds
its range request open to resume a seek. The helpers retry briefly and then raise
`FileInUseError`, which `backend/main.py` turns into a **409** naming the actionable step.
POSIX unlinks an open file happily, so no test here reaches the failing branch without the
monkeypatch in `test_video_locked_files_http.py`; the frontend half — unmounting the
`<video>` before the request goes out — is in `docs/dev/video-ui.md`, and the whole class is
`docs/dev/postmortems/PM-021-served-file-handle-blocked-windows-delete.md`.

The two columns part company here. A locked `file_path` propagates, and because that
happens before `db.delete`, a 409 leaves row and file both intact. A locked `poster_path` is
logged and the delete continues — a poster is never a gate, and 409-ing on it would abandon
a video file that is already gone, leaving exactly the `videos_missing` row the ordering
above exists to avoid. `rename_video`'s 404-on-`FileNotFoundError` branch is unaffected: the
helpers retry a *locked* failure only and re-raise every other `OSError` untouched.

## Path containment

`file_path` and `poster_path` are separate columns and each is gated separately by
`utils.within_datasets_dir` before it is unlinked, on the *resolved* path the guard hands
back. A path resolving outside `settings.datasets_dir` is skipped and logged rather than
refused — the row still goes, because an undeletable row the user can see is the worse
failure. `PATCH /{id}/rename` is the deliberate exception: it **403s** on such a
`file_path`, since renaming a row whose file the app may not touch is meaningless and the
rename would be a move of an arbitrary file; an out-of-tree `poster_path` there is dropped
from the row instead of moved (a poster is never a gate, and `GET /{id}/poster` cuts a
fresh one on the next view). `test_path_containment_http.py` pins both directions.
