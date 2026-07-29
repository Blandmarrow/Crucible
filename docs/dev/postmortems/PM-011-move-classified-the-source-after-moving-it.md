# PM-011: `/filesystem/move` classified the source *after* moving it

### Symptom

`POST /api/v1/filesystem/move` left the moved file's DB row pointing at the old path. It
returned `200 {"new_path": …}`, the file really was at the destination, and
`Image.file_path` / `Video.file_path` / `filename` / `dataset_id` were all unchanged. A file
moved into another dataset's folder therefore stayed listed under the source dataset and
served a 404 from `GET /images/{id}/file`; a rescan of the destination then re-registered
the same bytes as a second row.

No user reached this through the browser: the endpoint is API-only. `filesystemApi.move`
exists in the frontend client and has no callers — there is no move menu item, drag/drop or
destination picker — so the symptom was reachable only by an API client.

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

The correct shape was already in the same file, 60 lines below: `delete_path` captures
`is_dir` and its directory prefix into locals immediately, *before* the `rmtree`/`unlink`,
for exactly this reason. When one handler in a router gets this right and its neighbour does
not, the neighbour is the bug — check the siblings before deciding the pattern is unusual.

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

Resurrecting the block made a cross-dataset re-home path *live* that had never actually run,
and it had never learned the rules `batch_move_dataset` follows — provenance
materialization, stats refresh, NULLing `Image.source_video_id`, moving the poster or
thumbnail. Two follow-up commits closed that:

- The file branch now **refuses with a 409** to move a registered file outside its own
  dataset, and the row lookup moved before `shutil.move` so the refusal lands with the
  filesystem untouched. The condition is broader than "another dataset" — a registered file
  moved out of the datasets tree entirely 403s from `utils.safe_dataset_path` just the same.
  Files with no DB row still move anywhere.
- The directory branch now prefix-rewrites `Image.thumbnail_path` / `Video.poster_path` when
  the derived path is *inside* the moved tree (a poster is; an image's thumbnail, one level
  up in `{ds}/thumbnails/`, is not).

The record-current-behaviour test became a refusal test, and its `Image` twin plus a
loose-file negative control were added beside it.

### Status & date

MITIGATED. Last reviewed for staleness: 2026-07-28.
