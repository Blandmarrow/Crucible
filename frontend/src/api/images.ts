import client from "./client";
import type { ImageDetail, ImageListItem } from "../types";

export interface UploadResult {
  added: number;
  files: string[];
  /** Videos land in the `videos` table, not `images`. */
  videos_added: number;
  videos: string[];
  /** Files the server declined, with a human-readable reason. Returned with a
   *  201 rather than an error status, because one bad file in a multi-file
   *  upload must not fail the rest. */
  skipped: { file: string; reason: string }[];
}

export interface BatchMoveSubfolderResult {
  moved: number;
  subfolder: string;
}

export interface BatchMoveDatasetResult {
  moved: number;
  target_dataset_id: string;
}

export interface BatchCopyDatasetResult {
  copied: number;
  target_dataset_id: string;
}

export interface BulkFilterParams {
  imageIds?: string[];
  qualityFlags?: string[];
  subfolder?: string;
}

export interface BulkRenameParams extends BulkFilterParams {
  newStem: string;
  sortBySortOrder?: boolean;
}

export interface ReorderUpdate {
  id: string;
  sort_order: number;
}

export type BulkDeleteParams = BulkFilterParams;
export interface BulkCountParams extends BulkFilterParams {
  includeFlagged?: boolean;
}
export interface BulkThumbnailParams extends BulkFilterParams {
  includeFlagged?: boolean;
  label?: string;
}

export interface ImageListParams {
  dataset_id: string;
  page?: number;
  limit?: number;
  sort?: string;
  order?: string;
  captioned?: boolean;
  search?: string;
  min_score?: number;
  max_score?: number;
  score_field?: string;
  score_is_null?: boolean;
  quality_flag?: string;
  file_size_min?: number;
  file_size_max?: number;
  mp_min?: number;
  mp_max?: number;
  ar_min?: number;
  ar_max?: number;
  format_filter?: string;
  score_filters?: string;
  subfolder?: string;
  /** Frame lineage: only images extracted from this video, in whatever subfolder
   *  curation has since moved them to. */
  source_video_id?: string;
  detection_label?: string;
  detection_label_exact?: string;
  detection_score_min?: number;
  detection_score_max?: number;
  detection_score_null?: boolean;
  mask_coverage_min?: number;
  mask_coverage_max?: number;
  detection_count_min?: number;
  detection_count_max?: number;
  caption_words_min?: number;
  caption_words_max?: number;
  caption_tokens_min?: number;
  caption_tokens_max?: number;
  /** JSON array of effective license ids, e.g. `JSON.stringify(["CC-BY-4.0"])`.
   *  Not comma-separated: an `other:<free text>` id may contain commas. */
  license_filter?: string;
  /** true = only images with no license at either level. */
  license_missing?: boolean;
}

/** What "the current view" means, minus which slice of it is on screen and minus
 *  how it is ordered — the mirror of the backend's `ImageFilterParams`, which
 *  excludes `sort`/`order` on purpose because `/count` must not care how the
 *  matches are ordered. `count` takes this so it cannot describe a different set
 *  of images than the grid does. */
export type ImageFilterParams = Omit<ImageListParams, "page" | "limit" | "sort" | "order">;

/** The filters plus the grid's ordering — the mirror of the backend's
 *  `ImageIdsParams`, which subclasses `ImageFilterParams` for the same reason:
 *  a truncated select-all has to be the first N *in the order on screen*. */
export type ImageIdsParams = ImageFilterParams & Pick<ImageListParams, "sort" | "order">;

/** `GET /images/ids`. `count` is the true total even when the response was
 *  trimmed, so a truncated selection can say "the first 20,000 of 84,113". */
export interface ImageIdsResult {
  ids: string[];
  count: number;
  truncated: boolean;
}

/**
 * One provenance field in an edit request. undefined/null = leave unchanged,
 * "" = clear so the dataset default applies, anything else = set that value.
 */
