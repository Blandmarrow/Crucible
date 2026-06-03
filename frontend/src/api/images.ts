import client from "./client";
import type { ImageDetail, ImageListItem } from "../types";

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
  detection_label?: string;
  caption_words_min?: number;
  caption_words_max?: number;
  caption_tokens_min?: number;
  caption_tokens_max?: number;
}

export const imagesApi = {
  list: (params: ImageListParams) =>
    client.get<ImageListItem[]>("/images/", { params }).then((r) => r.data),
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
  upload: (dataset_id: string, files: File[], subfolder = "") => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    const qs = subfolder ? `&subfolder=${encodeURIComponent(subfolder)}` : "";
    return client.post(`/images/upload?dataset_id=${dataset_id}${qs}`, form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },
  uploadSingle: (dataset_id: string, file: File, subfolder = "") => {
    const form = new FormData();
    form.append("files", file);
    const qs = subfolder ? `&subfolder=${encodeURIComponent(subfolder)}` : "";
    return client.post(`/images/upload?dataset_id=${dataset_id}${qs}`, form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
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
  bulkCount: (datasetId: string, params: BulkCountParams) =>
    client.post<{ count: number }>("/images/bulk-count", {
      dataset_id: datasetId,
      image_ids: params.imageIds ?? null,
      quality_flags: params.qualityFlags ?? null,
      subfolder: params.subfolder ?? null,
      include_flagged: params.includeFlagged ?? false,
    }).then((r) => r.data),
};
