import client from "./client";
import type { ModelInfo, OllamaModel, Wd14ModelInfo } from "../types";
import type { ProviderOut } from "./providers";

/** `GET /captioning/models`. `local_models` is filtered to captioners backend-side —
 * the registry's scorers, detectors and embedders never appear here. */
export interface CaptioningModels {
  local_models: ModelInfo[];
  ollama_models: OllamaModel[];
  wd14_models: Wd14ModelInfo[];
  openai_compat_models: ProviderOut[];
}

export type DelimiterMode = "overwrite" | "append" | "prepend";

export const DELIMITER_PRESETS: { label: string; value: string }[] = [
  { label: "Comma", value: "," },
  { label: "Period", value: "." },
  { label: "Space", value: " " },
  { label: "Newline", value: "\n" },
];

export interface CaptionRunParams {
  dataset_id: string;
  image_ids?: string[];
  subfolder?: string;
  model: string;
  style: string;
  overwrite: boolean;
  custom_prompt?: string;
  target_width?: number;
  target_height?: number;
  append_tags?: boolean;
  strip_refusals?: boolean;
  strip_thinking?: boolean;
  strip_underscores?: boolean;
  strip_hedges?: boolean;
  dedupe_tags?: boolean;
  save_backup?: boolean;
  rename_on_caption?: boolean;
  min_aesthetic_score?: number;
  exclude_flags?: string[];
  wd14_threshold?: number;
  label?: string;
  delimiter_mode?: DelimiterMode;
  delimiter?: string;
}

export interface PipelineStep {
  model: string;
  style: string;
  custom_prompt: string;
  overwrite: boolean;
  append_tags: boolean;
  strip_refusals: boolean;
  strip_thinking?: boolean;
  strip_underscores?: boolean;
  strip_hedges?: boolean;
  dedupe_tags?: boolean;
  wd14_threshold: number;
  target_width?: number | null;
  target_height?: number | null;
  delimiter_mode?: DelimiterMode;
  delimiter?: string;
}

export interface CaptionPipelineParams {
  dataset_id: string;
  image_ids?: string[];
  subfolder?: string;
  steps: PipelineStep[];
  save_backup?: boolean;
  rename_on_caption?: boolean;
  min_aesthetic_score?: number;
  exclude_flags?: string[];
  label?: string;
}

export const captioningApi = {
  models: () =>
    client.get<CaptioningModels>("/captioning/models").then((r) => r.data),
  run: (params: CaptionRunParams) =>
    client.post<{ job_id: string; total: number }>("/captioning/run", params).then((r) => r.data),
  pipeline: (params: CaptionPipelineParams) =>
    client.post<{ job_id: string; total: number }>("/captioning/pipeline", params).then((r) => r.data),
  unloadModel: (model_id: string) => client.delete(`/captioning/model/${model_id}/unload`),
};
