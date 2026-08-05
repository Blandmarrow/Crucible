export interface Branch {
  id: string;
  dataset_id: string;
  name: string;
  head_version_id: string | null;
  head_version_name: string | null;
  created_at: string;
}

export interface Version {
  id: string;
  dataset_id: string;
  branch_id: string | null;
  parent_id: string | null;
  name: string | null;
  description: string;
  image_count: number;
  created_at: string;
  source: "manual" | "pre_restore" | "branch_init";
  is_pinned: boolean;
}

export interface DiffImageEntry {
  image_id: string | null;
  filename: string;
  subfolder: string;
  caption: string;
}

/** Heavy JSON fields (generation_metadata, dino_layer_scores) arrive as a
 *  compact `{ changed: true }` marker instead of full from/to values. */
export type FieldChange = { from: unknown; to: unknown } | { changed: true };

export interface ModifiedImageDiff {
  image_id: string | null;
  filename: string;
  subfolder: string;
  changes: Record<string, FieldChange>;
}

export interface VersionDiff {
  added: DiffImageEntry[];
  removed: DiffImageEntry[];
  modified: ModifiedImageDiff[];
  unchanged_count: number;
  summary: { added: number; removed: number; modified: number; unchanged: number };
}

export interface Dataset {
  id: string;
  name: string;
  description: string;
  category: string;
  folder_path: string;
  created_at: string;
  updated_at: string;
  image_count: number;
  captioned_count: number;
  total_size_bytes: number;
  /** Videos are counted apart from image_count/total_size_bytes — a video is
   *  orders of magnitude larger than the frames it yields. */
  video_count: number;
  video_size_bytes: number;
  preview_image_ids: string[];
  current_branch_id: string | null;
  /** Provenance defaults inherited by images whose own field is unset. */
  source_name: string;
  source_url: string;
  license: string;
  attribution: string;
}

export interface DatasetStats {
  id: string;
  name: string;
  image_count: number;
  captioned_count: number;
  caption_coverage_pct: number;
  total_size_bytes: number;
  total_size_mb: number;
  avg_width: number | null;
  avg_height: number | null;
  aspect_ratio_distribution: Record<string, number>;
  format_distribution: Record<string, number>;
  score_distribution: Record<string, number>;
  blur_distribution: Record<string, number>;
  noise_distribution: Record<string, number>;
  uniformity_distribution: Record<string, number>;
  watermark_distribution: Record<string, number>;
  color_distribution: Record<string, number>;
  saturation_distribution: Record<string, number>;
  luminance_distribution: Record<string, number>;
  megapixel_distribution: Record<string, number>;
  file_size_distribution: Record<string, number>;
  file_size_summary: Record<string, number>;
  aspect_ratio_fine: Record<string, number>;
  caption_length_distribution: Record<string, number>;
  caption_token_distribution: Record<string, number>;
  style_similarity_distribution: Record<string, number>;
  quality_flag_counts: Record<string, number>;
  score_coverage: Record<string, number>;
  /** Effective license id -> count; "" = no license recorded at either level. */
  license_breakdown: Record<string, number>;
}

export interface TagCooccurrence {
  tags: string[];
  matrix: number[][];
}

export interface SubfolderInfo {
  path: string;
  image_count: number;
}

/** Result of `PATCH /datasets/{id}/subfolders` — a rename or a re-nest, both being
 *  a subtree prefix rewrite. `previous_path` is what the caller re-points its
 *  path-keyed state (active subfolder, expanded set) from. */
export interface SubfolderRepathResult {
  path: string;
  previous_path: string;
  images_updated: number;
}

export interface ImageListItem {
  id: string;
  dataset_id: string;
  filename: string;
  subfolder: string;
  width: number | null;
  height: number | null;
  file_size_bytes: number | null;
  format: string | null;
  aesthetic_score: number | null;
  blur_score: number | null;
  uniformity_score: number | null;
  watermark_score: number | null;
  color_score: number | null;
  saturation_score: number | null;
  luminance_score: number | null;
  style_similarity_score: number | null;
  /** The pixels were rewritten in place (resize, crop, LUT, upscale, crop-to-
   *  detection, frame re-extraction) after the scores above were measured, so
   *  they and the `quality_flags` derived from them describe an image that no
   *  longer exists. Cleared by a quality run that refreshes every score the
   *  image carries. `ImageDetail` inherits this. */
  scores_stale: boolean;
  dino_layer_scores: Record<string, number> | null;
  quality_flags: Record<string, unknown>;
  generation_metadata?: GenerationMetadata | null;
  caption_text: string;
  captioned_by: string;
  is_auto_named: boolean;
  sort_order: number | null;
  updated_at: string;
  /**
   * List endpoint: the *effective* license (own value coalesced over the
   * dataset default). Detail endpoint: the *raw* stored value, where null
   * means inherited — read `provenance` there for the resolved view.
   */
  license: string | null;
  /** Frame lineage marker — set only on images produced by video extraction, and
   *  cleared when the source video is deleted. The list payload carries the id
   *  alone; timestamps and shot index live on `ImageDetail`. */
  source_video_id: string | null;
  /** Label ids attached to this image — resolved to names and colours through
   *  `useLabels`, since the vocabulary is global and fetched once. Drives the
   *  coloured dots on `ImageCard`. `ImageDetail` inherits this. */
  label_ids: string[];
}

