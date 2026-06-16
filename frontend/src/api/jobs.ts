import client from "./client";
import type { Job } from "../types";

export const jobsApi = {
  list: (limit = 50) => client.get<Job[]>("/jobs/", { params: { limit } }).then((r) => r.data),
  get: (id: string) => client.get<Job>(`/jobs/${id}`).then((r) => r.data),
  cancel: (id: string) => client.delete(`/jobs/${id}`),
};
