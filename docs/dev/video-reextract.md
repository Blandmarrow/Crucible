# Pass 2: full-resolution re-extraction

The second half of the two-pass design. Pass 1 (`docs/dev/video-extract.md`) writes cheap
1024px triage JPEGs so a 900-shot episode can be scored, deduped and eyeballed quickly;
pass 2 turns the frames that *survived* curation into training data at native resolution.
`POST /videos/reextract` and `/videos/reextract/preview` in `routers/videos.py` drive it,
`video_extract.render_at_timestamps` decodes for it, and
`components/video/ReextractFramesForm.tsx` is the one form behind all three entry points.

Nothing here needs `scenedetect`. The pick already happened in pass 1, and pass 2 never
re-detects shots, never re-picks a frame, and never touches `_shot_windows`,
`_candidate_positions`, `sharpness`, `pick_index` or `is_degenerate`. No new columns, so no
migration and no new mirror sites.

## The contract

**The timestamp is the artifact.** `Image.source_timestamp_ms` is authoritative: pass 2
re-seeks it rather than upscaling the triage JPEG, which is why `backend/tests/
test_video_reextract.py::test_re_seeking_a_recorded_timestamp_lands_on_the_same_moment`
is the test to keep if only one survives review. Off-by-one-keyframe seeking silently
yields the *wrong frame* — every count the job reports still says success.

**Geometry replays verbatim.** `Video.crop_x/y/w/h` and `Video.deinterlace` hold the
*normalized* values the extract endpoint stored (`docs/dev/video-extract.md` § The
endpoints), so pass 2 applies them as-is. Trims are irrelevant to a direct seek.

**Quality scores are left alone**, matching `batch_upscale`/`batch_lut` replace mode. The
job says so through `result_data["note"]`, which the completion toast repeats — silence
about stale scores would be the misleading choice. `phash` *is* re-derived: dedup depends
on it. It is scale-invariant, so the value often does not change, which is why the test
poisons it first rather than asserting it moved.

**Output format is the user's.** JPEG (default) or PNG for a lossless capture; resolution
is native by default (`long_edge=0`, which `render_shot` already read as "no downscale")
with an optional `max_long_edge` cap.

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

## Target resolution — one function, two callers

`_resolve_reextract_targets` backs both endpoints, so the modal's accounting and the job's
cannot diverge. Exactly one scope: `image_ids` for a gallery selection (which can span videos
and datasets) or `video_id`, optionally narrowed by `subfolder`.

Three rules apply to the scope itself, all inside `_reextract_rows` so preview and enqueue
refuse identically:

- **An unknown `video_id` is a 404**, not an empty result. An empty result means "this video
  has no eligible frames" — a real answer the modal renders — and returning it for a video
  that does not exist makes the two indistinguishable. It is raised *before* the busy guard,
  preserving the ordering `test_a_reextract_with_nothing_to_do_does_not_409` pins.
- **Both scopes are capped at `REEXTRACT_MAX_FRAMES` (5000)** — the schema's `max_length` for
  `image_ids`, an explicit 400 naming the count for `video_id`, which resolves its ids from
  the DB. The constant lives in `schemas/video.py` and the router imports it, so the two
  cannot drift (and a schema must never import from a router). It bounds three things at
  once: the `BackgroundJob.config` id blob, the job's `select(Image)` entity load, and the
  preview's `skipped` array.
- **`image_ids` is deduped** with `dict.fromkeys` before anything counts it. The `IN` query
  collapses duplicates anyway, so an id sent twice reported `eligible=2` against one row: a
  phantom entry in `cfg`, a phantom skip, and a bar that topped out at `1 / 2`.

Per image, in order, each skip carrying a human reason:

| Condition | Reason |
|---|---|
| row missing | `"no longer exists"` |
| `source_video_id is None` | `"not extracted from a video"` |
| `source_timestamp_ms is None` | `"no recorded timestamp"` |
| `Video` row missing / different dataset | `"source video is gone"` |
| video file absent on disk | `"source video file is missing"` |
| `processing_history` holds any op **other than** `reextract` | `"already edited in place"` |

That last rung is `backend/models/image.py`'s mandate: the **replace** mode of
crop/upscale/LUT/detection-crop mutates a row in place, so a frame keeps its lineage while
the pixels stop being the extracted frame. The `reextract` exclusion is load-bearing —
without it pass 2 refuses to run a second time, because its own history entry looks like
third-party editing. Anything that is not a dict counts as an unknown edit and skips.

