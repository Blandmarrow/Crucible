# Pass 2: full-resolution re-extraction

The second half of the two-pass design. Pass 1 (`docs/dev/video/extract.md`) writes cheap
1024px triage JPEGs so a 900-shot episode can be scored, deduped and eyeballed quickly;
pass 2 turns the frames that *survived* curation into training data at native resolution.
This file is how a frame is rewritten in place: `video_extract.render_at_timestamps` decodes
and the `video_reextract` job swaps. Which frames a run touches, and what it promises — the
contract and `_resolve_reextract_targets` — is `docs/dev/video/reextract-endpoints.md`; the
UI half — `ReextractFramesForm`/`ReextractFramesModal`, its three entry points, the job
adoption and the `TopBar` invalidations — is `docs/dev/video/reextract-ui.md`, mirroring how
`docs/dev/video/extract-ui.md` stands to `docs/dev/video/extract.md`; the tests are in
`docs/dev/video/tests.md` § Re-extraction.

Nothing here needs `scenedetect`. The pick already happened in pass 1, and pass 2 never
re-detects shots, never re-picks a frame, and never touches `_shot_windows`,
`_candidate_positions`, `sharpness`, `pick_index` or `is_degenerate`. No new columns, so no
migration and no new mirror sites.

## The shared write half

`render_shot`'s crop → resize → save → thumbnail tail is `video_extract._write_frame`,
shared by both passes so they cannot drift on format or quality. The format comes from the
**suffix**, through `utils.normalize_image_format` + `image_save_kwargs` —
`image_save_kwargs("JPEG")` is `{quality: 95, subsampling: 0}`, so pass 1's output is
unchanged bit for bit. It returns the path actually written, which differs from the one
asked for when the format falls back to PNG.

`render_at_timestamps` is the pass-2 decoder beside it: one `read_positions` call per
target (which already applies `apply_orientation`, normalizes ffmpeg's RGB→BGR at one
boundary, wraps the generator in `contextlib.closing` and calls `require_deinterlace`), and
each frame released before the next seek — never a `list[np.ndarray]`, per that module's
RSS rule. `WrittenFrame.pick` carries the index of the target it came from, since the
ascending order reorders the output; `shot_index` is `-1` because pass 2 knows nothing
about shots.

**Each target gets its own decoder open** — every `read_positions` call opens and releases
its own `cv2.VideoCapture`, or spawns its own ffmpeg subprocess on the deinterlace path.
There is no shared forward walk through the file; the ascending order buys page-cache
locality on the container, nothing more. The job compounds it deliberately by passing one
timestamp per call: the per-frame structure is what buys the cancel check, the COW
protect+commit, the "does it re-open" verification and the progress event, and batching
would mean N temp files coexisting against a disk preflight that budgets for one.

## The `video_reextract` job

One job per video, which is what lets each label name its video
(reading `Re-extract: {stem} — 50 frames at full res`, from
`f"Re-extract: {Path(v.filename).stem[:60]} — {n} frame{'' if n == 1 else 's'} at full res"`
— it pluralises, so no label contains the literal `frame(s)`; the `[:60]` matters, `label`
is a `String(200)`). `_make_reextract_runner` copies `_make_extract_runner`'s `_run_with_stats`
wrapper verbatim, including its three load-bearing details — see
`docs/dev/video/extract.md` § The `video_extract` job.

**Per frame, and this ordering is better than the upscale path's on purpose:**

1. `job_queue.cancel_requested` check.
2. Decode + `_write_frame` into `{stem}.{uuid4().hex}.tmp{suffix}` beside the image.
3. `get_image_info(tmp)`. `{}` means the file will not re-open — unlink the temp, count
   failed, carry on. **The original is still intact**, unlike upscale and LUT, which
   overwrite first and discover afterwards.
4. The live `processing_history` re-check (`docs/dev/video/reextract-endpoints.md`
   § Target resolution — one function, two callers, at *The ladder runs twice*), then
   `version_service.protect_file_before_overwrite` and an immediate `commit()` — mandatory
   per that function's docstring, and the eighth *router* call site
   (`docs/dev/versioning/hooks.md`).
5. `utils.replace_retrying` into place (below).
6. Row update: `filename`/`file_path` if the suffix changed, plus `width`, `height`,
   `file_size_bytes`, `format`, `phash`, `updated_at` (what busts
   `imagesApi.thumbnailUrlVersioned`) and a `reextract` entry appended to
   `processing_history` by list-concat reassignment, never `.append()`.
