# Video support — high-level roadmap

Working roadmap for the `experimental-video-support` arc. Each phase gets its own
detailed plan when it is built; this file only fixes the shape of the work, the
decisions already made, and the open questions each phase must settle. Delete this
file when the arc lands (subsystem knowledge then moves to `docs/dev/`, the same way
the detection-arc roadmap was retired).

Findings marked **(measured)** were verified empirically in the dev container on
2026-07-27, not reasoned about. Where a measurement contradicted an earlier
assumption, the assumption has been rewritten rather than annotated.

## Goal

Support workflows that prep data for training video LoRAs: import videos into a
dataset, preview them in-app, and extract frames by shot so the frames can be
curated with Crucible's existing image systems (dedup, quality scoring, captioning,
export). The curated-frame workflow is the product of this arc — not video-native
ML.

## Core design decision: videos are sources, frames are Images

Videos get their own `Video` model, table, and `{dataset}/videos/` folder. They are
**not** rows in `images` — the `Image` table carries ~20 image-specific columns,
FK cascades (detections, tags), and the load-bearing "thumbnails are `.webp` keyed
by stem" invariant, none of which apply to a video file.

Extraction converts video into ordinary `Image` rows at the boundary. Everything
downstream — pHash dedup, technical/aesthetic scoring, captioning, export,
versioning — operates on frames and needs **no media-awareness**. The entire ML
layer stays untouched this arc.

Lineage lives on the frame: `{video_id, timestamp_ms, shot_index}`. **The timestamp
is the real artifact** — the DB is the authoritative record (never a CSV), and the
full-res second pass re-seeks by timestamp rather than trusting the triage JPEG.

Every frame that reaches a user is a **file on disk** — an ordinary `Image` row in
`{dataset}/images/` with a `.webp` thumbnail, served by the existing endpoints. The
only in-memory image payloads in this arc are the handful of transient probe
previews (Phase 2); nothing downstream can consume anything but a file path.

## Non-goals (this arc)

- **Audio** — deferred entirely, but keep naming extensible: discriminators are
  `media_kind`-shaped, never `is_video` booleans. This applies to API fields and
  helper signatures, **not** the schema: the schema deliberately splits into two
  tables, so no `media_kind` column belongs on `Image`.
- **Clip-level ML** — scoring/captioning/detection on video files themselves.
- **Clip export** for video trainers (Wan/Hunyuan style); noted under Later arcs.
- **Videos in the image grid** — the gallery grid's selection/dnd/filter machinery
  is image-typed; videos get their own strip/tab instead.
- **Videos in version control** — snapshots do not capture `videos/`. This is a
  deliberate choice with a useful consequence: restoring a pre-extraction snapshot
  deletes the frames but leaves the video and its saved decode parameters intact,
  so re-running extraction reproduces them exactly.

## Locked decisions (from design discussion)

- Extraction params live in one shared **`ExtractFramesModal`** (built on
  `useModalBehavior`), never inline panels — same pattern as `MoveToDatasetModal`:
  one modal, thin entry points.
- Two entry points: per-video button in the video detail view, and batch over
  selected videos (one parameter set across a series — same trims/crop/detector).
- Modal is **two-step**: a probe step (sampled frames → cropdetect + interlace
  detection → preview with adjustable crop overlay, deinterlace toggle, head/tail
  trim), then parameters (shot-detection sensitivity, frames per shot + pick
  policy, triage resolution, target subfolder).
- Confirmed probe decisions (crop, deinterlace, trims) are **saved on the `Video`
  row** so pass 2 replays identical decode parameters.
- Frames land in the **same dataset**, default **subfolder per video** (video slug)
  — subfolder-scoped ops (scoring, captioning, export filters) become per-video
  scopes for free. Not subfolder-per-shot; shot index is frame metadata.
- Cross-dataset extraction targets are out of scope — `MoveToDatasetModal` covers
  "curate here, move survivors there".
- Two-pass extraction: pass 1 writes downscaled triage JPEGs; pass 2 re-extracts
  only survivors at full res by seeking their timestamps.

## Dependencies (new)

- `scenedetect` (PySceneDetect, adaptive detector) — default backend is OpenCV,
  already a dependency (`opencv-python>=4.9`, 5.0.0 installed).
- `imageio-ffmpeg` — bundles a static ffmpeg binary, no system install needed.
- `Pillow` pin rises from `>=10.0` to `>=11.3` (AVIF decode; see Phase 0).

