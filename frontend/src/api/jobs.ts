import client from "./client";
import type { Job } from "../types";

export const jobsApi = {
  list: (limit = 50) => client.get<Job[]>("/jobs/", { params: { limit } }).then((r) => r.data),
  get: (id: string) => client.get<Job>(`/jobs/${id}`).then((r) => r.data),
  // Fire-and-forget: callers optimistically mark the job cancelled in jobStore and
  // the SSE stream is the source of truth. The common HTTP failure (404: job row
  // already gone) is not actionable, so cancel never rejects.
  cancel: (id: string) => client.delete(`/jobs/${id}`).catch(() => {}),
};