7. `commit()`. From here the row and the file on disk agree, durably.
8. **Best-effort epilogue**: unlink the superseded original (extension change only), then
   regenerate the thumbnail from the new file into `img.thumbnail_path` **when the row has
   one** — the whole regeneration sits inside `if img.thumbnail_path:`, so a row that lost
   its thumbnail path gets neither a thumbnail nor a `thumbnails_stale` count. Both are wrapped;
   a failure is logged and cannot change the frame's outcome, and a failed thumbnail
   increments `counts["thumbnails_stale"]` so `result_data` still records it. All five jobs
   with an epilogue of this shape report that count — `batch_lut`, `batch_upscale`,
   `crop_upscale` and `crop_to_detection` as well — and `TopBar` turns it into a warning
   naming the repair; see
   `docs/dev/frontend-jobs.md` § The stale-thumbnail warning, and the ordering rule behind it.

Steps 6 and 7 used to be the other way round, with the thumbnail between them. That is
PM-013: `generate_thumbnail` catches nothing, so an unwritable `thumbnails/` raised out of
the job, the row rolled back, and the frame was left named `.jpg` on a `.png` that had
already been written — `GET /images/{id}/file` 404s and a later rescan adopts the orphan.
**Nothing fallible may sit between the replace and the commit that describes it** —
CLAUDE.md § Key invariants, with the incident and the other three sites it was fixed at in
`docs/dev/postmortems/PM-013-fs-mutation-before-the-commit.md`. The
epilogue is safe to read `img` after the commit only because `AsyncSessionLocal` sets
`expire_on_commit=False`.

Steps 2–8 sit inside a `try`/`finally` that unlinks the temp. The name carries a real image
extension and sits in `images/`, so a survivor is a file `rescan_dataset` would adopt as a
new image. The `finally` reads the **live** `tmp` binding — it is rebound to the written
path after the render, because `_write_frame` may have fallen back to PNG — and
`missing_ok=True` makes it a no-op once the replace has consumed it. That unlink is itself
wrapped in `try`/`except OSError`: a raise from a `finally` would discard the frame's return
value and kill the job, which is the outcome the outcome-tuple exists to prevent, and the
temp was written milliseconds ago — exactly when an AV scanner is holding it. Plain
`try`/`except` rather than `unlink_retrying`, because awaiting inside a `finally` during a
`CancelledError` unwind loses the cleanup altogether. A `SIGKILL` is still
outside what a `finally` covers, and so is a failing step 7 — the one window that cannot be
ordered away. Both leave the same residue and only that: an orphan file at the new suffix,
with the row and the original still intact (see § The extension change). Accepted, not
claimed fixed.

**`done` is the committed count** — frames a gallery refetch would actually see, the PM-008
invariant — and it must be non-decreasing. `_rewrite` commits each frame itself at step 7,
so `done` is `counts["rewritten"]` with no pending-row correction. The loop keeps its own
`commit()` immediately before the progress event even though it is now a no-op on every
path: it is where the invariant is provable, three lines above the emit. Batching it was
never an option anyway — step 4 *has* to commit mid-frame for the COW hook, so any
`EXTRACT_COMMIT_EVERY`-style threshold would never be reached and `done` would lag one frame
behind for the whole run, topping the bar out at `N-1 / N` until the terminal event.
`EXTRACT_COMMIT_EVERY` plays no part here — it belongs to pass 1. Disk is re-checked every
`EXTRACT_DISK_RECHECK_EVERY` (100) written, after the emit, so the frame that filled the
disk still publishes its progress event before `require_free_space` raises out of the job.

A circuit breaker mirroring `EXTRACT_MAX_CONSECUTIVE_FAILURES` aborts a run whose video has
gone unreadable mid-job; frames already rewritten stay, and the job ends `failed`, not
`cancelled`. `_rewrite` returns `(reason, is_fault)` rather than a bare reason so the
breaker can tell a decode fault from a refusal — see § The extension change. **`is_fault` is
a prediction that the next frame will fail the same way, not a severity or readability
claim**: it belongs to a condition that will still hold a millisecond later. `False` is for a
refusal specific to *this* frame. Cancellation
keeps everything already written: the files are real and their COW backups exist.

## The extension change

When `format` matches the frame's current suffix — the common case — step 5 is just
`replace_retrying(tmp, img.file_path)`: atomic, no rename, nothing derived moves. It is
`replace_retrying` and **not** `rename_retrying`, and the distinction is load-bearing rather
than stylistic: `target == src_path` on every same-format run, and `Path.rename` is
`MoveFileW` without `REPLACE_EXISTING`, so it raises `FileExistsError` on Windows while
clobbering silently on POSIX — the swap would pass CI and break every same-suffix
re-extraction on the one platform PM-021 is about.

