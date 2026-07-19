import client from "./client";

interface ExportFilters {
  caption_format?: string;
  resize_to?: number | null;
  aesthetic_min?: number | null;
  captioned_only?: boolean;
  exclude_flags?: string;
  style_sim_min?: number | null;
  subfolders?: string[] | null;
  strip_metadata?: boolean;
  captions_only?: boolean;
  export_masks?: boolean;
  mask_labels?: string[] | null;
  mask_exclude_labels?: string[] | null;
  mask_invert?: boolean;
  mask_missing?: "white" | "skip";
  label?: string;
}

export const exportApi = {
  kohya: (params: {
    dataset_id: string;
    output_dir: string;
    n_repeats?: number;
    concept_token?: string;
    image_ids?: string[];
    output_format?: string;
  } & ExportFilters) =>
    client.post<{ job_id: string }>("/export/kohya", params).then((r) => r.data),

  aitoolkit: (params: {
    dataset_id: string;
    output_dir: string;
    concept_name?: string;
    image_ids?: string[];
    output_format?: string;
  } & ExportFilters) =>
    client.post<{ job_id: string }>("/export/aitoolkit", params).then((r) => r.data),

  plain: (params: {
    dataset_id: string;
    output_dir: string;
    image_ids?: string[];
    output_format?: string;
  } & ExportFilters) =>
    client.post<{ job_id: string }>("/export/plain", params).then((r) => r.data),

  preview: (
    dataset_id: string,
    filters?: {
      aesthetic_min?: number | null;
      captioned_only?: boolean;
      exclude_flags?: string;
      style_sim_min?: number | null;
      subfolders?: string[] | null;
      export_masks?: boolean;
      mask_labels?: string[] | null;
      mask_exclude_labels?: string[] | null;
      mask_missing?: "white" | "skip";
    },
  ) =>
    client
      .get(`/export/preview/${dataset_id}`, {
        params: {
          ...(filters?.aesthetic_min != null && { aesthetic_min: filters.aesthetic_min }),
          ...(filters?.captioned_only && { captioned_only: true }),
          ...(filters?.exclude_flags && { exclude_flags: filters.exclude_flags }),
          ...(filters?.style_sim_min != null && { style_sim_min: filters.style_sim_min }),
          ...(filters?.subfolders?.length && { subfolders: filters.subfolders.join(",") }),
          ...(filters?.export_masks && { export_masks: true }),
          // JSON array — detection labels are free text and may contain commas
          ...(filters?.export_masks && filters?.mask_labels?.length && { mask_labels: JSON.stringify(filters.mask_labels) }),
          ...(filters?.export_masks && filters?.mask_exclude_labels?.length && { mask_exclude_labels: JSON.stringify(filters.mask_exclude_labels) }),
          ...(filters?.export_masks && filters?.mask_missing === "skip" && { mask_missing: "skip" }),
        },
      })
      .then((r) => r.data),
};
