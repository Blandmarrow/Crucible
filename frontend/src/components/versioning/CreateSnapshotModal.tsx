import { useState, useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { versioningApi, type SnapshotCreateRequest } from "../../api/versioning";
import { useJobSSE } from "../../hooks/useSSE";
import { useModalBehavior } from "../../hooks/useModalBehavior";
import { useJobStore } from "../../store/jobStore";
import JobProgressBar from "../common/JobProgressBar";

interface Props {
  datasetId: string;
  activeBranchId?: string;
  onClose: () => void;
}

export default function CreateSnapshotModal({ datasetId, activeBranchId, onClose }: Props) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [loading, setLoading] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);

  useJobSSE(jobId);
  const jobProgress = useJobStore((s) => (jobId ? s.activeJobs.get(jobId) : undefined));

  useEffect(() => {
    if (jobProgress?.status === "completed") {
      qc.invalidateQueries({ queryKey: ["versions", datasetId] });
      qc.invalidateQueries({ queryKey: ["branches", datasetId] });
      toast.success("Snapshot created");
      onClose();
    } else if (jobProgress?.status === "failed") {
      toast.error("Snapshot failed: " + (jobProgress.message ?? "unknown error"));
      setLoading(false);
      setJobId(null);
    }
  }, [jobProgress?.status, qc, datasetId, onClose]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    try {
      const body: SnapshotCreateRequest = {
        name: name.trim() || undefined,
        description: description.trim(),
        branch_id: activeBranchId,
      };
      const result = await versioningApi.createSnapshot(datasetId, body);
      if ("job_id" in result) {
        setJobId(result.job_id);
      } else {
        qc.invalidateQueries({ queryKey: ["versions", datasetId] });
        qc.invalidateQueries({ queryKey: ["branches", datasetId] });
        toast.success("Snapshot created");
        onClose();
      }
    } catch {
      toast.error("Failed to create snapshot");
      setLoading(false);
    }
  }

  const isRunning = jobId !== null && jobProgress?.status === "running";
  const percent = jobProgress?.percent ?? 0;

  // Escape mirrors the Cancel button, which is disabled while the job runs.
  const { overlayProps, panelProps } = useModalBehavior({
    onClose: () => { if (!isRunning) onClose(); },
    label: "Create snapshot",
  });

  return (
    <div
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)",
        display: "flex", alignItems: "center", justifyContent: "center", zIndex: 200,
      }}
      {...overlayProps}
    >
      <div className="panel" style={{ width: 440, padding: 0 }} {...panelProps}>
        <div className="panel-h" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontWeight: 600, fontSize: 14 }}>Create Snapshot</span>
          <button className="btn ghost" onClick={onClose} disabled={isRunning} style={{ padding: "2px 8px" }}>✕</button>
        </div>
        <form onSubmit={handleSubmit}>
          <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <div>
              <label style={{ fontSize: 12, color: "var(--fg-mute)", display: "block", marginBottom: 4 }}>
                Name <span style={{ opacity: 0.6 }}>(optional — auto-generated if blank)</span>
              </label>
              <input
                className="input"
                style={{ width: "100%" }}
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Before quality scoring"
                disabled={isRunning}
              />
            </div>
            <div>
              <label style={{ fontSize: 12, color: "var(--fg-mute)", display: "block", marginBottom: 4 }}>
                Description
              </label>
              <textarea
                className="input"
                style={{ width: "100%", resize: "vertical", minHeight: 60 }}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                disabled={isRunning}
              />
            </div>

            {isRunning && (
              <JobProgressBar message={jobProgress?.message ?? "Snapshotting…"} percent={percent} />
            )}

            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button type="button" className="btn ghost" onClick={onClose} disabled={isRunning}>
                Cancel
              </button>
              <button type="submit" className="btn primary" disabled={loading}>
                {isRunning ? "Creating…" : "Create Snapshot"}
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}
