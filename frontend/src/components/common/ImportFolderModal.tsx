import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import toast from "react-hot-toast";
import type { Dataset } from "../../types";
import { datasetsApi } from "../../api/datasets";
import DirPickerModal from "./DirPickerModal";

interface Props {
  /** Candidate datasets to import into. When more than one, a target selector is shown. */
  datasets: Dataset[];
  /** Preselected target (e.g. the card or gallery a user opened this from). */
  initialDatasetId?: string;
  onStarted: (jobId: string) => void;
  onClose: () => void;
}

/** Shared "Import from folder" modal used by DatasetsPage and GalleryPage. */
export default function ImportFolderModal({ datasets, initialDatasetId, onStarted, onClose }: Props) {
  const [targetId, setTargetId] = useState(initialDatasetId ?? datasets[0]?.id ?? "");
  const [path, setPath] = useState("");
  const [subfolder, setSubfolder] = useState("");
  const [preserveStructure, setPreserveStructure] = useState(false);
  const [importCaptions, setImportCaptions] = useState(true);
  const [dirPickerOpen, setDirPickerOpen] = useState(false);
  const [showSubfolders, setShowSubfolders] = useState(false);

  const target = datasets.find((d) => d.id === targetId) ?? null;

  // Existing logical subfolders of the target dataset, for the subfolder "Browse" picker.
  const { data: subfolders = [] } = useQuery({
    queryKey: ["subfolders", targetId],
    queryFn: () => datasetsApi.subfolders(targetId),
    enabled: !!targetId,
  });
  const subfolderPaths = subfolders.map((s) => s.path).filter(Boolean).sort();

  const importMutation = useMutation({
    mutationFn: () => datasetsApi.importFolder(targetId, path, subfolder, preserveStructure, importCaptions),
    onSuccess: (data) => {
      toast.success("Import started");
      onStarted(data.job_id);
      onClose();
    },
    onError: () => toast.error("Import failed"),
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="card p-5" style={{ width: 460, maxWidth: "92vw", maxHeight: "90vh", overflowY: "auto" }} onClick={(e) => e.stopPropagation()}>
        <h3 style={{ fontSize: 15, fontWeight: 600, marginBottom: 14 }}>Import from folder</h3>
        <div style={{ marginBottom: 14 }}>
          <label className="label">Dataset</label>
            {datasets.length <= 1 ? (
              <p style={{ margin: 0 }}>Into: <strong style={{ color: "var(--fg)" }}>{target?.name ?? "—"}</strong></p>
            ) : (
              <select className="select" value={targetId} onChange={(e) => setTargetId(e.target.value)} style={{ width: "100%" }}>
                {datasets.map((d) => (
                  <option key={d.id} value={d.id}>{d.name}</option>
                ))}
              </select>
            )}
          </div>
          <div style={{ marginBottom: 14 }}>
            <label className="label">Folder path</label>
            <div style={{ display: "flex", gap: 8 }}>
              <input
                className="input"
                placeholder="/home/user/images or D:\images"
                value={path}
                onChange={(e) => setPath(e.target.value)}
                // eslint-disable-next-line jsx-a11y/no-autofocus
                autoFocus
                style={{ flex: 1 }}
              />
              <button className="btn" onClick={() => setDirPickerOpen(true)}>Browse…</button>
            </div>
          </div>
          <div style={{ marginBottom: 14 }}>
            <label className="label">Target subfolder <span style={{ fontWeight: 400, color: "var(--fg-mute)", fontSize: 11 }}>(optional)</span></label>
            <div style={{ display: "flex", gap: 8, position: "relative" }}>
              <input
                className="input"
                placeholder="e.g. characters (leave blank for root)"
                value={subfolder}
                onChange={(e) => setSubfolder(e.target.value)}
                disabled={preserveStructure}
                style={{ flex: 1, opacity: preserveStructure ? 0.5 : 1 }}
              />
              <button className="btn" disabled={preserveStructure} onClick={() => setShowSubfolders((v) => !v)}>Browse…</button>
              {showSubfolders && !preserveStructure && (
                <>
                  <div onClick={() => setShowSubfolders(false)} style={{ position: "fixed", inset: 0, zIndex: 1 }} />
                  <div style={{
                    position: "absolute", top: "100%", left: 0, right: 0, marginTop: 4, zIndex: 2,
                    background: "var(--surface-1)", border: "1px solid var(--line-2)", borderRadius: "var(--r)",
                    maxHeight: 200, overflowY: "auto", boxShadow: "var(--shadow-lg)",
                  }}>
                    <button
                      type="button"
                      onClick={() => { setSubfolder(""); setShowSubfolders(false); }}
                      style={{ display: "block", width: "100%", textAlign: "left", padding: "7px 12px", background: "transparent", border: "none", borderBottom: "1px solid var(--line)", color: "var(--fg-mute)", cursor: "pointer", fontSize: 13, fontStyle: "italic" }}
                    >
                      (root)
                    </button>
                    {subfolderPaths.length === 0 ? (
                      <div style={{ padding: "7px 12px", color: "var(--fg-soft)", fontSize: 12.5 }}>No existing subfolders</div>
                    ) : (
                      subfolderPaths.map((p) => (
                        <button
                          key={p}
                          type="button"
                          onClick={() => { setSubfolder(p); setShowSubfolders(false); }}
                          style={{ display: "flex", alignItems: "center", gap: 8, width: "100%", textAlign: "left", padding: "7px 12px", background: "transparent", border: "none", borderBottom: "1px solid var(--line)", color: "var(--fg)", cursor: "pointer", fontSize: 13 }}
                          onMouseEnter={(e) => (e.currentTarget.style.background = "var(--surface-3)")}
                          onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                        >
                          <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="var(--fg-dim)" strokeWidth="1.4"><path d="M2.5 3.5h4l1.5 2h5.5v7h-11v-9z"/></svg>
                          {p}
                        </button>
                      ))
                    )}
                  </div>
                </>
              )}
            </div>
          </div>
          <label style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14, cursor: "pointer" }}>
            <input
              type="checkbox"
              className="checkbox"
              checked={preserveStructure}
              onChange={(e) => { setPreserveStructure(e.target.checked); if (e.target.checked) setSubfolder(""); }}
            />
            <span style={{ fontSize: 13 }}>Preserve source folder structure</span>
          </label>
          {preserveStructure && (
            <p style={{ fontSize: 11.5, color: "var(--fg-mute)", marginTop: -8, marginBottom: 14, paddingLeft: 22 }}>
              Subfolders from the source directory will be recreated as logical subfolders.
            </p>
          )}
          <label style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 18, cursor: "pointer" }}>
            <input
              type="checkbox"
              className="checkbox"
              checked={importCaptions}
              onChange={(e) => setImportCaptions(e.target.checked)}
            />
            <span style={{ fontSize: 13 }}>Import captions (.txt sidecars)</span>
          </label>
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button className="btn ghost" onClick={onClose}>Cancel</button>
            <button className="btn primary" onClick={() => importMutation.mutate()} disabled={!path || !targetId || importMutation.isPending}>Import</button>
          </div>
      </div>
      {dirPickerOpen && (
        <DirPickerModal
          initialPath={path}
          title="Select a folder to import"
          confirmLabel="Use folder"
          onConfirm={(p) => { setPath(p); setDirPickerOpen(false); }}
          onCancel={() => setDirPickerOpen(false)}
        />
      )}
    </div>
  );
}
