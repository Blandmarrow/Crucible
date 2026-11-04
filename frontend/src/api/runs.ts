import client from "./client";

/** The stages a recipe can switch on, in the order they always run
 *  (`schemas/run.STAGE_ORDER`, less `next_pass`, which a looping run gets by itself). */
export const RUN_STAGES = [
  "prompts",
  "rewrite",
  "free_rewrite_model",
  "handoff_to_comfy",
  "generate",
  "handoff_to_local",
  "score",
  "gate",
  "caption",
  "free_caption_model",
  "export",
] as const;
export type RunStageKey = (typeof RUN_STAGES)[number];
/** A key a run's stage list can hold: the recipe's, plus the loop's own. */
export type RunStageEntryKey = RunStageKey | "next_pass";

export const RUN_STAGE_LABELS: Record<RunStageEntryKey, string> = {
  prompts: "Fill the blueprint",
  rewrite: "Rewrite prompts",
  free_rewrite_model: "Unload the rewrite model",
  handoff_to_comfy: "Free VRAM for ComfyUI",
  generate: "Generate",
  handoff_to_local: "Free ComfyUI's VRAM",
  score: "Score",
  gate: "Gate",
  next_pass: "Decide the next pass",
  caption: "Caption",
  free_caption_model: "Unload the caption model",
  export: "Export",
};

/** The flags a gate may test (`schemas/run.GATE_FLAGS`): the ones the score stage sets. */
export const GATE_FLAGS = ["is_blurry", "is_noisy", "is_uniform", "is_duplicate", "has_watermark", "is_nsfw"] as const;

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

/** `schemas/run.GateSettings`. A stale score always fails. */
export interface RunGateSettings {
  aesthetic_min: number | null;
  exclude_flags: string[];
}

/** `schemas/run.RewriteSettings`: the rewrite route's settings plus the reviewed scenarios. */
export interface RunRewriteSettings {
  provider_id: string;
  model_name: string;
  batch_size: number;
  temperature: number;
  scenarios: string[];
}

/** `schemas/run.LoopSettings`: the goal loop's hard caps. */
export interface RunLoopSettings {
  max_passes: number;
  max_images: number;
  max_minutes: number;
  max_paid_calls: number | null;
}

export interface RunRecipe {
  stages: RunStageKey[];
  generate: { subfolder: string; set_caption: boolean };
  score: { run_aesthetic: boolean; aesthetic_model: "laion" | "v2_5" };
  caption: RunCaptionSettings | null;
  export: RunExportSettings | null;
  gate?: RunGateSettings | null;
  rewrite?: RunRewriteSettings | null;
  loop?: RunLoopSettings | null;
}

export interface RunStage {
  key: RunStageEntryKey;
  /** Which pass of the loop; null for a stage after the last pass. */
  pass_no: number | null;
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
  /** The loop's ending, once it has one. */
  outcome: { outcome: "met" | "short"; reason: string | null } | null;
  /** The loop's folders, short ones first (capped; `leaf_count` is the total). */
  leaves: RunLeaf[];
  leaf_count: number;
  /** Folders full, over all of them (`leaves` is capped). */
  met_count: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface RunLeaf {
  path: string;
  target: number;
  kept: number;
  state: "open" | "met" | "abandoned";
  reason: string | null;
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