**Division of labour between OpenCV and ffmpeg — do not blur this.** The 0.6.0
wheel ships exactly one binary, `ffmpeg-linux-x86_64-v7.0.2`, and **no ffprobe**
(measured: the wheel's file list, and `dir(imageio_ffmpeg)` exposes no probe entry
point). There is no system ffmpeg to fall back on either. So:

- **OpenCV** does metadata (Phase 0) and probe sampling (Phase 2). `cv2.VideoCapture`
  reads fps, dimensions and codec from the header across mp4/mkv/webm/mov/avi/ts,
  including HEVC 10-bit and ProRes 422 HQ, and decodes them all to 8-bit BGR
  (measured). `CAP_PROP_FOURCC` yields a stable 4-char codec code (`h264`, `VP90`,
  `hevc`, `apch`, `FMP4`) — store it raw, map to a display name with a fallback.
- **The ffmpeg binary** does the extraction filter chain (Phase 2) — `bwdif`
  deinterlace and crop, which OpenCV cannot express.

`imageio_ffmpeg.count_frames_and_secs()` is **not** an ingest-path option: its
source is a full decode pass (`-i … -vf null -f null -`) and its own docstring warns
it is slow and not certainly exact.

Possibly PyAV later if subprocess-ffmpeg seeking proves too imprecise for pass 2 —
though OpenCV's own seek measured accurate to within one frame (see Phase 4).

---

## Phase 0 — Foundations — **built**

Backend plumbing so videos can exist at all. Subsystem detail now lives in
`docs/dev/video.md`; the notes below are kept as the record of what was decided.
Two deviations from the plan as written, both deliberate:

- `DELETE /videos/{id}` shipped here rather than in Phase 1. Three ingest paths can
  create a `Video` in this phase and there was otherwise no way to undo a mis-import
  short of editing the DB. Rename still waits for Phase 1 and its UI.
- The file browser's move/rename/delete endpoints also learned to sync `Video` rows,
  which the bullet below does not mention. A video moved through the browser would
  otherwise leave a dangling row — the failure the `Image` branch already guards.

One thing the plan got wrong: poster-stem collisions are **not** covered by globbing
`videos/thumbnails/` the way image thumbnails are, because no poster exists until
Phase 1 writes one, so that directory is empty and `a.mp4`/`a.mkv` both claim stem
`a`. The occupied-stem set is seeded from existing `Video.filename` stems instead.

- Consolidate the three image-extension allowlists (`routers/filesystem.py`,
  `routers/images.py`, `services/dataset_service.py`) into one shared module,
  `backend/media_types.py` — flat, beside `licenses.py` and `utils.py`; the backend
  has no `constants/` package and two frozensets do not justify creating one. It
  exports `IMAGE_EXTENSIONS`, `VIDEO_EXTENSIONS` (mp4/mkv/webm/mov/avi at minimum)
  and `media_kind_for(suffix) -> "image" | "video" | None`.
- **The `.avif` disagreement resolves upward.** Pillow 12.3.0 reports
  `features.check("avif") is True` (measured), so the file browser's list is the
  correct one and the two importers are stale. But AVIF is a *build-time* feature:
  a source build can produce a Pillow 11.3+ where it is absent, which would recreate
  the same drift one layer down. So gate the entry at import rather than trusting
  the pin:

  ```python
  _AVIF = {".avif"} if features.check("avif") else set()
  IMAGE_EXTENSIONS = frozenset({".png", ".jpg", …} | _AVIF)
  ```

  Raising the pin to `>=11.3` states intent; the gate enforces reality. The pin is
  not an upgrade — the venv already runs 12.3.0 and 329 backend tests pass on it
  (measured) — and Pillow 11.3 needs Python `>=3.9` against the project's own 3.12+
  floor, so it cannot make the dependency unresolvable.
- `Video` model + Alembic migration: dataset FK, filename, file_path, subfolder-
  free (flat in `videos/`), file_size_bytes, duration, fps, codec, width/height,
  poster thumbnail path, provenance columns mirroring `Image`'s four, plus
  extraction-settings columns (crop rect, deinterlace mode, head/tail trims).
- **Metadata ladder, with a mandatory guard.** cv2 header for fps/dimensions/codec
  always → duration from `frames / fps` **only when `0 < frames < 1e9`** →
  otherwise `duration = None`, rendered "unknown". The guard is not defensive
  padding: a matroska written to a non-seekable pipe (no duration, no cues — what
  stream-ripped and partially-copied files look like) returns
  `CAP_PROP_FRAME_COUNT = -230584300921369408`, which turns a naive `frames / fps`
  into a duration of −9.2e15 seconds (measured). fps, dimensions and codec were all
  still correct for that file; only the count is poisoned. Parsing the `ffmpeg -i`
  banner is **not** a useful rescue — it prints `Duration: N/A` for exactly that
  file (measured). Backfill the true duration in Phase 2's probe, which is already
  decoding.
