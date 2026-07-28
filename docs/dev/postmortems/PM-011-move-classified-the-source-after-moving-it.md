# PM-011: `/filesystem/move` classified the source *after* moving it

### Symptom

Moving any media file in the file browser left its DB row pointing at the old path.
`POST /api/v1/filesystem/move` returned `200 {"new_path": …}`, the file really was at the
destination, and `Image.file_path` / `Video.file_path` / `filename` / `dataset_id` were all
unchanged. A file moved into another dataset's folder therefore stayed listed under the
source dataset and served a 404 from `GET /images/{id}/file`; a rescan of the destination
then re-registered the same bytes as a second row.

Both branches of the sync were affected — the single-file one and the directory one that
rewrites every `Image` and `Video` path under a moved folder — so the entire DB-sync block
(`backend/routers/filesystem.py`, ~25 lines) was dead code.

### Root cause

The branch predicate was evaluated after the operation that invalidates it:

```python
shutil.move(str(src), str(new_path))
...
kind = media_kind_for(src.suffix) if src.is_file() else None   # src no longer exists
if kind is not None:  ...
elif src.is_dir():                                              # also False
```

`src` is gone by the time it is asked what it is, so `is_file()` and `is_dir()` both answer
False and control falls off the end of the `if/elif`. Nothing raised, because "no media row
matched" and "we never looked" are indistinguishable at that point — the block's success
path is silent by design.

### Generalizable rule

Flag any predicate about a filesystem path that is evaluated **after** an operation that
moves, renames or deletes that path. `Path.exists()`, `.is_file()`, `.is_dir()`, `.stat()`
and `.suffix`-plus-existence checks are all only meaningful before the mutation; capture
what you need into locals first. The same shape reads fine in review precisely because the
`shutil.move` between the two lines does not look like it consumes its argument.

More generally: an `if/elif` with no `else` over a set of cases the author believed
exhaustive is where a silently-false predicate hides. Where the block's whole purpose is a
side effect, "matched nothing" and "was never reached" look identical.

### Why it wasn't caught the first time

`/filesystem` had tests only for its 409 collision guards, which return before
`shutil.move` runs and so never reach the sync. There was no test of the *success* path in
either branch, for either model — the endpoint's only behavioural assertion was about what
it refuses to do. The bug was found while writing
`test_moving_a_video_between_datasets_rewrites_its_row`, which was planned as a
record-current-behaviour test for a *different* gap (see below) and failed on its first
assertion instead.

### Fix

Classify before the move — `src_is_file` / `src_is_dir` captured into locals immediately
after the 409 guard — plus two tests in `backend/tests/test_conflict_paths_http.py`
covering the file branch (cross-dataset, asserting `file_path`, `filename` and
`dataset_id`) and the directory branch (prefix rewrite).

Still open, and recorded rather than fixed: the file branch does not rewrite
`Video.poster_path`, so a cross-dataset move leaves the poster pointing into the source
dataset's `videos/thumbnails/`. The test pins that as current behaviour.

### Status & date

MITIGATED. Last reviewed for staleness: 2026-07-28.
