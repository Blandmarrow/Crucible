import client from "./client";

export const inpaintApi = {
  /** Start a `batch_inpaint` job: paint existing detections out of their images.
   *
   * `labels` filters which *detections* form the paint mask; `label` overrides
   * the job's display name. The names match the backend request model and every
   * sibling endpoint — do not swap them.
   */
  run: (params: {
    dataset_id: string;
    image_ids?: string[];
    subfolder?: string;
    quality_flags?: string[];
    labels?: string[];
    dilate_px?: number;
    replace?: boolean;
    dest_subfolder?: string;
    label?: string;
  }) =>
    client
      .post<{ job_id: string; total: number; skipped: number }>("/inpaint/run", params)
      .then((r) => r.data),
};