- **Ingest gate comes free**: `isOpened()` returns `False` on a truncated or
  zero-byte file (measured). Use it as the "we cannot decode this" rejection at
  upload, with a readable error rather than a silent skip.
- Storage layout: `{dataset.folder_path}/videos/` for the files, and poster thumbs
  in **`{dataset}/videos/thumbnails/`** — a separate directory, *not* the images
  thumbnail folder with a distinguishing suffix. Eight code paths build
  `occupied_thumb_stems` from `thumb_dir.glob("*.webp")` (`routers/images.py`,
  `captioning.py`, `comfy.py`, `lut.py`, `upscaling.py`, `detection.py`,
  `version_service.py`, `dataset_service.py`); a suffix convention means all eight
  must learn to filter it, and any one that forgets is a silent thumbnail clobber.
  A separate directory means none of them change.
- Serving endpoint (`FileResponse`) + metadata endpoint. Range/206 support does come
  free: Starlette 1.3.1's `FileResponse` sets `accept-ranges: bytes` and handles
  `Range`, `If-Range` and 416 (measured).
- Ingest: gallery upload accepts video files (routed to `Video` — today the upload
  loop silently `continue`s on an unknown suffix); `ImportFolderModal` gains an
  "Include videos" toggle; rescan counts videos.
  - Folder import is the clean one: `_scan_source_files` is the single decision
    point for what is importable **and** computes `source_bytes` in the same
    traversal, feeding `require_free_space`. The toggle threads through one
    function and the disk preflight covers video bytes automatically.
  - Rescan is **not** free: it walks `images_dir.rglob("*")`, so `videos/` is
    invisible to it and counting videos needs a second explicit pass.
- **Stats: `image_count` stays images-only.** `refresh_stats` is DB-derived (sums
  `Image.file_size_bytes`), never a folder walk, so videos cannot distort it by
  accident. Add sibling `video_count` and `video_size_bytes` columns populated in
  the same function. Videos are 100× the size of the frames they yield; folding
  them into `total_size_bytes` would make every dataset card read as bloated, and
  `image_count` is what a user compares against an export manifest. Frames need no
  special-casing — they arrive as ordinary `Image` rows and count themselves.
- **`dataset_busy`: extraction does not take the flag.** Background jobs are already
  serialized by the single job queue, so an extraction job can never overlap a
  versioning job; the flag exists only to fence *interactive* endpoints. Extraction
  runs for minutes and only appends new rows and files into a fresh subfolder, so
  holding `busy` would 409 every caption edit for the duration with no safety gain.
  Call `ensure_not_busy` once at enqueue and leave it there.
- **File browser gets its plumbing now, previews in Phase 1.** The browser is an
  import surface (it gates import on `IMAGE_EXTENSIONS`), so without this a user
  browsing a video folder sees an empty, unimportable directory. Add `media_kind`
  to the listing payload and route video imports; render a film-strip glyph. Poster
  previews wait for Phase 1, when poster generation exists.
- `delete_dataset` already rmtrees the whole folder, so videos go with it — no
  change. Export iterates `Image` rows and never walks the folder, so videos cannot
  leak into an export — no change.

## Phase 1 — Preview UI

Videos visible and playable inside the dataset.

- Poster-frame thumbnail generation via ffmpeg (single seek, mid-file or first
  post-trim frame).
- **Videos strip/tab in `GalleryPage`**: poster thumbs + duration badges,
  collapsed/hidden when the dataset has no videos so image-only datasets look
  unchanged.
- **Video detail gets a real route *and* a pane view**, exactly like image-detail:
  `/datasets/:datasetId/video/:videoId` → `{page: "video-detail", datasetId,
  videoId}`. `routeToView` already pattern-matches the `/image/:imageId` shape, so
  this is one regex, one `PageRenderer` branch and one lazy route. It buys shareable
  URLs, back/forward, and — the real payoff — split view with the video in one pane
  and its frame subfolder in the other, which is the core curation loop. A modal
  would foreclose that.
- Contents: `<video>` player, metadata block, extraction history ("N frames →
  subfolder", linked), delete and rename, and the "Extract frames" button (wired in
  Phase 2). Extraction history needs no table — derive it with
  `SELECT subfolder, COUNT(*) … WHERE source_video_id = ?`.
