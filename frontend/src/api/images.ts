import client from "./client";
import type { ImageDetail, ImageListItem } from "../types";

export interface BatchMoveSubfolderResult {
  moved: number;
  subfolder: string;
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
}

export const imagesApi = {
  list: (params: ImageListParams) =>
    client.get<ImageListItem[]>("/images/", { params }).then((r) => r.data),
  get: (id: string) => client.get<ImageDetail>(`/images/${id}`).then((r) => r.data),
  delete: (id: string) => client.delete(`/images/${id}`),
  batchDelete: (image_ids: string[]) =>
    client.delete("/images/batch/delete", { data: image_ids }),
  fileUrl: (id: string) => `/api/v1/images/${id}/file`,
  thumbnailUrl: (id: string) => `/api/v1/images/${id}/thumbnail`,
  upload: (dataset_id: string, files: File[], subfolder = "") => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    const qs = subfolder ? `&subfolder=${encodeURIComponent(subfolder)}` : "";
    return client.post(`/images/upload?dataset_id=${dataset_id}${qs}`, form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },
  resize: (id: string, opts: { width?: number; height?: number; scale?: number; maintain_ar?: boolean }) =>
    client.post(`/images/${id}/resize`, opts).then((r) => r.data),
  crop: (id: string, box: { x: number; y: number; width: number; height: number }) =>
    client.post(`/images/${id}/crop`, box).then((r) => r.data),
  batchResize: (image_ids: string[], opts: object) =>
    client.post<{ job_id: string }>("/images/batch/resize", { image_ids, ...opts }).then((r) => r.data),
  batchCrop: (image_ids: string[], target_ar: number, strategy = "center") =>
    client.post<{ job_id: string }>("/images/batch/crop", { image_ids, target_ar, strategy }).then((r) => r.data),
  batchMoveSubfolder: (image_ids: string[], subfolder: string) =>
    client.post<BatchMoveSubfolderResult>("/images/batch/move-subfolder", { image_ids, subfolder }).then((r) => r.data),
};
