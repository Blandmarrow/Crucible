import client from "./client";
import type { Dataset, DatasetStats, SubfolderInfo, TagCooccurrence } from "../types";

export interface ScoreValues {
  aesthetic_score: number[];
  blur_score: number[];
  noise_score: number[];
  uniformity_score: number[];
  watermark_score: number[];
  color_score: number[];
  saturation_score: number[];
  style_similarity_score: number[];
  megapixels: number[];
  file_size_mb: number[];
  caption_words: number[];
  caption_tokens: number[];
}

export const datasetsApi = {
  list: () => client.get<Dataset[]>("/datasets/").then((r) => r.data),
  get: (id: string) => client.get<Dataset>(`/datasets/${id}`).then((r) => r.data),
  create: (name: string, description = "", category = "") =>
    client.post<Dataset>("/datasets/", { name, description, category }).then((r) => r.data),
  update: (id: string, data: { name?: string; description?: string; category?: string }) =>
    client.patch<Dataset>(`/datasets/${id}`, data).then((r) => r.data),
  delete: (id: string) => client.delete(`/datasets/${id}`),
  duplicate: (id: string, newName: string, sourceVersionId?: string) =>
    client
      .post<{ job_id: string }>(`/datasets/${id}/duplicate`, {
        new_name: newName,
        source_version_id: sourceVersionId ?? null,
      })
      .then((r) => r.data),
  importFolder: (id: string, folder_path: string, subfolder = "", preserve_structure = false) =>
    client.post<{ job_id: string }>(`/datasets/${id}/import`, { folder_path, subfolder, preserve_structure }).then((r) => r.data),
  subfolders: (id: string) =>
    client.get<SubfolderInfo[]>(`/datasets/${id}/subfolders`).then((r) => r.data),
  createSubfolder: (id: string, path: string) =>
    client.post<SubfolderInfo>(`/datasets/${id}/subfolders`, { path }).then((r) => r.data),
  deleteSubfolder: (id: string, path: string) =>
    client.delete(`/datasets/${id}/subfolders`, { params: { path } }).then((r) => r.data),
  refreshStats: (id: string) => client.post(`/datasets/${id}/refresh-stats`),
  stats: (id: string, subfolder?: string) =>
    client.get<DatasetStats>(`/datasets/${id}/stats`, { params: { subfolder } }).then((r) => r.data),
  tagCooccurrence: (id: string, limit = 15, subfolder?: string) =>
    client.get<TagCooccurrence>(`/datasets/${id}/tag-cooccurrence`, { params: { limit, subfolder } }).then((r) => r.data),
  scoreValues: (id: string, subfolder?: string) =>
    client.get<ScoreValues>(`/datasets/${id}/score-values`, { params: { subfolder } }).then((r) => r.data),
};