- **Strip selection is its own small state, not the gallery selection store**, which
  is image-typed and drags along shift-range, dnd and filter machinery this does not
  want. A local `Set<string>` plus shift-range over the strip's own order is enough;
  batch extraction is its only consumer.
- Basic management: delete video (file + poster + DB row), rename with slugify +
  collision handling.
- `DatasetsPage` cards get a video-count badge, hidden at zero, fed by the
  `video_count` column from Phase 0.

## Phase 2 — Extraction v1

The core deliverable: shot-segmented triage extraction as a background job.

- **Probe endpoint is a plain request, not a job.** Measured: 8 seek+decode
  operations cost 0.060 s total, 7.5 ms each. Run it through `run_in_executor`
  (well precedented on the request path — the directory listing, the ComfyUI
  workflow scan, upload metadata extraction all do this), with a fixed sample cap
  (≤12) and an `asyncio.wait_for` around it. Note in the code *why* the timeout is
  legitimate here and not the trap CLAUDE.md describes for stdlib `re`: cv2 releases
  the GIL during decode and each seek is individually bounded, so the timeout
  genuinely fires and abandons the response while the thread finishes harmlessly.
  Return partial samples when some seeks fail; broken tails are common.
- **Probe frames travel as base64 data URLs in the response** — decoded, downscaled,
  JPEG-encoded and base64'd in memory, never written to disk. Measured: JPEG 32.7 KB
  → base64 43.6 KB (1.33×), so an 8-sample response is ~348 KB. The alternative,
  temp files, needs a serving endpoint, a cleanup sweep and a traversal guard on an
  unauthenticated server — strictly more surface for a preview that lives for two
  seconds.
  - **The memory cost is upstream and much larger than the payload.** A decoded 4K
    frame is 24.9 MB as an array; collecting 12 before post-processing is ~300 MB of
    peak RSS (measured). Downscale and encode *inside* the loop and release each
    frame before seeking the next, accumulating only the ~44 KB strings — peak is
    then one frame plus ~350 KB. Never build a `list[np.ndarray]` and map over it
    afterward. Same failure class as the "close PIL Images after preprocessing"
    invariant. Cap sample count and total encoded bytes server-side.
- `ExtractFramesModal` (two-step, shared, batch-capable) per the locked decisions.
- Extraction job (`job_type="video_extract"`, one job per video, auto-label like
  `"Extract: episode01 — 1 frame/shot"`): PySceneDetect adaptive shot detection →
  per-shot frame pick (mid-shot or sharpest-in-window Laplacian) → decode with the
  video's fixups (trim, deinterlace `bwdif`, crop) → downscaled triage JPEG →
  register as `Image` rows with lineage. All decode work off the event loop
  (`run_in_executor`, folder-import pattern), SSE progress per shot batch,
  cancellation honored between shots.
- **Lineage is three real columns, and they must be mirrored in three places.** On
  `Image`: `source_video_id` (FK → `videos.id`, `ondelete="SET NULL"`, indexed),
  `source_timestamp_ms`, `source_shot_index`. Not `source_meta` — it is
  `deferred=True` (every ORM reader would need `undefer`, the `MissingGreenlet`
  trap), and both the Phase 3 "frames from video X" filter and Phase 4's group-by-
  video need indexed queries.
  - The mirrors: `VersionImageState` (or a snapshot restore silently wipes lineage,
    the same trap CLAUDE.md documents for provenance), **and both explicit column
    lists in `duplicate_dataset`** — the on-disk branch and the snapshot branch each
    construct `Image` rows field by field. Three sites, one migration. Every one of
    them fails silently and none has a test that would notice.
  - `SET NULL`, not `CASCADE`: deleting a source video must not delete curated
    frames. The delete-video dialog says "N extracted frames will keep their files
    but lose lineage."
- **`duplicate_dataset` does not copy videos** — it already copies only `Image`
  rows, so this needs no code change. Duplicated frames get `source_video_id = None`
  rather than a cross-dataset pointer, which would make the Phase 3 filter return
  rows from a dataset the user is not viewing and make the delete-video confirm
  count span datasets. `source_timestamp_ms`/`source_shot_index` are kept — still
  true, still useful. Say it in the duplicate modal.
