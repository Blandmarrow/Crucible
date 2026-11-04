# Video sources

The family's entry point: the tables below route from a code path to whichever file in
`docs/dev/video/` owns it. It keeps the storage layout and the invariant that videos are
sources, not images; the `Video` model, the three ingest paths, the poster-stem rules, stats
and what the rest of the app gets for free are in `docs/dev/video/storage.md`. The arc's
roadmap was retired into these files when pass 2 landed, the same way the detection arc's
was.

## The family

| File | Covers |
|---|---|
| [storage.md](video/storage.md) | The `Video` model, the three ingest paths, poster-stem collisions and `claimed_poster_stems`, how video bytes reach the dataset stats, what delete, export and versioning get for free |
| [endpoints.md](video/endpoints.md) | The `/videos` router, video rename/delete and range serving — `GET /capabilities`' declaration order and `frames-summary` |
| [decode.md](video/decode.md) | Video probe and metadata, duration, poster frames — the cv2 probe ladder, `isOpened()` as ingest gate, the poster fallback ladder |
| [shots.md](video/shots.md) | Probe sampling, shot detection and frame rendering — the RSS rule, the PySceneDetect contract and cost cliff, `render_shot`/`_write_frame` |
| [heuristics.md](video/heuristics.md) | Cropdetect, interlace/telecine detection, sharpness, and which frame a shot yields — `pick_index` candidate rejection |
| [extract.md](video/extract.md) | The extract/probe endpoints and the `video_extract` job — crop/trim validation, subfolder modes, step order and the replace delete, SSE progress, frame lineage |
| [reextract-endpoints.md](video/reextract-endpoints.md) | Which frames a re-extraction rewrites — `POST /videos/reextract` and its preview, seek-by-timestamp, the `processing_history` skip rule |
| [reextract.md](video/reextract.md) | The `video_reextract` job and a replace-mode extension change — `_rewrite`'s step order, the PM-013 window, the locked-file retry |
| [ui.md](video/ui.md) | Every video browsing screen — `VideoStrip`, `VideoDetailPage`, the extraction history, the frame lineage line and filter |
| [extract-ui.md](video/extract-ui.md) | The extraction modal and its progress rows — the touched-flag guards, re-attaching to a running job |
| [extract-controls.md](video/extract-controls.md) | The crop/trim controls and any bounded numeric input — `CropOverlay`'s mattes and even-snap, `TrimBar`'s tail semantics and arrow keys, `NumberField`'s draft contract |
| [reextract-ui.md](video/reextract-ui.md) | The re-extract dialog, its three entry points, and what a finished `video_reextract` refreshes |
| [tests.md](video/tests.md) | Adding a video test, a cv2-gated test or a CI media dependency — the four `mp4_*` fixtures, branches no fixture reaches, the skip convention |

## Where things live

Video is documented across fourteen files, so the reliable way in is the code file you are
about to open rather than the topic you have in mind — a reader always knows the former.
Each row names the doc that **owns** that code. A `+` clause is a second genuine owner,
not a see-also, and says which part of the file it owns.

### Backend

| Editing… | Read |
|---|---|
| `backend/media_types.py` | This file — the single ingestible-extension allowlist |
| `backend/models/video.py` | `docs/dev/video/storage.md` § The model |
| `backend/routers/images.py` (its video branch) | `docs/dev/video/storage.md` § Ingest — the gallery-upload path, `_write_video_upload_sync` and the `skipped` response |
| `backend/routers/videos.py` | `docs/dev/video/endpoints.md`; + `docs/dev/video/extract.md` for the extract and probe routes; + `docs/dev/video/reextract-endpoints.md` for `POST /videos/reextract`, `POST /videos/reextract/preview` and `_resolve_reextract_targets`; + `docs/dev/video/reextract.md` for the `video_reextract` job (`_run_reextraction`, `_rewrite`) |
| `backend/schemas/video.py` | `docs/dev/video/extract.md` (pass 1 bodies), `docs/dev/video/reextract-endpoints.md` (pass 2 bodies and `REEXTRACT_MAX_FRAMES`); + `docs/dev/video/endpoints.md` for `VideoOut`, `VideoFramesSummary` and `RenameVideoRequest` |
| `backend/services/video_service.py` | `docs/dev/video/decode.md` — the probe ladder, duration search and poster generation; + `docs/dev/video/storage.md` § Poster stems and collisions for `claimed_poster_stems` |
| `backend/services/video_extract.py` | `docs/dev/video/shots.md` — sampling, shot detection, `render_shot`; + `docs/dev/video/extract.md` for the `video_extract` job; + `docs/dev/video/reextract.md` for `render_at_timestamps` |
| `backend/services/video_frames.py` | `docs/dev/video/heuristics.md` — the pure-numpy judgement calls |
| `backend/services/dataset_service.py` (its video half) | `docs/dev/video/storage.md` § Ingest — folder import, `_rescan_videos` and the poster rename-on-collision; + `docs/dev/dataset-operations.md`'s **Dataset duplicate** paragraphs for `duplicate_dataset`'s video loop and `_copy_video_sync` |
| `backend/tests/test_video_*.py` | `docs/dev/video/tests.md` — also the cv2 gate and the skip convention |

