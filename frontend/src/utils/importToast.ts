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
      const r = job.result_data as { added?: number; videos_added?: number; failed_count?: number };
      const added = r.added ?? 0;
      const videos = r.videos_added ?? 0;
      const failed = r.failed_count ?? 0;
      const what =
        `${added} image${added !== 1 ? "s" : ""}` +
        (videos ? ` and ${videos} video${videos !== 1 ? "s" : ""}` : "");
      if (failed > 0) {
        toast(`Imported ${what} · ${failed} failed (see server log)`);
      } else {
        toast.success(`Imported ${what}`);
      }
    })
    .catch(() => toast.success("Import complete"));
}
