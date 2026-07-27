# Video support — high-level roadmap

Working roadmap for the `experimental-video-support` arc. Each phase gets its own
detailed plan when it is built; this file only fixes the shape of the work, the
decisions already made, and the open questions each phase must settle. Delete this
file when the arc lands (subsystem knowledge then moves to `docs/dev/`, the same way
the detection-arc roadmap was retired).

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

## Non-goals (this arc)

- **Audio** — deferred entirely, but keep naming extensible: discriminators are
  `media_kind`-shaped, never `is_video` booleans.
- **Clip-level ML** — scoring/captioning/detection on video files themselves.
- **Clip export** for video trainers (Wan/Hunyuan style); noted under Later arcs.
- **Videos in the image grid** — the gallery grid's selection/dnd/filter machinery
  is image-typed; videos get their own strip/tab instead.

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
  already a dependency.
- `imageio-ffmpeg` — bundles a static ffmpeg binary, no system install needed.
- Possibly PyAV later if subprocess-ffmpeg seeking proves too imprecise for pass 2.

---

## Phase 0 — Foundations

Backend plumbing so videos can exist at all.

- Consolidate the three image-extension allowlists (`routers/filesystem.py`,
  `routers/images.py`, `services/dataset_service.py` — they already disagree about
  `.avif`) into one shared constants module; add a video allowlist beside it
  (mp4/mkv/webm/mov/avi at minimum).
- `Video` model + Alembic migration: dataset FK, filename, file_path, subfolder-
  free (flat in `videos/`), file_size_bytes, duration, fps, codec, width/height,
  poster thumbnail path, provenance columns mirroring `Image`'s four, plus
  extraction-settings columns (crop rect, deinterlace mode, head/tail trims).
- Storage layout: `{dataset.folder_path}/videos/` + poster thumbs (location TBD in
  detailed plan — likely `thumbnails/` with a distinct suffix to dodge the
  stem-collision invariant).
- Serving endpoint (`FileResponse` — range/206 support already comes free from
  Starlette) + metadata endpoint.
- Ingest: gallery upload accepts video files (routed to `Video`, not silently
  skipped); `ImportFolderModal` gains an "Include videos" toggle; rescan counts
  videos. ffprobe (via imageio-ffmpeg) fills duration/fps/codec/dimensions.
- Open questions for detailed planning: dataset stats/Image-count interplay,
  `dataset_busy` guard scope, whether the file browser gets a video branch now or
  in Phase 1.

## Phase 1 — Preview UI

Videos visible and playable inside the dataset.

- Poster-frame thumbnail generation via ffmpeg (single seek, mid-file or first
  post-trim frame).
- **Videos strip/tab in `GalleryPage`**: poster thumbs + duration badges,
  collapsed/hidden when the dataset has no videos so image-only datasets look
  unchanged.
- Video detail view (a pane, per the split-view pane manager): `<video>` player,
  metadata block, extraction history ("N frames → subfolder", linked), delete and
  rename, and the "Extract frames" button (wired in Phase 2).
- Basic management: delete video (file + poster + DB row), rename with slugify +
  collision handling.
- Open questions: pane vs. route, selection model for the strip (needed for batch
  extraction), whether `DatasetsPage` cards surface video counts.

## Phase 2 — Extraction v1

The core deliverable: shot-segmented triage extraction as a background job.

- Probe endpoint: sample ~N frames across the timeline, run cropdetect + interlace
  detection, return proposed crop + sample frame images for the modal overlay.
- `ExtractFramesModal` (two-step, shared, batch-capable) per the locked decisions.
- Extraction job (`job_type="video_extract"`, one job per video, auto-label like
  `"Extract: episode01 — 1 frame/shot"`): PySceneDetect adaptive shot detection →
  per-shot frame pick (mid-shot or sharpest-in-window Laplacian) → decode with the
  video's fixups (trim, deinterlace `bwdif`, crop) → downscaled triage JPEG →
  register as `Image` rows with lineage `{video_id, timestamp_ms, shot_index}`
  (real column for timestamp — re-extraction queries key off it; details in the
  phase plan). All decode work off the event loop (`run_in_executor`, folder-import
  pattern), SSE progress per shot batch, cancellation honored between shots.
- Disk preflight via `require_free_space`; frames inherit provenance from the
  video's row (which inherited from the dataset at ingest).
- Open questions: lineage storage split (columns vs `source_meta`), frame naming
  scheme (`{video_slug}_{shot:04d}_{frame}` vs plain slug counters),
  telecine/`fieldmatch,decimate` support now or later, re-running extraction on an
  already-extracted video (replace? append? refuse?).

## Phase 3 — Curation glue

Make the existing cascade sing for frames.

- `luminance_score` in `technical_scorer` (pure OpenCV, applies to *all* images;
  frames get it in the normal triage scoring pass) + Stats histogram/filter via the
  validator-keyed schema — enables stratifying for bright frames at the end.
- "Frames from video X" gallery filter (lineage-based), so a video's output is
  addressable beyond its subfolder.
- Optional: VLM keep/reject gate as a thin variant of the OpenAI-compatible
  captioner infrastructure (prompt → verdict → quality flag). Scope at planning
  time; may slip to its own branch.

## Phase 4 — Pass 2: full-res re-extraction

The survivors become training data.

- `SelectionToolbar` action on *frames* ("Re-extract at full res"), enabled when
  the selection carries video lineage; groups by source video, one job per video.
- Accurate seeking: coarse `-ss` before `-i`, fine seek after (or PyAV) — pin
  exactness in tests; off-by-one-keyframe extraction silently yields wrong frames.
- Overwrites the triage JPEG in place with full-res output — **must** go through
  the versioning backup-before-overwrite hook like upscale/LUT, and re-derive
  width/height/phash/thumbnail. Format/extension change (JPEG→PNG) interacts with
  filename + thumbnail-stem invariants — settle in the detailed plan.
- Replays the `Video` row's saved decode parameters (crop/deinterlace/trim) so
  pass-2 frames match pass-1 geometry exactly.

## Later arcs (explicitly out of scope now)

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
