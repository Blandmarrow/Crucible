# PM-021: a file the app was still serving could not be deleted or renamed on Windows

### Symptom

On the Windows install, deleting a video from `VideoDetailPage` returned **500**:

```
PermissionError: [WinError 32] The process cannot access the file because it is
being used by another process: 'D:\\...\\datasets\\ds\\videos\\clip.mp4'
```

raised from `delete_video`'s `safe.unlink(missing_ok=True)`. The row survived, the file
survived, and the user got a generic "Delete failed" toast with no way to act on it.
Reproducible whenever the video had been played, or merely opened — the page mounts a
`<video>` on arrival. `PATCH /videos/{id}/rename` had the identical exposure through
`old_path.rename(new_path)` (`MoveFileEx`), and would have committed a row naming a file
that had not moved had the ordering been the other way round.

Nothing in CI, the test suite or the dev container ever saw it.

### Root cause

The handle blocking the delete was **Crucible's own**, held by the request the page had
issued to Crucible seconds earlier:

- `GET /videos/{id}/file` returns a Starlette `FileResponse`, which holds the file open
  (`async with await anyio.open_file(...)`) for the entire body send.
- Under `preload="metadata"` a browser stops consuming the body once it has the header, and
  Firefox keeps the connection open so it can resume a seek from it. The response therefore
  never finishes, and the handle never closes.
- The `<video>` element stayed mounted while the delete confirm was open and while the
  mutation fired, so nothing had asked the browser to abort that request.

Windows then refuses: `os.unlink` and `MoveFileEx` fail with `ERROR_SHARING_VIOLATION` (32)
unless every open handle was opened with `FILE_SHARE_DELETE`, and Python's `open()` does not
request it. POSIX unlinks the directory entry regardless of open handles and lets the last
close free the inode — which is why the dev container, CI and every existing test pass on
code that cannot work on the platform most users run.

The backend's own decoders were investigated and cleared: every `cv2.VideoCapture` is
released in a `finally`, and the ffmpeg frame generator is wrapped in `contextlib.closing`.

### Generalizable rule

**Flag any code that deletes, renames, moves or overwrites a file the app also serves
bytes for.** Two things are needed and neither is sufficient alone:

1. The mutation goes through `utils.unlink_retrying` / `rename_retrying`, so a lock that is
   merely a socket-teardown race clears within the backoff and anything longer becomes a
   409 the user can act on — never a 500.
2. **The client releases the file first.** Unmount the `<video>`/`<img>`/iframe that is
   holding the request; do not merely clear `src`, which leaves the element attached and
   fires a spurious `error` event. A retry alone cannot help while the element is still
   holding the connection open.

The corollary for reviewers: **"the tests pass" says nothing about file-locking behaviour**,
because the whole failure class is invisible on POSIX. Any new destructive filesystem path
has to be reasoned about for Windows explicitly. The class is not video-specific — image
delete, `POST /filesystem/delete` and `POST /filesystem/rename` all serve bytes for the
paths they mutate and have the same exposure; only the video routes are converted so far.

### Why it wasn't caught the first time

Three reinforcing gaps. The dev container and all three CI jobs are Linux, so the branch
does not exist there. The suite drives real files through real routes, which normally finds
this kind of bug — but a POSIX unlink of an open file simply succeeds, so a green suite was
positive evidence for the wrong thing. And the containment/ordering sweeps that repeatedly
audited these very call sites (PM-013, PM-014) asked "is the path inside the tree?" and "is
anything fallible before the commit?", never "can this file be locked right now?".

### Fix

- `backend/utils.py`: `FileInUseError`, `unlink_retrying`, `rename_retrying` — a locked
  failure (`winerror` 32/33, `errno` EACCES/EBUSY) retries on a 0.05/0.1/0.2/0.4 s
  `asyncio.sleep` backoff; every other `OSError` propagates untouched.
- `backend/main.py`: an app-level handler mapping `FileInUseError` → **409** with a detail
  naming the actionable step, so the `filesystem.py` sites inherit it when converted.
- `backend/routers/videos.py`: both mutations converted. `delete_video` splits the two
  columns — a locked `file_path` propagates (before `db.delete`, so row and file both
  survive), a locked `poster_path` is logged and the delete continues, because a poster is
  never a gate and 409-ing there would abandon a video file already gone.
- `VideoDetailPage` unmounts the player while a delete confirm or rename is open, showing
  the poster in its place; `FileBrowserPage`'s `PreviewPanel` does the same whenever a modal
  is open.
- `backend/tests/test_video_locked_files_http.py` monkeypatches `Path.unlink`/`Path.rename`
  to raise `PermissionError` with `winerror = 32` — the only way the branch is reachable
  here — and pins the 409, the surviving row, the retry count, the transient-lock success
  and the poster carve-out.

### Status & date

MITIGATED — the video routes are converted; `routers/images.py` and `routers/filesystem.py`
still call `Path.unlink`/`shutil.move` directly and carry the same exposure.
Last reviewed for staleness: 2026-08-01.
