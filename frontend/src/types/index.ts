export interface Dataset {
  id: string;
  name: string;
  description: string;
  folder_path: string;
  created_at: string;
  updated_at: string;
  image_count: number;
  captioned_count: number;
  total_size_bytes: number;
  preview_image_ids: string[];
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
  tags_json: string[];
  captioned_by: string;
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
  detected_at: string;
}

export interface ImageDetail extends ImageListItem {
  original_filename: string;
  phash: string | null;
  noise_score: number | null;
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
  tags: string[];
  caption_style: string;
  captioned_by: string;
}

export interface TagStat {
  tag: string;
  count: number;
  category: string;
}

export interface Job {
  id: string;
  job_type: string;
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
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  done: number;
  total: number;
  percent: number;
  current_item?: string;
  message?: string;
  image_id?: string;
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
