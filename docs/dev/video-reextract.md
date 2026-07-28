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
boundary, wraps the generator in `contextlib.closing` and calls `require_deinterlace`),
targets walked in ascending timestamp order, and each frame released before the next seek —
never a `list[np.ndarray]`, per that module's RSS rule. `WrittenFrame.pick` carries the
index of the target it came from, since the ascending walk reorders the output;
`shot_index` is `-1` because pass 2 knows nothing about shots.

## Target resolution — one function, two callers

`_resolve_reextract_targets` backs both endpoints, so the modal's accounting and the job's
cannot diverge. Per image, in order, each skip carrying a human reason:

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
6. Regenerate the thumbnail from the new file into `img.thumbnail_path`.
7. Row update: `width`, `height`, `file_size_bytes`, `format`, `phash`, `updated_at` (what
   busts `imagesApi.thumbnailUrlVersioned`) and a `reextract` entry appended to
   `processing_history` by list-concat reassignment, never `.append()`.

`done` is the **committed** count — frames a gallery refetch would actually see, the PM-008
invariant — and it must be non-decreasing. Step 4 commits mid-frame, so the pending-row
counter is reset there rather than derived from `rewritten % N`; deriving it would report
`done` low and fire `TopBar`'s high-water mark late. Commits land every
`EXTRACT_COMMIT_EVERY` (25) and disk is re-checked every 100 written, committing first.
A circuit breaker mirroring `EXTRACT_MAX_CONSECUTIVE_FAILURES` aborts a run whose video has
gone unreadable mid-job; frames already rewritten stay, and the job ends `failed`, not
`cancelled`. Cancellation keeps everything already written — the files are real and their
COW backups exist.

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

The one real hazard is a file with **no DB row to guard it** — hand-dropped into `images/`
and not yet rescanned. That is refused: the temp is unlinked, the frame counted failed, and
the original left untouched.

**The same gap existed in LUT replace mode and is now closed.** `apply_lut_sync` calls
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
from `result_data`. Job tracking uses `SelectionToolbar`'s `detectJobIds` array shape — one
job per video, so several ids.

Three entry points, all opening that one form:

- **`SelectionToolbar`** — rendered unconditionally like the other thirteen actions rather
  than gated on lineage. The store holds ids only (`selectedIds` + `datasetByImageId`) and a
  selection can span pages and datasets, so any client-side lineage gate would be wrong for
  exactly the selections that matter; the preview endpoint does the honest accounting
  instead. The flag joins `anyModalOpen` so the Delete-key handler stays suppressed.
- **`ImageDetailPage`** — a `re-extract` button on the existing lineage row, scoped to that
  one image.
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
round-trip, the unregistered-file refusal, a temp that will not re-open leaving the original
intact, cancel, the 507 and 503 gates, in-flight dedupe in both directions, route shadowing
and the SSE invariants. `backend/tests/test_lut_replace_extension_http.py` covers the LUT
half. `frontend/e2e/video-extract.spec.ts` opens the form from the gallery toolbar and
asserts the preview accounting; CI has no `scenedetect`, so no lineage-carrying frame exists
there and the run is deliberately never submitted.
