import { useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { comfyApi, type LinkedMode } from "../../api/comfy";
import { apiErrorDetail } from "../../utils/apiError";

export interface CopyRowsVars {
  sourcePlanId: string;
  sourceDatasetId: string;
  targetPlanId: string;
  targetDatasetId: string;
  targetName: string;
  rowIds: string[];
  move: boolean;
  /** null keeps each row's own folder. */
  subfolder?: string | null;
  linked?: LinkedMode;
}

/** Copy or move rows into another plan (`rows/copy`), refreshing both plans. Shared by
 *  `CopyRowsToPlanModal` and the drop of rows onto a plan in the rail (M6 §8c). */
export function useCopyRowsMutation(onDone?: (vars: CopyRowsVars) => void) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: CopyRowsVars) =>
      comfyApi.copyRows(v.targetPlanId, {
        source_plan_id: v.sourcePlanId, row_ids: v.rowIds, move: v.move,
        subfolder: v.subfolder ?? null, linked: v.linked ?? "refuse",
      }),
    onSuccess: (data, v) => {
      for (const pid of [v.sourcePlanId, v.targetPlanId]) {
        qc.invalidateQueries({ queryKey: ["comfy", "rows", pid] });
        qc.invalidateQueries({ queryKey: ["comfy", "prompts", pid] });
        qc.invalidateQueries({ queryKey: ["comfy", "plan", pid] });
      }
      qc.invalidateQueries({ queryKey: ["comfy", "plans", v.sourceDatasetId] });
      qc.invalidateQueries({ queryKey: ["comfy", "plans", v.targetDatasetId] });
      if (v.move) qc.invalidateQueries({ queryKey: ["comfy", "blueprint-preview", v.sourcePlanId] });
      const n = (v.move ? data.moved : data.copied) ?? 0;
      const extra = data.skipped ? ` (${data.skipped} skipped)` : "";
      toast.success(`${v.move ? "Moved" : "Copied"} ${n} row${n !== 1 ? "s" : ""} to "${v.targetName}"${extra}`);
      onDone?.(v);
    },
    onError: (err: unknown, v) => toast.error(apiErrorDetail(err, `Failed to ${v.move ? "move" : "copy"} the rows`)),
  });
}