### Frontend

| Editing… | Read |
|---|---|
| `frontend/src/api/videos.ts` | `docs/dev/video/endpoints.md` — it is a thin client for the twelve routes and carries no behaviour of its own |
| `frontend/src/components/gallery/VideoStrip.tsx` | `docs/dev/video/ui.md` § VideoStrip |
| `frontend/src/pages/VideoDetailPage.tsx` | `docs/dev/video/ui.md` § VideoDetailPage |
| `frontend/src/components/video/ExtractFramesModal.tsx` | `docs/dev/video/extract-ui.md` § ExtractFramesModal — `ExtractProgressList` lives inline in this file |
| `frontend/src/hooks/useVideoExtractJobs.ts` | `docs/dev/video/extract-ui.md` § Re-attaching to the job |
| `frontend/src/components/video/CropOverlay.tsx` | `docs/dev/video/extract-controls.md` § CropOverlay |
| `frontend/src/components/video/TrimBar.tsx` | `docs/dev/video/extract-controls.md` § TrimBar |
| `frontend/src/components/common/NumberField.tsx` | `docs/dev/video/extract-controls.md` § NumberField for the full draft contract |
| `frontend/src/components/video/ReextractFramesForm.tsx` | `docs/dev/video/reextract-ui.md` § ReextractFramesForm |
| `frontend/src/components/video/ReextractFramesModal.tsx` | `docs/dev/video/reextract-ui.md` § ReextractFramesModal — and § The three entry points |
| `frontend/src/utils/videoPlayback.ts` | `docs/dev/video/ui.md` — the shared playback classifier: `playbackErrorMessage` and `browserPlaybackHint` |
| `frontend/src/components/video/UnplayableOverlay.tsx` | `docs/dev/video/ui.md` — it renders what that classifier returns, over the poster |
| `frontend/e2e/video-extract.spec.ts` | `docs/dev/video/extract-controls.md` § End-to-end coverage |
| `frontend/e2e/video-delete.spec.ts` | `docs/dev/video/ui.md` § VideoStrip — the Delete button, the image-selection precedence rule and the split-view pane guard |

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
`captioning.py`, `comfy.py`, `detection.py`, `inpaint.py`, `videos.py`,
`services/version_service.py`, `dataset_service.py` and `image_edit_job.py` (upscale and LUT) (the count is modules, not call
sites; `routers/images.py` alone holds seven). `routers/videos.py` is in that list because
frame extraction writes `Image` rows: it points `thumb_dir` at the dataset's *images*
thumbnail folder, so it shares the collision domain despite living in the video router.
The tenth is `routers/filesystem.py`'s `rename_path`, added later and easy to miss — its
local is named `occupied`, not `occupied_thumb_stems`, and it consumes the set to **409 a
rename** rather than to uniquify, so its failure mode under a suffix convention would be a
spurious refusal instead of a silent clobber. It still has to learn the filter, which is the
whole point of counting. A suffix convention would require all ten to learn one, and any one
that forgot would be a bug. A separate directory means none of them change.

## Videos are sources, not images, and their posters live apart

**Videos are sources, not images, and their posters live apart.** A video is a `Video` row in `videos`, flat in `{dataset}/videos/`, never a row in `images`. Poster thumbnails go in `{dataset}/videos/thumbnails/` — a separate directory, *not* the images thumbnail folder with a distinguishing suffix: **ten modules** derive image-thumbnail-stem occupancy from a `thumb_dir.glob("*.webp")` over that folder (enumerated under **Storage layout** above, nine of them building `occupied_thumb_stems` to pick a name and `routers/filesystem.py` to refuse one), and a suffix convention means every one of them must learn to filter it, with any one that forgets being a silent thumbnail clobber. `Dataset.video_count`/`video_size_bytes` are deliberately separate from `image_count`/`total_size_bytes`, and **must be passed explicitly** wherever a `DatasetOut` is hand-built field by field (`list_datasets`): both default to 0, so an omission reports every dataset as video-free instead of failing. A poster is a nicety, never a gate — every *decode* failure in `video_service.generate_poster` is a False, and every caller wraps its encode tail (which can still raise), so a video whose frames will not decode still ingests, lists, plays, renames and deletes. See `docs/dev/video/decode.md` for the decode surface, and `docs/dev/video/extract.md` for frames.

## Frontend

Four files, all split out of this one as the frontend grew; they are read together but sized
apart. `VideoStrip` and its selection, `VideoDetailPage`, the extraction history and the frame
lineage line are in `docs/dev/video/ui.md`. The two-step `ExtractFramesModal` and the job
re-attach hook are in `docs/dev/video/extract-ui.md`, with its `CropOverlay`/`TrimBar`/
`NumberField` controls in `docs/dev/video/extract-controls.md`. Pass 2's
`ReextractFramesForm`/`ReextractFramesModal` and their three entry points are in
`docs/dev/video/reextract-ui.md`.
