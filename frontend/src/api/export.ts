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
  /** Effective license ids to keep; null/empty = no restriction. "" matches images with no license. */
  license_filter?: string[] | null;
  /** Keep only images whose license is *known* to permit commercial use. */
  commercial_only?: boolean;
  /** Drop images with no effective license. Not expressible via license_filter,
   *  which is an allowlist of known ids and would also drop `other:` values. */
  exclude_unlicensed?: boolean;
  label?: string;
}

export interface ExportPreview {
  image_count: number;
  will_export: number;
  captioned_count: number;
  excluded_low_aesthetic: number;
  excluded_uncaptioned: number;
  excluded_flagged: number;
  excluded_style_sim: number;
  excluded_license: number;
  unlicensed_count: number;
  sample_files: { image: string; caption_preview: string }[];
  images_without_detections?: number;
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
      license_filter?: string[] | null;
      commercial_only?: boolean;
      exclude_unlicensed?: boolean;
    },
  ) =>
    client
      .get<ExportPreview>(`/export/preview/${dataset_id}`, {
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
          // JSON array — an other:<free text> license id may contain commas
          ...(filters?.license_filter?.length && { license_filter: JSON.stringify(filters.license_filter) }),
          ...(filters?.commercial_only && { commercial_only: true }),
          ...(filters?.exclude_unlicensed && { exclude_unlicensed: true }),
        },
      })
      .then((r) => r.data),
};
