import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { apiErrorDetail } from "../../utils/apiError";
import { comfyApi, type ComfyRow, type LinkedMode } from "../../api/comfy";
import { datasetsApi } from "../../api/datasets";
import { useModalBehavior } from "../../hooks/useModalBehavior";
import { normalizeSubfolder } from "../../utils/subfolderTree";

interface Props {
  planId: string;
  datasetId: string;
  /** Selected rows the folder is written to. */
  rows: ComfyRow[];
  onClose: () => void;
}

/** Set one destination folder on every selected queue row.
 *
 *  The dataset's existing folders are offered as a datalist rather than a closed
 *  select: a run's whole point here is creating folders that do not exist yet. */
export default function SetRowFolderModal({ planId, datasetId, rows, onClose }: Props) {
  const qc = useQueryClient();
  const [folder, setFolder] = useState("");
  // Set when the folder would move rows the blueprint still counts: the dialog then
  // asks before the server is called (it would refuse the default mode with a 409).
  const [asking, setAsking] = useState(false);
  const rowIds = rows.map((r) => r.id);
  const { overlayProps, panelProps } = useModalBehavior({ onClose, label: "Set folder", closeOnBackdrop: true });

  // Same key the run bar uses, so this is usually already cached.
  const { data: subfolders = [] } = useQuery({
    queryKey: ["subfolders", datasetId],
    queryFn: () => datasetsApi.subfolders(datasetId),
    enabled: !!datasetId,
  });

  const setMutation = useMutation({
    mutationFn: ({ value, linked }: { value: string; linked: LinkedMode }) =>
      comfyApi.setRowsSubfolder(planId, value, rowIds, linked),
    onSuccess: ({ updated }, { linked }) => {
      qc.invalidateQueries({ queryKey: ["comfy", "rows", planId] });
      qc.invalidateQueries({ queryKey: ["comfy", "plan", planId] });
      // A moved orphan can change adoption, and an unlinked row leaves its leaf short
      // and changes what the rewrite would count.
      qc.invalidateQueries({ queryKey: ["comfy", "blueprint-preview", planId] });
      if (linked === "unlink") qc.invalidateQueries({ queryKey: ["comfy", "blueprint-rewrite", planId] });
      toast.success(`Set the folder on ${updated} row${updated !== 1 ? "s" : ""}`);
      onClose();
    },
    onError: (err: unknown) => {
      toast.error(apiErrorDetail(err, "Failed to set the folder"));
    },
  });

  const trimmed = folder.trim();
  // Compare on the server's spelling so a no-op never asks.
  const target = normalizeSubfolder(trimmed);
  const linkedMoving = rows.filter((r) => r.blueprint_linked && r.subfolder !== target).length;

  function apply() {
    if (setMutation.isPending) return;
    if (linkedMoving > 0) setAsking(true);
    else setMutation.mutate({ value: trimmed, linked: "refuse" });
  }

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
              disabled={asking}
              onKeyDown={(e) => { if (e.key === "Enter") apply(); }}
            />
            <datalist id="comfy-row-folders">
              {subfolders.filter((sf) => sf.path).map((sf) => <option key={sf.path} value={sf.path} />)}
            </datalist>
          </label>
          {asking ? (
            <div role="alertdialog" aria-label="Move blueprint rows" data-testid="comfy-linked-confirm" style={{
              display: "flex", flexDirection: "column", gap: 8, padding: "10px 12px",
              border: "1px solid var(--warn)", background: "var(--warn-bg)", borderRadius: "var(--r)", fontSize: 12.5,
            }}>
              <span style={{ lineHeight: 1.45 }}>
                {linkedMoving} of these {rows.length} row{rows.length !== 1 ? "s" : ""} came from the blueprint.
                Moving them unlinks them, and the blueprint then counts their old folder as short, so
                the next Top up refills it.
              </span>
              <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "flex-end", gap: 8 }}>
                <button className="btn ghost" onClick={() => setAsking(false)}>Cancel</button>
                <button className="btn" disabled={setMutation.isPending}
                  onClick={() => setMutation.mutate({ value: trimmed, linked: "skip" })}>
                  Move hand-made rows only
                </button>
                <button className="btn primary" disabled={setMutation.isPending}
                  onClick={() => setMutation.mutate({ value: trimmed, linked: "unlink" })}>
                  Move and unlink
                </button>
              </div>
            </div>
          ) : (
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button className="btn ghost" onClick={onClose}>Cancel</button>
              <button className="btn primary" disabled={setMutation.isPending} onClick={apply}>
                {setMutation.isPending ? "Applying…" : "Apply"}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
