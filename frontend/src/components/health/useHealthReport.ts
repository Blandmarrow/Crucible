import { useQuery, useQueryClient } from "@tanstack/react-query";
import { healthApi, type HealthReport } from "../../api/health";
import type { Dataset } from "../../types";

/** The dataset's health report, `["dataset-health", id]`. One query function for every
 *  reader (the Health page and the chat), so whichever fetches it also refreshes the
 *  card badge (`docs/dev/analyst/ui.md` § Files and query keys). */
export function useHealthReport(datasetId: string) {
  const qc = useQueryClient();
  return useQuery<HealthReport>({
    queryKey: ["dataset-health", datasetId],
    queryFn: async () => {
      const r = await healthApi.get(datasetId);
      // Building the report is what writes the card badge's counts, so the list is
      // refetched only now, after the write, and only when the counts moved.
      // Invalidating both at once raced: the list came back first, still old.
      const listed = qc.getQueryData<Dataset[]>(["datasets"])?.find((d) => d.id === datasetId);
      const s = listed?.health_summary;
      if (listed && (!s || s.error !== r.summary.error || s.warn !== r.summary.warn || s.info !== r.summary.info)) {
        qc.invalidateQueries({ queryKey: ["datasets"] });
      }
      return r;
    },
  });
}
