import toast from "react-hot-toast";

import { jobsApi } from "../api/jobs";

/**
 * Fetch a finished restore or checkout job and toast its outcome: a warning
 * naming what could not be restored when `result_data.failed_count > 0`,
 * otherwise `doneMessage` as a success. Falls back to the success toast when
 * the lookup fails.
 *
 * A restore that loses files still ends `completed` — the job succeeded, some
 * of the work did not (PM-023's `failed_count` convention) — so the status
 * alone would say "Restore complete" over images it never brought back.
 * Fire-and-forget on purpose: both callers unmount (or reset) the moment the
 * job completes, and the promise outlives them. Modelled on `importToast.ts`.
 */
export function showRestoreSummaryToast(jobId: string, doneMessage: string): void {
  jobsApi
    .get(jobId)
    .then((job) => {
      const r = (job.result_data ?? {}) as {
        failed_count?: number;
        failure_summary?: string | null;
      };
      const failed = Number(r.failed_count ?? 0);
      if (failed > 0) {
        const summary = r.failure_summary || `${failed} file(s) could not be restored`;
        toast(`${doneMessage} with problems: ${summary} (see Logs)`, {
          icon: "⚠️",
          duration: 10000,
        });
      } else {
        toast.success(doneMessage);
      }
    })
    .catch(() => toast.success(doneMessage));
}