export interface GenerationMetadata {
  source?: string;
  prompt?: string;
  negative_prompt?: string;
  seed?: number;
  steps?: number;
  cfg_scale?: number;
  sampler?: string;
  model?: string;
  model_hash?: string;
  size?: string;
  vae?: string;
  raw?: string;
  comfyui_workflow?: Record<string, unknown>;
}

export interface Detection {
  id: number;
  label: string;
  bbox: [number, number, number, number]; // [x1, y1, x2, y2] normalized 0-1
  score: number | null;
  model: string;
  task: string;
  mask?: string | null;
  detected_at: string;
}

export interface ImageDetail extends ImageListItem {
  original_filename: string;
  phash: string | null;
  noise_score: number | null;
  nsfw_score: number | null;
  caption_style: string;
  captioned_at: string | null;
  created_at: string;
  generation_metadata?: GenerationMetadata | null;
  has_dino_layer_embeddings: boolean;
  detections: Detection[];
  /** Raw provenance as stored — null/"" means the field is inherited. */
  source_name: string | null;
  source_url: string | null;
  attribution: string | null;
  /** Resolved view; `inherited` lists which fields came from the dataset.
   *  `source_meta` lives here only — it is a scraper's raw payload and was
   *  duplicated at the top level, on a response refetched per arrow-key nav. */
  provenance: ResolvedProvenance | null;
  /** Where in the source video this frame came from. Both survive the video's
   *  deletion — a frame keeps knowing its position even once `source_video_id`
   *  has gone null. */
  source_timestamp_ms: number | null;
  source_shot_index: number | null;
}

export interface ResolvedProvenance {
  source_name: string;
  source_url: string;
  license: string;
  attribution: string;
  source_meta: Record<string, unknown> | null;
  inherited: string[];
}

export interface CaptionData {
  image_id: string;
  caption_text: string;
  caption_style: string;
  captioned_by: string;
}

export interface TagStat {
  tag: string;
  count: number;
}

