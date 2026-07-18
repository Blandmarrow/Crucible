import client from "./client";
import type { Detection } from "../types";

export type { Detection };

export const detectionApi = {
  run: (params: {
    dataset_id: string;
    image_ids?: string[];
    model: string;
    task: string;
    custom_prompt?: string;
    use_caption_as_prompt?: boolean;
    overwrite?: boolean;
    label?: string;
    min_prob?: number;
    point_prompts?: number[][];
    point_labels?: number[];
  }) =>
    client
      .post<{ job_id: string | null; total: number }>("/detection/run", params)
      .then((r) => r.data),

  getForImage: (image_id: string) =>
    client.get<Detection[]>(`/detection/image/${image_id}`).then((r) => r.data),

  labels: (dataset_id: string) =>
    client
      .get<{ label: string; image_count: number }[]>(`/detection/labels/${dataset_id}`)
      .then((r) => r.data),

  cropToDetection: (params: {
    dataset_id: string;
    image_ids?: string[];
    subfolder?: string;
    quality_flags?: string[];
    labels?: string[];
    mode: "union" | "largest";
    padding_pct: number;
    target_ar?: number | null;
    replace: boolean;
    label?: string;
  }) =>
    client
      .post<{ job_id: string; total: number; skipped: number }>("/detection/crop", params)
      .then((r) => r.data),
};
