import client from "./client";

/** The order stages always run in, whichever are switched on (`schemas/run.STAGE_ORDER`). */
export const RUN_STAGES = [
  "prompts",
  "handoff_to_comfy",
  "generate",
  "handoff_to_local",
  "score",
  "caption",
  "export",
] as const;
export type RunStageKey = (typeof RUN_STAGES)[number];

export const RUN_STAGE_LABELS: Record<RunStageKey, string> = {
  prompts: "Fill the blueprint",
  handoff_to_comfy: "Free VRAM for ComfyUI",
  generate: "Generate",
  handoff_to_local: "Free ComfyUI's VRAM",
  score: "Score",
  caption: "Caption",
  export: "Export",
};

export type RunStatus = "running" | "paused" | "completed" | "failed" | "cancelled";
export type StageStatus = "pending" | "running" | "done" | "skipped" | "failed" | "cancelled";

/** `schemas/run.CaptionSettings`: named as on the caption job request, passed through whole. */
export interface RunCaptionSettings {
  model: string;
  style: string;
  custom_prompt: string;
  overwrite: boolean;
  target_width: number | null;
  target_height: number | null;
  strip_refusals: boolean;
  strip_thinking: boolean;
  strip_underscores: boolean;
  strip_hedges: boolean;
  dedupe_tags: boolean;
  save_backup: boolean;
  rename_on_caption: boolean;
  wd14_threshold: number;
  delimiter_mode: "overwrite" | "append" | "prepend";
  delimiter: string;
  min_aesthetic_score: number | null;
  exclude_flags: string[] | null;
}

/** `schemas/run.ExportSettings`: named as on the export requests; `exclude_flags` is a list. */
export interface RunExportSettings {
  kind: "plain" | "kohya" | "aitoolkit";
  output_dir: string;
  n_repeats: number;
  concept_token: string;
  concept_name: string;
  caption_format: "txt" | "caption" | "jsonl";
  output_format: string;
  jpeg_quality: number;
  resize_to: number | null;
  aesthetic_min: number | null;
  captioned_only: boolean;
  exclude_flags: string[];
  style_sim_min: number | null;
  strip_metadata: boolean;
  captions_only: boolean;
}

export interface RunRecipe {
  stages: RunStageKey[];
  generate: { subfolder: string; set_caption: boolean };
  score: { run_aesthetic: boolean; aesthetic_model: "laion" | "v2_5" };
  caption: RunCaptionSettings | null;
  export: RunExportSettings | null;
}

export interface RunStage {
  key: RunStageKey;
  status: StageStatus;
  job_id: string | null;
  note: string | null;
  summary: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface Run {
  id: string;
  dataset_id: string;
  plan_id: string | null;
  name: string;
  recipe: RunRecipe;
  stages: RunStage[];
  status: RunStatus;
  pause_requested: boolean;
  current_job_id: string | null;
  error_msg: string | null;
  row_count: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface RunStartBody {
  dataset_id: string;
  plan_id: string;
  name?: string;
  recipe: RunRecipe;
}

export const runsApi = {
  list: (datasetId: string) =>
    client.get<Run[]>("/runs/", { params: { dataset_id: datasetId } }).then((r) => r.data),
  get: (id: string) => client.get<Run>(`/runs/${id}`).then((r) => r.data),
  start: (body: RunStartBody) => client.post<Run>("/runs/", body).then((r) => r.data),
  pause: (id: string) => client.post<Run>(`/runs/${id}/pause`).then((r) => r.data),
  resume: (id: string) => client.post<Run>(`/runs/${id}/resume`).then((r) => r.data),
  cancel: (id: string) => client.post<Run>(`/runs/${id}/cancel`).then((r) => r.data),
  remove: (id: string) => client.delete(`/runs/${id}`),
};

export const isLiveRun = (r: Run) => r.status === "running" || r.status === "paused";
