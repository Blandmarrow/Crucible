# PM-014: `/filesystem/delete` bypassed the versioning deletion hook

### Symptom

Deleting a folder in the File Browser destroyed images a snapshot still referenced;
restore could not materialize them. `POST /filesystem/delete` removed `{ds}/images` (or a
single image file), the rows went with it, and a snapshot taken *before* that delete still
listed every image — but the object store held no bytes for them, so a restore reported
them unavailable and the pictures were gone for good. Deleting the same images from the
gallery was safe. Found in the code review of the video-support branch (V-22), not in
production.

### Root cause

The versioning deletion hook (`mark_image_deleted_in_versions`) fires from every delete in
`routers/images.py`, from `routers/quality.py`'s duplicate resolution, and from restore's
`handle_extra_images="remove"` — but not from `routers/filesystem.py`, which deletes
registered media just as thoroughly. The endpoint issued a Core
`delete(Image).where(Image.file_path.startswith(prefix))` and committed.

The hook audit was scoped **by router rather than by the table it writes**.
`/filesystem/delete` *is* a delete endpoint; it simply is not in the router whose name says
so. This is PM-003's shape one step further out: there the taxonomy was keyed on the DB
verb (a move is not a delete), here on the file the code lives in.

### Generalizable rule

Flag any statement that removes an `Image` row or unlinks an image file, **wherever it
lives**, that does not fire `mark_image_deleted_in_versions` first. Audit the hook's
call sites by asking "which code can delete from this table?" — grep the model and the
file path, not the routers you expect. The same question applies to every other
cross-cutting obligation a delete carries here: `ensure_not_busy`, the orphaned
thumbnail/poster/sidecar unlink, NULLing `Image.source_video_id`, `refresh_stats`.

Ordering has two silent traps once the hook is added. It must run before the **row**
delete, not only before the file operation: the hook does `db.get(Image, image_id)`
internally, and after a staged Core delete that autoflushes, returns `None`, and the hook
no-ops through its `dataset is None` early return — green tests, no backup. And it must run
before the unlink, since `_backup_and_record_hash` early-returns on a file that no longer
exists.

### Why it wasn't caught the first time

`POST /filesystem/delete` had **no test at all**, and the statement itself read as correct:
the Core `delete(Image).where(...)` looked exactly like `batch_delete`'s in
`routers/images.py` — which fires the hook two lines above it. A reader pattern-matching on
the statement rather than on the surrounding block sees a correct-looking site. The hook's
call-site table in `docs/dev/versioning-service.md` was likewise organised by file, so a router
missing from it looked like a router with nothing to say.

### Fix

`delete_path` now runs **gather → guard → hook → stage + flush → filesystem → commit →
epilogue**: it loads the `Image`/`Video` rows before touching anything, calls
`ensure_not_busy` per dataset, fires `mark_image_deleted_in_versions` per image, NULLs the
frame lineage, stages the row deletes and flushes, then does the `rmtree`/`unlink`, commits,
and only afterwards unlinks orphaned thumbnails/posters/sidecars and refreshes stats.
Regression test: `test_delete_backs_the_file_up_to_the_object_store_first` in
`backend/tests/test_filesystem_delete_http.py` (fails on the previous code); the rest of
that module covers the parity work.

### Status & date

MITIGATED. Last reviewed for staleness: 2026-07-29.
