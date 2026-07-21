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
  preview_image_ids: string[];
  current_branch_id: string | null;
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
  megapixel_distribution: Record<string, number>;
  file_size_distribution: Record<string, number>;
  file_size_summary: Record<string, number>;
  aspect_ratio_fine: Record<string, number>;
  caption_length_distribution: Record<string, number>;
  caption_token_distribution: Record<string, number>;
  style_similarity_distribution: Record<string, number>;
  quality_flag_counts: Record<string, number>;
  score_coverage: Record<string, number>;
}

export interface TagCooccurrence {
  tags: string[];
  matrix: number[][];
}

export interface SubfolderInfo {
  path: string;
  image_count: number;
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
  style_similarity_score: number | null;
  dino_layer_scores: Record<string, number> | null;
  quality_flags: Record<string, unknown>;
  generation_metadata?: GenerationMetadata | null;
  caption_text: string;
  captioned_by: string;
  is_auto_named: boolean;
  sort_order: number | null;
  updated_at: string;
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
  throughput_ips?: number;
  vram_used_mb?: number;
}

export interface BooruTag {
  tag: string;
  count: number;
  category: string;
  source: string;
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
