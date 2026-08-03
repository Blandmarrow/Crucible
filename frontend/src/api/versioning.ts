import client from "./client";
import type { Branch, Version, VersionDiff } from "../types";

export interface ListVersionsParams {
  branchId?: string;
  limit?: number;
  offset?: number;
  search?: string;
  createdAfter?: string;
  createdBefore?: string;
}

export interface VersionUpdateRequest {
  is_pinned?: boolean;
}

export interface SnapshotCreateRequest {
  name?: string;
  description?: string;
  branch_id?: string;
}

export interface RestoreRequest {
  handle_extra_images: "keep" | "remove";
  pre_restore_snapshot: boolean;
}

export interface RatingImpact {
  /** Live images whose rating the restore would change, in either direction. */
  will_change: number;
  /** The subset it would *clear* — rated after the snapshot was taken. */
  will_clear: number;
  /** Rated images the version does not contain. Under `handle_extra_images:
   *  "remove"` these are **deleted**, not reverted, so the modal reports them
   *  separately: which number applies depends on the mode selected. */
  extras_rated: number;
}

export interface RestoreSummary {
  files_restored: number;
  files_unavailable: number;
  files_failed: number;
  images_re_created: number;
  images_removed: number;
  pre_restore_version_id: string | null;
}

export const versioningApi = {
  listBranches: (datasetId: string) =>
    client.get<Branch[]>(`/datasets/${datasetId}/versions/branches`).then((r) => r.data),

  createBranch: (datasetId: string, name: string, fromVersionId?: string, includeSnapshot = true) =>
    client
      .post<Branch | { job_id: string }>(`/datasets/${datasetId}/versions/branches`, {
        name,
        from_version_id: fromVersionId,
        include_snapshot: includeSnapshot,
      })
      .then((r) => r.data),

  checkoutBranch: (datasetId: string, branchId: string, preRestoreSnapshot = true) =>
    client
      .post<{ job_id: string }>(`/datasets/${datasetId}/versions/branches/${branchId}/checkout`, {
        pre_restore_snapshot: preRestoreSnapshot,
      })
      .then((r) => r.data),

  listVersions: (datasetId: string, params: ListVersionsParams = {}) =>
    client
      .get<Version[]>(`/datasets/${datasetId}/versions`, {
        params: {
          branch_id: params.branchId,
          limit: params.limit ?? 50,
          offset: params.offset ?? 0,
          search: params.search || undefined,
          created_after: params.createdAfter || undefined,
          created_before: params.createdBefore || undefined,
        },
      })
      .then((r) => r.data),

  createSnapshot: (datasetId: string, body: SnapshotCreateRequest) =>
    client
      .post<Version | { job_id: string }>(`/datasets/${datasetId}/versions`, body)
      .then((r) => r.data),

  getVersion: (datasetId: string, versionId: string) =>
    client.get<Version>(`/datasets/${datasetId}/versions/${versionId}`).then((r) => r.data),

  deleteBranch: (datasetId: string, branchId: string) =>
    client.delete(`/datasets/${datasetId}/versions/branches/${branchId}`),

  deleteVersion: (datasetId: string, versionId: string) =>
    client.delete(`/datasets/${datasetId}/versions/${versionId}`),

  updateVersion: (datasetId: string, versionId: string, body: VersionUpdateRequest) =>
    client
      .patch<Version>(`/datasets/${datasetId}/versions/${versionId}`, body)
      .then((r) => r.data),

  /** What restoring this version would do to the keep/cut ratings on disk now.
   *  A version-vs-*current* comparison, which `diffVersions` is not — that reads
   *  two snapshots. A rating is hand-made work nothing recomputes, so the
   *  restore confirm states the number first. */
  ratingImpact: (datasetId: string, versionId: string) =>
    client
      .get<RatingImpact>(`/datasets/${datasetId}/versions/${versionId}/rating-impact`)
      .then((r) => r.data),

  restoreVersion: (datasetId: string, versionId: string, body: RestoreRequest) =>
    client
      .post<{ job_id: string }>(`/datasets/${datasetId}/versions/${versionId}/restore`, body)
      .then((r) => r.data),

  pruneStorage: (datasetId: string) =>
    client
      .post<{ job_id: string }>(`/datasets/${datasetId}/versions/prune`)
      .then((r) => r.data),

  diff: (datasetId: string, v1: string, v2: string) =>
    client
      .get<VersionDiff>(`/datasets/${datasetId}/versions/diff`, { params: { v1, v2 } })
      .then((r) => r.data),
};
