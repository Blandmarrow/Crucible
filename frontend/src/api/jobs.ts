import client from "./client";
import type { Job } from "../types";

export interface JobListFilters {
  /** Only jobs in these statuses (the server 422s an unknown one). */
  status?: Job["status"][];
  /** Only these job ids — at most 500 per request. ANDed with `status`. An empty
   *  list matches nothing, so a caller with no ids skips the request instead. */
  ids?: string[];
}

export const jobsApi = {
  list: (limit = 50, filters: JobListFilters = {}) =>
    client
      .get<Job[]>("/jobs/", {
        params: {
          limit,
          ...(filters.status ? { status: filters.status.join(",") } : {}),
          ...(filters.ids ? { ids: filters.ids.join(",") } : {}),
        },
      })
      .then((r) => r.data),
  get: (id: string) => client.get<Job>(`/jobs/${id}`).then((r) => r.data),
  // Fire-and-forget: callers optimistically mark the job cancelled in jobStore and
  // the SSE stream is the source of truth. The common HTTP failure (404: job row
  // already gone) is not actionable, so cancel never rejects.
  cancel: (id: string) => client.delete(`/jobs/${id}`).catch(() => {}),
};
