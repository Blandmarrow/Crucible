import client from "./client";

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
    label?: string;
  }) =>
    client.post<{ job_id: string; total: number }>("/quality/score", params).then((r) => r.data),

  duplicates: (dataset_id: string) =>
    client
      .get<{ groups: Array<Array<{ id: string; filename: string; aesthetic_score: number | null; updated_at: string }>> }>(
        `/quality/duplicates/${dataset_id}`
      )
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
    reference_image_ids: string[];
    reference_embeddings?: string[];
    embedding_type?: "clip" | "dino" | "combined" | "dino_all_layers" | "combined_all_layers";
    dino_layer?: number;
  }) =>
    client.post<{ updated: number; skipped: number }>("/quality/style-similarity", params).then((r) => r.data),
};
