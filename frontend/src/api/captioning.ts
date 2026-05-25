import client from "./client";

export const captioningApi = {
  models: () =>
    client.get<{ local_models: unknown[]; ollama_models: unknown[] }>("/captioning/models").then((r) => r.data),
  styles: () => client.get("/captioning/styles").then((r) => r.data),
  run: (params: {
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
  }) => client.post<{ job_id: string; total: number }>("/captioning/run", params).then((r) => r.data),
  unloadModel: (model_id: string) => client.delete(`/captioning/model/${model_id}/unload`),
};
