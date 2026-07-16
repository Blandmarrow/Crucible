import toast from "react-hot-toast";

import { jobsApi } from "../api/jobs";

/**
 * Fetch a completed import job and show a summary toast: a warning when any
 * images failed, otherwise a success toast with the added count. Falls back to
 * a generic success toast if the job lookup fails.
 */
export function showImportSummaryToast(jobId: string): void {
  jobsApi
    .get(jobId)
    .then((job) => {
      const r = job.result_data as { added?: number; failed_count?: number };
      const added = r.added ?? 0;
      const failed = r.failed_count ?? 0;
      if (failed > 0) {
        toast(`Imported ${added} · ${failed} failed (see server log)`);
      } else {
        toast.success(`Imported ${added} image${added !== 1 ? "s" : ""}`);
      }
    })
    .catch(() => toast.success("Import complete"));
}
