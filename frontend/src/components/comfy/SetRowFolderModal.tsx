import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { apiErrorDetail } from "../../utils/apiError";
import { comfyApi } from "../../api/comfy";
import { datasetsApi } from "../../api/datasets";
import { useModalBehavior } from "../../hooks/useModalBehavior";

interface Props {
  planId: string;
  datasetId: string;
  /** Selected rows the folder is written to. */
  rowIds: string[];
  onClose: () => void;
}

/** Set one destination folder on every selected queue row.
 *
 *  The dataset's existing folders are offered as a datalist rather than a closed
 *  select: a run's whole point here is creating folders that do not exist yet. */
export default function SetRowFolderModal({ planId, datasetId, rowIds, onClose }: Props) {
  const qc = useQueryClient();
  const [folder, setFolder] = useState("");
  const { overlayProps, panelProps } = useModalBehavior({ onClose, label: "Set folder", closeOnBackdrop: true });

  // Same key the run bar uses, so this is usually already cached.
  const { data: subfolders = [] } = useQuery({
    queryKey: ["subfolders", datasetId],
    queryFn: () => datasetsApi.subfolders(datasetId),
    enabled: !!datasetId,
  });

  const setMutation = useMutation({
    mutationFn: (value: string) => comfyApi.setRowsSubfolder(planId, value, rowIds),
    onSuccess: ({ updated }) => {
      qc.invalidateQueries({ queryKey: ["comfy", "rows", planId] });
      toast.success(`Set the folder on ${updated} row${updated !== 1 ? "s" : ""}`);
      onClose();
    },
    onError: (err: unknown) => {
      toast.error(apiErrorDetail(err, "Failed to set the folder"));
    },
  });

  const trimmed = folder.trim();

  return (
    <div
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.55)", zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center" }}
      {...overlayProps}
    >
      <div className="panel" style={{ width: 440, maxWidth: "92vw" }} {...panelProps}>
        <div className="panel-h"><h3>Set folder</h3></div>
        <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>
            Files the images of {rowIds.length} selected row{rowIds.length !== 1 ? "s" : ""} into this
            folder, nested under the base folder set on the Run bar. Selected rows only. Completed rows
            keep their images and are not reset. Leave blank to file them straight into the base folder.
          </p>
          <label style={{ fontSize: 12, color: "var(--fg-mute)", display: "flex", flexDirection: "column", gap: 4 }}>
            Folder
            <input
              className="input" autoFocus list="comfy-row-folders" maxLength={512}
              placeholder="e.g. salt-fen/iron-knight"
              value={folder} onChange={(e) => setFolder(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !setMutation.isPending) setMutation.mutate(trimmed); }}
            />
            <datalist id="comfy-row-folders">
              {subfolders.filter((sf) => sf.path).map((sf) => <option key={sf.path} value={sf.path} />)}
            </datalist>
          </label>
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
            <button className="btn ghost" onClick={onClose}>Cancel</button>
            <button
              className="btn primary"
              disabled={setMutation.isPending}
              onClick={() => setMutation.mutate(trimmed)}
            >
              {setMutation.isPending ? "Applying…" : "Apply"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