export interface Job {
  id: string;
  job_type: string;
  label: string | null;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  dataset_id: string | null;
  total_items: number;
  done_items: number;
  error_msg: string | null;
  result_data: Record<string, unknown>;
  config: Record<string, unknown>;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface JobProgress {
  type: string;
  job_id: string;
  job_type: string;
  label?: string | null;
  dataset_id?: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  done: number;
  total: number;
  percent: number;
  current_item?: string;
  message?: string;
  image_id?: string;
  /** comfy_prompts: the plan rows are being written into. Emitters pass extra keys
   *  through the raw dict; jobStore merges by job id, so these survive onto the
   *  worker's terminal event, which carries neither. */
  plan_id?: string;
  /** comfy_prompts: prompts asked for. `total` becomes the created count when the
   *  job completes, so the shortfall is only visible by comparing against this. */
  requested?: number;
  /** video_extract: which of a batch's N jobs this is. Exactly `plan_id`'s role —
   *  one job per video, one store holding every event. */
  video_id?: string;
  /** video_extract: "detecting" | "replacing" | "extracting". One monotone
   *  percent spans all three, so this is the only way to label the stage. */
  phase?: string;
  /** video_extract: shot counters, for a "shot 12 of 340" line the generic
   *  done/total (which counts frames) cannot give. */
  shot?: number;
  shots?: number;
  /** True only on the frontend's own cancel-button write, never on a server event
   *  (`useSSE` force-clears it). TopBar's terminal-status watcher skips optimistic
   *  entries so invalidation waits for the backend to actually finish cancelling. */
  optimistic?: boolean;
  throughput_ips?: number;
  vram_used_mb?: number;
}

export interface BooruTag {
  tag: string;
  count: number;
  category: string;
  source: string;
}

/** A source video. Videos are not Images: separate table, separate folder
 *  (`{dataset}/videos/`), separate stats. Frames extracted from a video become
 *  ordinary Image rows. */
export interface Video {
  id: string;
  dataset_id: string;
  filename: string;
  original_filename: string;
  width: number | null;
  height: number | null;
  file_size_bytes: number | null;
  /** null when the container header carried no trustworthy frame count —
   *  render "unknown", never 0. */
  duration_ms: number | null;
  fps: number | null;
  /** Raw FOURCC as stored; `codec_label` is the display form. */
  codec: string | null;
  codec_label: string;
  has_poster: boolean;
  created_at: string;
  updated_at: string;
  /** Decode fixups replayed by frame extraction. All-null crop means no crop. */
  crop_x: number | null;
  crop_y: number | null;
  crop_w: number | null;
  crop_h: number | null;
  deinterlace: string;
  trim_start_ms: number;
  trim_end_ms: number;
  /** Raw provenance: null/"" means inherited from the dataset. */
  source_name: string | null;
  source_url: string | null;
  license: string | null;
  attribution: string | null;
  provenance: Record<string, unknown> | null;
}

/** `Video.crop_*` column order, in *frame* coordinates. The backend snaps to even
 *  numbers and treats a full-frame rect as no crop at all, so the value stored is
 *  not necessarily the value sent — re-read the Video row after an extract. */
export interface CropRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** What the extraction backend can actually do, from the probe response.
 *  `shot_detection: false` means frames get sampled at fixed intervals;
 *  `deinterlace: false` means the filter is unavailable, not merely off. */
export interface ExtractCapabilities {
  shot_detection?: boolean;
  deinterlace?: boolean;
  scenedetect_version?: string;
  ffmpeg_version?: string;
}

export interface VideoProbeRequest {
  samples?: number;
  trim_start_ms?: number;
  trim_end_ms?: number;
  max_edge?: number;
}

export interface VideoProbeSample {
  timestamp_ms: number;
  /** A `data:image/jpeg;base64,…` URL — never a path; there is no endpoint that
   *  would serve a temp preview file. */
  data_url: string;
}

export interface VideoProbeResult {
  samples: VideoProbeSample[];
  /** null means no matte was found, which is different from "not looked for". */
  crop: CropRect | null;
  crop_confidence: number;
  interlace: boolean;
  /** Detected, not corrected — only bwdif ships. */
  telecine: boolean;
  /** "unknown" means the container will not seek, so the samples are head-only
   *  and the tail trim is unavailable. */
  duration_source: "header" | "measured" | "unknown";
  duration_ms: number | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  samples_failed: number;
  truncated: boolean;
  warnings: string[];
  capabilities: ExtractCapabilities;
}

export interface VideoExtractRequest {
  video_ids: string[];
  crop?: CropRect | null;
  /** Not redundant with `crop: null` — that means "leave the stored rect alone",
   *  this means "the user cleared it". */
  clear_crop?: boolean;
  deinterlace?: "" | "bwdif" | null;
  trim_start_ms?: number | null;
  /** Milliseconds cut off the *tail*, not an end position. */
  trim_end_ms?: number | null;
  sensitivity?: number;
  min_shot_ms?: number;
  detector_frame_skip?: number;
  max_shots?: number;
  frames_per_shot?: number;
  pick?: "sharpest" | "middle";
  candidates?: number;
  long_edge?: number;
  mode?: "add" | "new_subfolder" | "replace";
  subfolder?: string | null;
  label?: string | null;
}

export interface VideoExtractJob {
  video_id: string;
  filename: string;
  job_id: string;
  subfolder: string;
}

export interface VideoExtractResult {
  jobs: VideoExtractJob[];
  /** Videos already covered by a pending or running extraction. Named rather
   *  than silently folded in; the rest of the batch still enqueues. */
  skipped: { video_id: string; filename: string; reason: string }[];
}

/** Pass 2: re-cut already-extracted frames from their source video at full
 *  resolution. Exactly one scope — `image_ids` for a gallery selection, or
 *  `video_id` (optionally narrowed by `subfolder`) for a whole triage batch. */
export interface VideoReextractRequest {
  image_ids?: string[];
  video_id?: string;
  subfolder?: string;
  /** PNG is a lossless capture and changes the file extension; the stem, the
   *  thumbnail and the .txt sidecar all stay where they are. */
  format?: "jpeg" | "png";
  /** Omitted = native resolution, which is the point of pass 2. */
  max_long_edge?: number | null;
  label?: string | null;
}

export interface VideoReextractGroup {
  video_id: string;
  filename: string;
  frames: number;
  /** null on the preview endpoint, which resolves without writing anything. */
  job_id: string | null;
}

export interface VideoReextractResult {
  groups: VideoReextractGroup[];
  /** Every frame the run will not touch, with a reason a user can act on. */
  skipped: { image_id: string; filename: string; reason: string }[];
  eligible: number;
  total: number;
}

export interface VideoFramesGroup {
  /** "" is the dataset root — a real group, never "no subfolder". */
  subfolder: string;
  count: number;
  last_extracted_at: string | null;
}

export interface VideoFramesSummary {
  total: number;
  groups: VideoFramesGroup[];
}

export interface ModelInfo {
  id: string;
  name: string;
  vram_mb: number;
  loaded: boolean;
}

export interface OllamaModel {
  id: string;
  name: string;
  size_mb: number;
}
