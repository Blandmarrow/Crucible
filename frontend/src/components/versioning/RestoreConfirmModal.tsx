import { useState, useEffect } from "react";
import toast from "react-hot-toast";
import { versioningApi } from "../../api/versioning";
import { useJobSSE } from "../../hooks/useSSE";
import { useJobStore } from "../../store/jobStore";
import JobProgressBar from "../common/JobProgressBar";
import type { Version } from "../../types";

interface Props {
  datasetId: string;
  version: Version;
  onClose: () => void;
  onSuccess: () => void;
}

export default function RestoreConfirmModal({ datasetId, version, onClose, onSuccess }: Props) {
  const [handleExtra, setHandleExtra] = useState<"keep" | "remove">("keep");
  const [preRestore, setPreRestore] = useState(true);
  const [loading, setLoading] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);

  useJobSSE(jobId);
  const jobProgress = useJobStore((s) => (jobId ? s.activeJobs.get(jobId) : undefined));

  useEffect(() => {
    if (jobProgress?.status === "completed") {
      toast.success("Restore complete");
      onSuccess();
      onClose();
    } else if (jobProgress?.status === "failed") {
      toast.error("Restore failed: " + (jobProgress.message ?? "unknown error"));
      setLoading(false);
      setJobId(null);
    }
  }, [jobProgress?.status, onSuccess, onClose]);

  async function handleConfirm() {
    setLoading(true);
    try {
      const result = await versioningApi.restoreVersion(datasetId, version.id, {
        handle_extra_images: handleExtra,
        pre_restore_snapshot: preRestore,
      });
      setJobId(result.job_id);
    } catch {
      toast.error("Failed to start restore");
      setLoading(false);
    }
  }

  const isRunning = jobId !== null && jobProgress?.status === "running";
  const percent = jobProgress?.percent ?? 0;

  return (
    <div style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)",
      display: "flex", alignItems: "center", justifyContent: "center", zIndex: 200,
    }}>
      <div className="panel" style={{ width: 460, padding: 0 }}>
        <div className="panel-h" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontWeight: 600, fontSize: 14 }}>Restore Snapshot</span>
          <button className="btn ghost" onClick={onClose} disabled={isRunning} style={{ padding: "2px 8px" }}>✕</button>
        </div>
        <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div style={{
            background: "var(--surface-2)", borderRadius: "var(--r)", padding: "10px 12px", fontSize: 13,
          }}>
            <div style={{ fontWeight: 500 }}>{version.name ?? `Snapshot ${version.id.slice(0, 8)}`}</div>
            <div style={{ fontSize: 12, color: "var(--fg-mute)", marginTop: 2 }}>
              {version.image_count} images · {new Date(version.created_at).toLocaleString()}
            </div>
            {version.description && (
              <div style={{ fontSize: 12, color: "var(--fg-mute)", marginTop: 4 }}>{version.description}</div>
            )}
          </div>

          <div>
            <div style={{ fontSize: 12, fontWeight: 500, marginBottom: 8 }}>Images not in this snapshot:</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {(["keep", "remove"] as const).map((opt) => (
                <label key={opt} style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", fontSize: 13 }}>
                  <input
                    type="radio"
                    name="handle_extra"
                    value={opt}
                    checked={handleExtra === opt}
                    onChange={() => setHandleExtra(opt)}
                    disabled={isRunning}
                  />
                  {opt === "keep" ? "Keep (leave them in the dataset)" : "Remove (delete them from the dataset)"}
                </label>
              ))}
            </div>
          </div>

          <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", fontSize: 13 }}>
            <input
              type="checkbox"
              checked={preRestore}
              onChange={(e) => setPreRestore(e.target.checked)}
              disabled={isRunning}
            />
            Auto-snapshot current state before restoring
          </label>

          <div style={{
            fontSize: 12, color: "var(--warn)",
            background: "rgba(255,152,0,0.1)", border: "1px solid rgba(255,152,0,0.3)",
            borderRadius: "var(--r)", padding: "8px 10px",
          }}>
            Files can only be restored if they were backed up to the object store. Files modified
            outside this app, or before any snapshot was created, cannot be recovered.
          </div>

          {isRunning && (
            <JobProgressBar message={jobProgress?.message ?? "Restoring…"} percent={percent} />
          )}

          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button className="btn ghost" onClick={onClose} disabled={isRunning}>Cancel</button>
            <button className="btn danger" onClick={handleConfirm} disabled={loading}>
              {isRunning ? "Restoring…" : "Restore"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
