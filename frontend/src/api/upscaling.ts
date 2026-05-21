import client from "./client";

export interface UpscaleModelInfo {
  name: string;
  path: string;
  scale: number | null;
}

export interface UpscaleRunRequest {
  dataset_id: string;
  image_ids?: string[];
  model_path: string;
  replace: boolean;
  target_width?: number | null;
  target_height?: number | null;
}

export const upscalingApi = {
  models: () =>
    client.get<UpscaleModelInfo[]>("/upscaling/models").then((r) => r.data),

  run: (req: UpscaleRunRequest) =>
    client
      .post<{ job_id: string; total: number }>("/upscaling/run", req)
      .then((r) => r.data),
};
