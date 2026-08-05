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
  /** Drop CC BY-ND and friends — an export ships resized/cropped copies. */
  exclude_no_derivatives?: boolean;
  /** Label ids the export is about. Like `subfolders`, this **narrows** the
   *  export rather than joining the exclusion tally: it says which images the
   *  export covers, not which of a fixed population to drop. So `image_count`
   *  shrinks and no "excluded by label" counter exists. Named `label_filter`,
   *  not `labels` — `label` below is the job's display name. */
  label_filter?: string[] | null;
  label_match?: "any" | "all";
  /** true = only images carrying no label at all. */
  label_missing?: boolean;
  label?: string;
}

/** The filter params `GET /export/preview/{id}` understands. Named rather than
 *  inlined so `ExportPage` can hold exactly this shape in state — the preview and
 *  the export POST bodies must be built from one encoding of the label trio, not
 *  two (see `labelParams` there). */
export interface ExportPreviewFilters {
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
  exclude_no_derivatives?: boolean;
  label_filter?: string[] | null;
  label_match?: "any" | "all";
  label_missing?: boolean;
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
  /** How many of those actually ship under the *current* filters — every filter,
   *  not just the license ones. The client cannot derive this. */
  unlicensed_will_export: number;
  /** Shipping images whose effective license is `other:` free text. "Exclude
   *  no-derivatives" cannot classify those, so it lets them through — unlike the
   *  commercial filter, which drops anything it cannot classify. */
  freetext_will_export: number;
  /** Images whose pixels were rewritten in place after they were scored, over the
   *  whole dataset scope. Their flags were computed against pixels that are gone,
   *  so `exclude_flags` may be dropping or keeping the wrong images. */
  stale_scores_count: number;
  /** How many of those survive the *current* filters and actually ship. */
  stale_scores_will_export: number;
  /** Which models produced the aesthetic scores in scope, as `{marker: count}` —
   *  `{}` when nothing is scored. Two or more keys means two non-comparable
   *  scales in one column, which `aesthetic_min` cannot see. A dict rather than
   *  a bool because the skew ("1,204 by LAION, 766 by V2.5") is what says
   *  whether the threshold is over- or under-including. */
  aesthetic_models: Record<string, number>;
  /** The same breakdown restricted to what actually ships under the current
   *  filters. */
  aesthetic_models_will_export: Record<string, number>;
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

  preview: (dataset_id: string, filters?: ExportPreviewFilters) =>
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
          ...(filters?.exclude_no_derivatives && { exclude_no_derivatives: true }),
          // JSON array, the same encoding as license_filter above. Which of the
          // three are present at all is decided upstream by `labelParams`, the one
          // encoder the POST bodies share — this only turns them into query params.
          ...(filters?.label_filter?.length && { label_filter: JSON.stringify(filters.label_filter) }),
          ...(filters?.label_match === "all" && { label_match: "all" }),
          ...(filters?.label_missing && { label_missing: true }),
        },
      })
      .then((r) => r.data),
};