**The guard is only as good as what writes `processing_history`.** Upscale, LUT and
detection-crop always recorded an entry; the five in-place paths in `routers/images.py` —
`resize`, replace-mode `crop`, the `crop_upscale` replace job, `batch_resize` and
`batch_crop` — did not, so a frame cropped in place stayed silently eligible and pass 2
would have discarded the crop (`docs/dev/postmortems/PM-010`). They now all go through
`images._record_in_place(img, op,
**params)`, which is the single writer: list-concat reassignment, never `.append()`
(CLAUDE.md § Key invariants), plus the `updated_at` bump. Add a call there in any future
path that overwrites an image file. (`POST /images/batch/crop` and `/batch/resize` are
unreachable today — `POST /images/{image_id}/crop` is declared first and reads `batch` as an
image id, and nothing in the frontend calls either — so their entries are written but
untestable over HTTP.)

**In-flight dedupe lives in the resolver**, not the enqueue path, so the preview is honest
about it too — and it now runs in **both directions**: `_videos_with_running_extractions`
matches `video_extract` *and* `video_reextract`, because a pass-1 `replace` deletes the very
rows pass 2 is rewriting. `extract_frames` reads the same helper.

The endpoint alone adds `ensure_not_busy` per dataset (the job does not hold `busy`,
following `batch_upscale`), the deinterlace 503 gate on the *stored* filter — there is no
request field to override it, and without the gate the job dies inside every frame and
reports a missing package as a decode fault — and a disk preflight estimating
`cropped_w × cropped_h × 0.5` for JPEG or `× 2.0` for PNG, conservative because a temp file
coexists with the original during each swap.

## The `video_reextract` job

One job per video, which is what lets each label name its video
(`f"Re-extract: {stem[:60]} — {n} frame(s) at full res"`; the `[:60]` matters, `label` is a
`String(200)`). `_make_reextract_runner` copies `_make_extract_runner`'s `_run_with_stats`
wrapper verbatim, including its three load-bearing details — see
`docs/dev/video-extract.md` § The `video_extract` job.

**Per frame, and this ordering is better than the upscale path's on purpose:**

1. `job_queue.cancel_requested` check.
2. Decode + `_write_frame` into `{stem}.{uuid4().hex}.tmp{suffix}` beside the image.
3. `get_image_info(tmp)`. `{}` means the file will not re-open — unlink the temp, count
   failed, carry on. **The original is still intact**, unlike upscale and LUT, which
   overwrite first and discover afterwards.
4. `version_service.protect_file_before_overwrite` then an immediate `commit()` — mandatory
   per that function's docstring, and the 8th call site (`docs/dev/versioning.md`).
5. `os.replace` into place (below).
6. Row update: `filename`/`file_path` if the suffix changed, plus `width`, `height`,
   `file_size_bytes`, `format`, `phash`, `updated_at` (what busts
   `imagesApi.thumbnailUrlVersioned`) and a `reextract` entry appended to
   `processing_history` by list-concat reassignment, never `.append()`.
7. `commit()`. From here the row and the file on disk agree, durably.
8. **Best-effort epilogue**: unlink the superseded original (extension change only), then
   regenerate the thumbnail from the new file into `img.thumbnail_path`. Both are wrapped;
   a failure is logged and cannot change the frame's outcome, and a failed thumbnail
   increments `counts["thumbnails_stale"]` so `result_data` still records it.

Steps 6 and 7 used to be the other way round, with the thumbnail between them. That is
PM-013: `generate_thumbnail` catches nothing, so an unwritable `thumbnails/` raised out of
the job, the row rolled back, and the frame was left named `.jpg` on a `.png` that had
already been written — `GET /images/{id}/file` 404s and a later rescan adopts the orphan.
**Nothing fallible may sit between the `os.replace` and the commit that describes it** —
CLAUDE.md § Key invariants, with the incident and the other three sites it was fixed at in
`docs/dev/postmortems/PM-013-fs-mutation-before-the-commit.md`. The
epilogue is safe to read `img` after the commit only because `AsyncSessionLocal` sets
`expire_on_commit=False`.

Steps 2–8 sit inside a `try`/`finally` that unlinks the temp. The name carries a real image
extension and sits in `images/`, so a survivor is a file `rescan_dataset` would adopt as a
new image. The `finally` reads the **live** `tmp` binding — it is rebound to the written
path after the render, because `_write_frame` may have fallen back to PNG — and
`missing_ok=True` makes it a no-op once `os.replace` has consumed it. A `SIGKILL` is still
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
breaker can tell a decode fault from a refusal — see § The extension change. Cancellation
keeps everything already written: the files are real and their COW backups exist.

## The extension change

When `format` matches the frame's current suffix — the common case — step 5 is just
`os.replace(tmp, img.file_path)`: atomic, no rename, nothing derived moves.

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
(CLAUDE.md § Key invariants). So if this frame owns `clip_s0001_00.jpg`, no other row can
own `clip_s0001_00.png`. No `unique_filename_with_thumb` call is needed here, no
`disk_exclude`, and no exception to the `occupied_thumb_stems` rule.

