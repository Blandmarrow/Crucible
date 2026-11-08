import { useId, useState } from "react";
import { useMutation, useIsMutating } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { datasetsApi } from "../../api/datasets";
import type { Dataset } from "../../types";
import DirPickerModal from "../common/DirPickerModal";
import ModalShell from "../common/ModalShell";

// Shared by every mount, so a request still in flight after this dialog
// closed keeps the reopened one's submit disabled, as when the page owned
// the mutation.
const MUTATION_KEY = ["datasets-dialog", "caption-import"] as const;

interface Props {
  dataset: Dataset;
  /** Hands the job to the page, whose watcher outlives this dialog. */
  onStarted: (jobId: string) => void;
  onClose: () => void;
}

export default function CaptionImportDialog({ dataset, onStarted, onClose }: Props) {
  // Prefix for the dialog fields' `id`/`htmlFor` pairs (GPT-029).
  const uid = useId();
  const [captionImportPath, setCaptionImportPath] = useState("");
  const [captionDirPickerOpen, setCaptionDirPickerOpen] = useState(false);

  const captionImportMutation = useMutation({
    mutationKey: MUTATION_KEY,
    mutationFn: () => datasetsApi.importCaptions(dataset.id, captionImportPath),
    onSuccess: (data) => {
      toast.success("Importing captions…");
      onClose();
      onStarted(data.job_id);
    },
    onError: () => toast.error("Caption import failed"),
  });
  const pending = useIsMutating({ mutationKey: MUTATION_KEY }) > 0;

  return (
    <ModalShell label="Import captions from folder" onClose={onClose}>
      <h3>Import captions from folder</h3>
      <p>Into: <strong style={{ color: "var(--fg)" }}>{dataset.name}</strong></p>
      <p style={{ fontSize: 12, color: "var(--fg-mute)", marginTop: -4, marginBottom: 14 }}>
        Matches each <code>.txt</code> file to an image by filename and overwrites its caption.
      </p>
      <div style={{ marginBottom: 18 }}>
        <label className="label" htmlFor={`${uid}-caption-path`}>Folder path</label>
        <div style={{ display: "flex", gap: 8 }}>
          <input id={`${uid}-caption-path`} className="input" placeholder="/home/user/captions or D:\captions" value={captionImportPath}
            onChange={(e) => setCaptionImportPath(e.target.value)} autoFocus style={{ flex: 1 }}
            onKeyDown={(e) => { if (e.key === "Enter" && captionImportPath && !pending) captionImportMutation.mutate(); }} />
          <button className="btn" onClick={() => setCaptionDirPickerOpen(true)}>Browse…</button>
        </div>
      </div>
      {captionDirPickerOpen && (
        <DirPickerModal
          initialPath={captionImportPath}
          title="Select a caption folder"
          confirmLabel="Use folder"
          onConfirm={(p) => { setCaptionImportPath(p); setCaptionDirPickerOpen(false); }}
          onCancel={() => setCaptionDirPickerOpen(false)}
        />
      )}
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className="btn primary" onClick={() => captionImportMutation.mutate()} disabled={!captionImportPath || pending}>Import captions</button>
      </div>
    </ModalShell>
  );
}
