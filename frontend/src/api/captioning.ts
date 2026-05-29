import client from "./client";

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
  save_backup?: boolean;
  rename_on_caption?: boolean;
  min_aesthetic_score?: number;
  exclude_flags?: string[];
  wd14_threshold?: number;
  label?: string;
}

export interface PipelineStep {
  model: string;
  style: string;
  custom_prompt: string;
  overwrite: boolean;
  append_tags: boolean;
  strip_refusals: boolean;
  wd14_threshold: number;
  target_width?: number | null;
  target_height?: number | null;
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
    client.get<{ local_models: unknown[]; ollama_models: unknown[]; wd14_models: unknown[]; openai_compat_models: unknown[] }>("/captioning/models").then((r) => r.data),
  styles: () => client.get("/captioning/styles").then((r) => r.data),
  run: (params: CaptionRunParams) =>
    client.post<{ job_id: string; total: number }>("/captioning/run", params).then((r) => r.data),
  pipeline: (params: CaptionPipelineParams) =>
    client.post<{ job_id: string; total: number }>("/captioning/pipeline", params).then((r) => r.data),
  unloadModel: (model_id: string) => client.delete(`/captioning/model/${model_id}/unload`),
};
