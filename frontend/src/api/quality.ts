import client from "./client";

export interface DuplicateImage {
  id: string;
  filename: string;
  aesthetic_score: number | null;
  updated_at: string;
  created_at: string;
  /** True for the one image in the group the scan kept — the group's first
   *  member, and the only one no default action deletes. */
  kept: boolean;
}

export type DuplicateGroup = DuplicateImage[];

export const qualityApi = {
  score: (params: {
    dataset_id: string;
    subfolder?: string;
    image_ids?: string[];
    run_aesthetic: boolean;
    run_technical: boolean;
    run_watermark?: boolean;
    run_embeddings?: boolean;
    run_dino?: boolean;
    run_dino_layers?: boolean;
    run_nsfw?: boolean;
    label?: string;
  }) =>
    client.post<{ job_id: string; total: number }>("/quality/score", params).then((r) => r.data),

  /** Groups are led by the image the scan kept (`kept: true`); the rest are the
   *  removable copies, in `created_at` order. See `get_duplicates`. */
  duplicates: (dataset_id: string) =>
    client
      .get<{ groups: DuplicateGroup[] }>(`/quality/duplicates/${dataset_id}`)
      .then((r) => r.data),

  resolveDuplicates: (keep_ids: string[], delete_ids: string[]) =>
    client.post("/quality/duplicates/resolve", { keep_ids, delete_ids }),

  embedReferences: (files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    return client
      .post<{ embeddings: string[] }>("/quality/embed-references", form, {
        headers: { "Content-Type": "multipart/form-data" },
      })
      .then((r) => r.data);
  },

  styleSimilarity: (params: {
    dataset_id: string;
    image_ids?: string[];
    reference_image_ids: string[];
    reference_embeddings?: string[];
    embedding_type?: "clip" | "dino" | "combined" | "dino_all_layers" | "combined_all_layers";
    dino_layer?: number;
  }) =>
    client.post<{ updated: number; skipped: number }>("/quality/style-similarity", params).then((r) => r.data),
};