**The superseded original is unlinked in step 8, after the commit**, not alongside the
rename. `Path.unlink` is fallible in its own right (`EACCES`/`EROFS`, and `PermissionError`
on Windows if any process holds the file open — `GET /images/{id}/file` streams that exact
path while the job runs, and the job holds no `busy` lock), so unlinking before the commit
would put a fallible call inside the forbidden window. Ordering it after also makes a
failing commit survivable: the row still names the `.jpg`, the `.jpg` still exists, `/file`
still 200s, and the residue is an orphan `.png`. The two files coexist briefly, which the
endpoint's disk preflight already budgets for — it estimates `× 2.0` for PNG precisely
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
file at the fallback path is already gone. See `docs/dev/ml-models.md` § LUT grading.

## Frontend

`ReextractFramesForm` follows `UpscaleForm`/`CropToDetectionForm` — props
`{ datasetId, imageIds?, videoId?, subfolder?, onSuccess?, onCancel? }`, owning its own API
call, job ids and invalidation. On mount it calls the preview endpoint and renders the
accounting grouped by reason, so 300 identical skips read as one line. Controls: a
JPEG/PNG radio, an optional max long edge (empty = native) and an optional job label. The
stale-scores note sits above the submit button and is repeated in the completion toast built
from `result_data`. Max long edge is validated client-side against the server's `ge=64,
le=16384` (empty stays valid, meaning native): submit is disabled with the bound shown
inline, rather than letting `30` reach the API and return a raw 422 toast. Job tracking uses
`SelectionToolbar`'s `detectJobIds` array shape — one job per video, so several ids.

`ReextractFramesModal` wraps it for all three entry points: `useModalBehavior` (Escape, Tab
cycling, focus return, `role="dialog"`), the overlay and `.card` panel, a `title` and an
optional `headerExtra` slot — `SelectionToolbar`'s dataset breakdown, `VideoDetailPage`'s
`{filename} · {subfolder}` line. A component rather than a hook call per page because the
hook must not be called conditionally and every entry point renders behind a flag;
`useModalBehavior`'s docstring rules out a *generic* wrapper, so a feature-specific one is
the sanctioned shape. Backdrop-click closing stays off, matching `ExtractFramesModal` — the
sibling on the same page.

Three entry points, all opening that one modal:

- **`SelectionToolbar`** — rendered unconditionally like the other thirteen actions rather
  than gated on lineage. The store holds ids only (`selectedIds` + `datasetByImageId`) and a
  selection can span pages and datasets, so any client-side lineage gate would be wrong for
  exactly the selections that matter; the preview endpoint does the honest accounting
  instead. The flag joins `anyModalOpen` so the Delete-key handler stays suppressed.
- **`ImageDetailPage`** — a `re-extract` button on the existing lineage row, scoped to that
  one image. Its flag joins `showCropDetect` in `formModalOpen`, which suppresses **both**
  window-level key handlers: without it ArrowLeft/Right navigated the page underneath the
  open dialog, and since the form is passed `imageIds={[imageId]}` from the route the
  preview silently re-queried for a different image while the dialog still named the old
  one.
- **`VideoDetailPage`** — a per-row action on the extraction-history panel, scoped by
  `{videoId, subfolder}`, which is the only scope that panel has and the reason the request
  accepts it. `null` is closed and `""` is the dataset root, a real subfolder.

`ExtractFramesModal` and `useVideoExtractJobs` are **not** involved: that hook filters
`job_type === "video_extract"` and exists for a two-step probe modal with a re-attach story
pass 2 does not have. `TopBar` carries `video_reextract` in both `LIVE_IMAGE_JOB_TYPES` and
`IMAGE_MODIFYING_JOB_TYPES`, and invalidates the singular `["image"]` key for it — otherwise
an open detail pane keeps showing the triage dimensions and thumbnail.

## Tests

`backend/tests/test_video_reextract.py` covers the decode half against real mp4 fixtures
(native vs capped resolution, crop replay and clamping, both formats, PNG being
byte-identical to the decoded frame, and the seek-exactness check above).
`backend/tests/test_video_reextract_http.py` covers the endpoints and the job: preview and
enqueue agreeing on the same skip set, the COW hook restoring triage pixels from a
pre-existing snapshot, a second run not self-skipping, the extension swap and its
round-trip, the unregistered-file refusal and a whole run of them completing rather than
tripping the breaker, a temp that will not re-open leaving the original intact, a raise
between the render and the swap leaving no temp behind, cancel, the 507 and 503 gates,
in-flight dedupe in both directions, route shadowing and the SSE invariants (including
`done` reaching `N`). `backend/tests/test_lut_replace_extension_http.py` covers the LUT
half. `frontend/e2e/video-extract.spec.ts` opens the modal from the gallery toolbar and
asserts the preview accounting, the long-edge bound, and that it is a real dialog (Escape
closes, focus returns to the opener); CI has no `scenedetect`, so no lineage-carrying frame
exists there and the run is deliberately never submitted.