export interface ProvenanceEdit {
  source_name?: string | null;
  source_url?: string | null;
  license?: string | null;
  attribution?: string | null;
}

export interface BulkProvenanceParams extends BulkFilterParams, ProvenanceEdit {}

export const imagesApi = {
  list: (params: ImageListParams) =>
    client.get<ImageListItem[]>("/images/", { params }).then((r) => r.data),
  /** How many images match these filters — the pagination row's total and the
   *  number in the select-all offer. */
  count: (params: ImageFilterParams) =>
    client.get<{ count: number }>("/images/count", { params }).then((r) => r.data),
  /** Every matching id, in the order the grid is showing them, capped server-side
   *  (`truncated`). Feeds "select all N matching filters". */
  listIds: (params: ImageIdsParams) =>
    client.get<ImageIdsResult>("/images/ids", { params }).then((r) => r.data),
  get: (id: string) => client.get<ImageDetail>(`/images/${id}`).then((r) => r.data),
  delete: (id: string) => client.delete(`/images/${id}`),
  batchDelete: (image_ids: string[]) =>
    client.delete("/images/batch/delete", { data: image_ids }),
  fileUrl: (id: string) => `/api/v1/images/${id}/file`,
  fileUrlVersioned: (id: string, updatedAt: string) =>
    `/api/v1/images/${id}/file?v=${Date.parse(updatedAt)}`,
  thumbnailUrl: (id: string) => `/api/v1/images/${id}/thumbnail`,
  thumbnailUrlVersioned: (id: string, updatedAt: string) =>
    `/api/v1/images/${id}/thumbnail?v=${Date.parse(updatedAt)}`,
  /** Upload several files in one request. Same contract as `uploadSingle`: videos
   *  are routed to Video rows, and anything declined comes back in `skipped` with
   *  a reason rather than as an HTTP error — report it with
   *  `utils/uploadToast.ts`, or a rejected upload reads as a successful one. */
  upload: (dataset_id: string, files: File[], subfolder = ""): Promise<UploadResult> => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    const qs = subfolder ? `&subfolder=${encodeURIComponent(subfolder)}` : "";
    return client.post(`/images/upload?dataset_id=${dataset_id}${qs}`, form, {
      headers: { "Content-Type": "multipart/form-data" },
    }).then((r) => r.data);
  },
  /** Upload one file. A video is routed to a Video row rather than an Image;
   *  a file the server will not or cannot ingest comes back in `skipped` with a
   *  reason, NOT as an HTTP error — check it, or a rejected upload reads as a
   *  successful one. */
  uploadSingle: (dataset_id: string, file: File, subfolder = ""): Promise<UploadResult> => {
    const form = new FormData();
    form.append("files", file);
    const qs = subfolder ? `&subfolder=${encodeURIComponent(subfolder)}` : "";
    return client.post(`/images/upload?dataset_id=${dataset_id}${qs}`, form, {
      headers: { "Content-Type": "multipart/form-data" },
    }).then((r) => r.data);
  },
  resize: (id: string, opts: { width?: number; height?: number; scale?: number; maintain_ar?: boolean }) =>
    client.post(`/images/${id}/resize`, opts).then((r) => r.data),
  crop: (
    id: string,
    box: {
      x: number; y: number; width: number; height: number;
      output_width?: number; output_height?: number;
      replace?: boolean;
      upscale_model?: string;
      upscale_target_width?: number;
      upscale_target_height?: number;
    },
  ) =>
    client
      .post<{ id: string; filename: string; width: number; height: number } | { job_id: string }>(`/images/${id}/crop`, box)
      .then((r) => r.data),
  batchResize: (image_ids: string[], opts: object) =>
    client.post<{ job_id: string }>("/images/batch/resize", { image_ids, ...opts }).then((r) => r.data),
  batchCrop: (image_ids: string[], target_ar: number, strategy = "center") =>
    client.post<{ job_id: string }>("/images/batch/crop", { image_ids, target_ar, strategy }).then((r) => r.data),
  batchMoveSubfolder: (image_ids: string[], subfolder: string, rename_on_move = true) =>
    client.post<BatchMoveSubfolderResult>("/images/batch/move-subfolder", { image_ids, subfolder, rename_on_move }).then((r) => r.data),
  batchMoveDataset: (
    params: { image_ids?: string[]; source_dataset_id?: string; source_subfolder?: string },
    target_dataset_id: string,
    subfolder: string,
  ) =>
    client.post<BatchMoveDatasetResult>("/images/batch/move-dataset", { ...params, target_dataset_id, subfolder }).then((r) => r.data),
  batchCopyDataset: (
    params: { image_ids?: string[]; source_dataset_id?: string; source_subfolder?: string },
    target_dataset_id: string,
    subfolder: string,
  ) =>
    client.post<BatchCopyDatasetResult>("/images/batch/copy-dataset", { ...params, target_dataset_id, subfolder }).then((r) => r.data),
  renameImage: (id: string, newStem: string) =>
    client.patch<{ filename: string }>(`/images/${id}/rename`, { new_stem: newStem }).then((r) => r.data),
  bulkRename: (datasetId: string, params: BulkRenameParams) =>
    client.post<{ affected: number }>("/images/bulk-rename", {
      dataset_id: datasetId,
      new_stem: params.newStem,
      image_ids: params.imageIds ?? null,
      quality_flags: params.qualityFlags ?? null,
      subfolder: params.subfolder ?? null,
      sort_by_sort_order: params.sortBySortOrder ?? false,
    }).then((r) => r.data),
  reorderImages: (datasetId: string, updates: ReorderUpdate[]) =>
    client.patch<{ updated: number }>("/images/batch/reorder", {
      dataset_id: datasetId,
      updates,
    }).then((r) => r.data),
  bulkDelete: (datasetId: string, params: BulkDeleteParams) =>
    client.post<{ deleted: number }>("/images/bulk-delete", {
      dataset_id: datasetId,
      image_ids: params.imageIds ?? null,
      quality_flags: params.qualityFlags ?? null,
      subfolder: params.subfolder ?? null,
    }).then((r) => r.data),
  setProvenance: (id: string, edit: ProvenanceEdit) =>
    client.patch<ImageDetail>(`/images/${id}/provenance`, edit).then((r) => r.data),
  bulkProvenance: (datasetId: string, params: BulkProvenanceParams) =>
    client.post<{ updated: number }>("/images/bulk-provenance", {
      dataset_id: datasetId,
      image_ids: params.imageIds ?? null,
      quality_flags: params.qualityFlags ?? null,
      subfolder: params.subfolder ?? null,
      source_name: params.source_name ?? null,
      source_url: params.source_url ?? null,
      license: params.license ?? null,
      attribution: params.attribution ?? null,
    }).then((r) => r.data),
  /** Re-cut the thumbnails for a scope — the repair for a run that reported
   *  `thumbnails_stale`. Returns a `regenerate_thumbnails` job; 507 when the
   *  volume is too full to write them. */
  bulkThumbnails: (datasetId: string, params: BulkThumbnailParams) =>
    client.post<{ job_id: string; total: number }>("/images/bulk-thumbnails", {
      dataset_id: datasetId,
      image_ids: params.imageIds ?? null,
      quality_flags: params.qualityFlags ?? null,
      subfolder: params.subfolder ?? null,
      include_flagged: params.includeFlagged ?? false,
      label: params.label ?? null,
    }).then((r) => r.data),
  bulkCount: (datasetId: string, params: BulkCountParams) =>
    client.post<{ count: number }>("/images/bulk-count", {
      dataset_id: datasetId,
      image_ids: params.imageIds ?? null,
      quality_flags: params.qualityFlags ?? null,
      subfolder: params.subfolder ?? null,
      include_flagged: params.includeFlagged ?? false,
    }).then((r) => r.data),
};
