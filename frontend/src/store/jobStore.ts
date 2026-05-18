import { create } from "zustand";
import type { JobProgress } from "../types";

const TERMINAL_STATUSES = new Set(["completed", "failed", "cancelled"]);
const TTL_MS = 5 * 60 * 1000; // purge completed jobs after 5 minutes

interface JobStore {
  activeJobs: Map<string, JobProgress>;
  updateJob: (id: string, progress: Partial<JobProgress>) => void;
  removeJob: (id: string) => void;
  getJob: (id: string) => JobProgress | undefined;
}

export const useJobStore = create<JobStore>((set, get) => ({
  activeJobs: new Map(),
  updateJob: (id, progress) =>
    set((s) => {
      const next = new Map(s.activeJobs);
      const existing = next.get(id) ?? { job_id: id } as JobProgress;
      const updated = { ...existing, ...progress };
      next.set(id, updated);

      // Schedule cleanup for terminal jobs so the Map doesn't grow unbounded
      if (progress.status && TERMINAL_STATUSES.has(progress.status)) {
        setTimeout(() => {
          get().removeJob(id);
        }, TTL_MS);
      }

      return { activeJobs: next };
    }),
  removeJob: (id) =>
    set((s) => {
      const next = new Map(s.activeJobs);
      next.delete(id);
      return { activeJobs: next };
    }),
  getJob: (id) => get().activeJobs.get(id),
}));
