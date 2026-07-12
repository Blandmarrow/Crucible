import client from "./client";

export interface Thresholds {
  blur_threshold: number;
  noise_threshold: number;
  uniformity_threshold: number;
  duplicate_threshold: number;
  watermark_threshold: number;
  nsfw_threshold: number;
  gdino_threshold: number;
  versioning_mode: "off" | "manual" | "auto";
  auto_rescan_on_open: boolean;
  comfyui_url: string;
  comfy_workflow_dir: string;
}

export const settingsApi = {
  getThresholds: () =>
    client.get<Thresholds>("/settings/thresholds").then((r) => r.data),
  updateThresholds: (data: Partial<Thresholds>) =>
    client.patch<Thresholds>("/settings/thresholds", data).then((r) => r.data),
};
