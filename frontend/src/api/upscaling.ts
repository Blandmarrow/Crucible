import client from "./client";

export interface UpscaleModelInfo {
  name: string;
  path: string;
  scale: number | null;
}

/** Dropdown label for an upscale model: "name (4×)", or "name (1× restore)" for
 *  a 1x restoration model (denoise / deblur / JPEG artifacts). Bare name when
 *  the filename heuristic found no scale. */
export function upscaleModelLabel(m: UpscaleModelInfo): string {
  if (!m.scale) return m.name;
  return `${m.name} (${m.scale}×${m.scale === 1 ? " restore" : ""})`;
}

export interface UpscaleRunRequest {
  dataset_id: string;
  image_ids?: string[];
  model_path: string;
  replace: boolean;
  target_width?: number | null;
  target_height?: number | null;
  subfolder?: string;
  label?: string;
  quality_flags?: string[];
}

export const upscalingApi = {
  models: () =>
    client.get<UpscaleModelInfo[]>("/upscaling/models").then((r) => r.data),

  run: (req: UpscaleRunRequest) =>
    client
      .post<{ job_id: string; total: number }>("/upscaling/run", req)
      .then((r) => r.data),
};