- **Frame naming: `{video_slug}_s{shot:04d}_{pick:02d}`.** Shot-grouped, lexically
  sortable, and stable across pass 2 (which overwrites in place, so the name must
  not depend on seek precision). No timestamp in the filename — the DB is
  authoritative, and a ms in the name invites trusting it after a re-extract that
  landed a keyframe away. The generated name is a *proposal*; it still goes through
  `unique_filename_with_thumb` like every other creation path.
- **Telecine: detect and warn now, implement later.** The probe can report "3:2
  pulldown detected" cheaply; correct `fieldmatch,decimate` is a rabbit hole (field
  order, mixed cadence) and modern video-LoRA source material is overwhelmingly
  progressive. Ship `bwdif` only, with warning text naming the limitation.
- **Re-extraction offers three choices, defaulting to a new subfolder.** When the
  modal sees prior extractions for a video: *Add to existing subfolder* / *New
  subfolder (`{slug}_2`)* / *Replace (delete previous N frames)*. Default is new — a
  re-run almost always means "different params, compare results". Refusing is
  user-hostile; silently replacing destroys curation work. Replace must go through
  the normal image-delete path (thumbnail, sidecar, `mark_image_deleted_in_versions`),
  never a raw unlink.
- **Triage resolution defaults to 1024px on the long edge** (the modal still exposes
  it). Large enough that technical and aesthetic scoring stay meaningful, small
  enough that a 900-shot episode is cheap to write, score and eyeball — and pass 2
  re-seeks the survivors at full res anyway, so nothing is lost by keeping pass 1
  lean.
- Disk preflight via `require_free_space`; frames inherit provenance from the
  video's row (which inherited from the dataset at ingest).

## Phase 3 — Curation glue

Make the existing cascade sing for frames.

- `luminance_score` in `technical_scorer` (pure OpenCV, applies to *all* images;
  frames get it in the normal triage scoring pass) + Stats histogram/filter via the
  validator-keyed schema — enables stratifying for bright frames at the end.
- "Frames from video X" gallery filter (lineage-based), so a video's output is
  addressable beyond its subfolder.

Those two items are the whole phase. The VLM keep/reject gate that was previously
sketched here is **deferred out of this arc** — see Later arcs.

## Phase 4 — Pass 2: full-res re-extraction

The survivors become training data.

- `SelectionToolbar` action on *frames* ("Re-extract at full res"), enabled when
  the selection carries video lineage; groups by source video, one job per video.
- **Seeking looks less risky than assumed.** OpenCV's `CAP_PROP_POS_MSEC` seek
  measured accurate to within one frame across eight sample points (`want=625 ms →
  got=640 ms` at 25 fps) — it is not keyframe-snapped. Still pin exactness in tests;
  off-by-one-keyframe extraction silently yields wrong frames, and this was measured
  on a short clip, not a two-hour one.
- Overwrites the triage JPEG in place with full-res output — **must** go through
  the versioning backup-before-overwrite hook like upscale/LUT, and re-derive
  width/height/phash/thumbnail.
- **Whether pass 2 writes PNG instead of JPEG is deliberately left open**, to be
  decided with the pass-2 code in front of us. It is not a free choice: an extension
  change moves the file, which collides with both the filename uniqueness rule and
  the thumbnail-stem invariant, so it has to be settled together with the rename
  path rather than picked on quality grounds alone.
- Replays the `Video` row's saved decode parameters (crop/deinterlace/trim) so
  pass-2 frames match pass-1 geometry exactly.

## Later arcs (explicitly out of scope now)

- **VLM keep/reject gate**: a thin variant of the OpenAI-compatible captioner
  infrastructure (prompt → verdict → quality flag). Applies to all images, not just
  frames, which is precisely why it does not belong in a video arc — it is its own
  branch with its own settings surface and cost model.
- **Clip-level curation**: clips as trainable artifacts — media-kind-aware gallery
  cards, clip captioning (VLM video input), clip trimming, export for video-LoRA
  trainers (export's `original`-format byte-copy path already ships files
  unmodified, so this is closer than it looks).
- **Audio**: zero supporting infrastructure today (no librosa/torchaudio, no
  waveform rendering); shares little with frame extraction. Revisit as its own
  product surface.

## Cross-cutting rules (every phase)

- User-visible changes update `README.md` + relevant `docs/*.md` in the same
  change; run `python scripts/check_docs.py` (per CLAUDE.md doc rules).
- Model/migration changes run `python scripts/check_migrations.py`.
- New job types follow the label + SSE + cancellation conventions; new modals
  spread `useModalBehavior`.
- Naming stays `media_kind`-extensible for the eventual audio arc.