The retry is there because `GET /images/{id}/file` serves this exact path, so an open detail
pane is a Windows lock on the file being overwritten. A surviving lock is caught as
`FileInUseError` — narrowly, since a broad `except OSError` would report `ENOSPC` or `EROFS`
as "open in another program" on every frame — and returns `("failed", …, is_fault=True)`.
True rather than False because the breaker is 10 *consecutive* failures, so one locked frame
among good ones can never abort a run, while with `False` a systemic lock would pay a
full-res decode *and* a whole-file copy into `.versions/objects/` per frame for zero
rewrites and end "completed, 0 rewritten". The failed state needs no rollback: the COW hook
recorded the hash of the original, which is still on disk untouched, so a re-run's
`file_hash IS NULL` probe skips the backup and `_edited_in_place` still returns False.

When the suffix differs (`clip_s0001_00.jpg` → `.png`), **the stem is unchanged, and that is
what makes this cheap.** Three things are derived from an image's filename:

| Derived from filename | Effect of a pure extension change |
|---|---|
| thumbnail `{stem}.webp` (`utils.thumbnail_path_for`) | **unchanged** — regenerated in place |
| caption sidecar `{stem}.txt` | **unchanged** — no `rename_with_sidecar` |
| `UniqueConstraint("dataset_id", "filename")` | provably free — see below |

The name is provably free because of a stronger invariant the codebase already maintains:
**within a dataset, no two images share a stem, in any extension.** Every image-name-picking
site goes through `unique_filename_with_thumb`, which rejects a candidate whose *stem* is
occupied — including the two rescan paths that adopt names off disk rather than picking them
(`docs/dev/image-files.md` § A stem-keyed derived artifact needs a collision guard at every writer). So if this frame owns `clip_s0001_00.jpg`, no other row can
own `clip_s0001_00.png`. No `unique_filename_with_thumb` call is needed here, no
`disk_exclude`, and no exception to the `occupied_thumb_stems` rule.

**The superseded original is unlinked in step 8, after the commit**, not alongside the
rename. `Path.unlink` is fallible in its own right (`EACCES`/`EROFS`, and `PermissionError`
on Windows if any process holds the file open — `GET /images/{id}/file` streams that exact
path while the job runs, and the job holds no `busy` lock), so unlinking before the commit
would put a fallible call inside the forbidden window. Ordering it after also makes a
failing commit survivable: the row still names the `.jpg`, the `.jpg` still exists, `/file`
still 200s, and the residue is an orphan `.png`. It stays a plain `unlink`, one of the three
sites deliberately exempt from the retrying helpers (named in CLAUDE.md): by the time it
runs the row already names a file that exists, so a lock costs only an orphan a rescan can
adopt and there is nothing a retry could save. The two files coexist briefly, which the
endpoint's disk preflight (`docs/dev/video/reextract-endpoints.md` § Target resolution — one
function, two callers) already budgets for — it estimates `× 2.0` for PNG precisely
because a second copy of each frame is live during the swap.

Rejected alternative: nulling `thumbnail_path` when regeneration fails, to force it on
demand. A deterministic failure (a full disk, a read-only `thumbnails/`) would then 500 the
thumbnail request path on every gallery scroll instead of showing one stale tile.

The one real hazard is a file with **no DB row to guard it** — hand-dropped into `images/`
and not yet rescanned. That is refused: the temp is unlinked, the frame counted failed, and
the original left untouched. The refusal is **exempt from the circuit breaker** — it is a
name collision, not a decode fault, and the video is demonstrably readable — so it returns
`is_fault=False` and a directory of squatters reports every frame instead of aborting after
ten. It still counts as `failed`: the user asked for that frame and did not get it.

**The same gap existed in LUT replace mode and is now closed** (`docs/dev/postmortems/PM-009`). `apply_lut_sync` calls
`normalize_image_format`, which falls back to PNG for `.gif`/`.bmp`/`.tiff`/`.avif` — all in
`IMAGE_EXTENSIONS` — so a replace-mode grade of one of those writes a different file; the
row used to keep pointing at the stale original, which was also left on disk.
`routers/lut.py`'s replace branch now follows the written path, and its collision guard runs
**before** the save rather than after: by the time `apply_lut_sync` returns, an unregistered
file at the fallback path is already gone. See `docs/dev/image-processing-models.md` § LUT Color Grading.
