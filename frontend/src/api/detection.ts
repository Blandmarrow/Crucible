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
};
