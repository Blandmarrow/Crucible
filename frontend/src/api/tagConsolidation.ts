import client from "./client";

export interface ClusterVariant {
  tag: string;
  count: number;
}

export interface TagCluster {
  canonical: string;
  variants: ClusterVariant[];
  min_sim: number;
}

export interface AnalyzeResult {
  clusters: TagCluster[];
  vocab_size: number;
  image_count: number;
  truncated: boolean;
}

export const tagConsolidationApi = {
  analyze: (datasetId: string, params: { threshold: number; subfolder?: string }) =>
    client
      .post<{ job_id: string | null; total?: number; message?: string }>(
        `/tag-consolidation/dataset/${datasetId}/analyze`,
        params,
      )
      .then((r) => r.data),
  apply: (datasetId: string, params: { mapping: Record<string, string>; subfolder?: string }) =>
    client
      .post<{ job_id: string | null; total?: number; message?: string }>(
        `/tag-consolidation/dataset/${datasetId}/apply`,
        params,
      )
      .then((r) => r.data),
  subsume: (datasetId: string, params: { subfolder?: string; dry_run: boolean; image_ids?: string[] }) =>
    client
      .post<{ affected: number; skipped: number }>(
        `/tag-consolidation/dataset/${datasetId}/subsume`,
        params,
      )
      .then((r) => r.data),
};
