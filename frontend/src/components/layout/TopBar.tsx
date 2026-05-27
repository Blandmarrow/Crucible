import { useState, useEffect, useRef } from "react";
import { Link, useMatch } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { datasetsApi } from "../../api/datasets";
import { useJobStore } from "../../store/jobStore";
import { useUploadStore } from "../../store/uploadStore";
import { useAllJobsSSE } from "../../hooks/useSSE";
import ConfirmDialog from "../common/ConfirmDialog";
import { usePaneStore } from "../../stores/paneStore";
import { Columns2 } from "lucide-react";

const IMAGE_MODIFYING_JOB_TYPES = new Set(["batch_upscale", "batch_lut", "crop_upscale"]);

const PAGE_LABELS: Record<string, string> = {
  gallery: "Gallery",
  captioning: "Captioning",
  quality: "Score images",
  stats: "Stats",
  export: "Export",
  image: "Image detail",
};

function Breadcrumbs() {
  const dsMatch = useMatch("/datasets/:datasetId/*");
  const datasetId = dsMatch?.params?.datasetId;
  const rest = dsMatch?.params?.["*"] ?? "";
  const segment = rest.split("/")[0];
  const pageLabel = PAGE_LABELS[segment] ?? segment;
  const isBooruMatch = useMatch("/booru");
  const isDatasetsMatch = useMatch("/datasets");

  const { data: dataset } = useQuery({
    queryKey: ["dataset", datasetId],
    queryFn: () => datasetsApi.get(datasetId!),
    enabled: !!datasetId,
    staleTime: 30_000,
  });

  if (isBooruMatch) {
    return (
      <div className="crumbs">
        <Link to="/datasets">Datasets</Link>
        <span className="sep">/</span>
        <span className="here">Booru Browser</span>
      </div>
    );
  }
  if (isDatasetsMatch) {
    return <div className="crumbs"><span className="here">Datasets</span></div>;
  }
  if (datasetId) {
    return (
      <div className="crumbs" style={{ minWidth: 0, overflow: "hidden" }}>
        <Link to="/datasets">Datasets</Link>
        <span className="sep">/</span>
        <Link to={`/datasets/${datasetId}/gallery`} style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 160 }}>
          {dataset?.name ?? "…"}
        </Link>
        {pageLabel && (
          <>
            <span className="sep">/</span>
            <span className="here">{pageLabel}</span>
          </>
        )}
      </div>
    );
  }
  return <div className="crumbs"><span className="here">Crucible</span></div>;
}

export default function TopBar() {
  useAllJobsSSE();
  const qc = useQueryClient();
  const jobs = useJobStore((s) => s.activeJobs);
  const runningJobs = [...jobs.values()].filter((j) => j.status === "running");
  const active = runningJobs[0];
  const uploadProgress = useUploadStore((s) => s.progress);
  const [showConfirm, setShowConfirm] = useState(false);
  const [shuttingDown, setShuttingDown] = useState(false);
  const { enabled: paneEnabled, toggleEnabled: togglePane } = usePaneStore();

  // Page-level job watchers (e.g. in ImageDetailPage) stop when the component
  // unmounts, so jobs that finish after navigation never invalidate the gallery.
  // TopBar is always mounted, making it the right place for this side effect.
  const processedJobsRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    jobs.forEach((progress, jobId) => {
      if (
        progress.status === "completed" &&
        progress.dataset_id &&
        IMAGE_MODIFYING_JOB_TYPES.has(progress.job_type) &&
        !processedJobsRef.current.has(jobId)
      ) {
        processedJobsRef.current.add(jobId);
        qc.invalidateQueries({ queryKey: ["images", progress.dataset_id] });
      }
    });
  }, [jobs, qc]);

  async function handleShutdown() {
    setShowConfirm(false);
    setShuttingDown(true);
    await fetch("/api/v1/shutdown", { method: "POST" }).catch(() => {});
  }

  return (
    <>
      <header style={{
        height: 49, display: "flex", alignItems: "center", gap: 16,
        padding: "0 20px", borderBottom: "1px solid var(--line)",
        background: "var(--surface-1)", flexShrink: 0,
      }}>
        <Breadcrumbs />
        <div style={{ flex: 1 }} />

        {uploadProgress && (
          <div className="progress-pill">
            <span className="pp-dot" />
            <span className="pp-label">Uploading images</span>
            <div className="pp-bar">
              <div className="pp-fill" style={{ width: `${Math.round((uploadProgress.done / uploadProgress.total) * 100)}%`, background: uploadProgress.errors > 0 ? "var(--warn)" : undefined }} />
            </div>
            <span className="pp-num mono">{uploadProgress.done} / {uploadProgress.total}</span>
          </div>
        )}
        {active && (
          <div className="progress-pill">
            <span className="pp-dot" />
            <span className="pp-label">{active.message || active.job_type}</span>
            <div className="pp-bar"><div className="pp-fill" style={{ width: `${active.percent ?? 0}%` }} /></div>
            <span className="pp-num mono">{active.done ?? 0} / {active.total ?? 0}</span>
          </div>
        )}
        {!active && !uploadProgress && (
          <span style={{ fontSize: 12, color: "var(--fg-dim)" }}>
            {shuttingDown ? "Shutting down…" : "Ready"}
          </span>
        )}
        {runningJobs.length > 1 && (
          <span className="badge solid mono">{runningJobs.length} jobs</span>
        )}

        {/* Split view toggle */}
        <button
          className="icon-btn"
          title={paneEnabled ? "Exit split view" : "Enter split view"}
          type="button"
          onClick={togglePane}
          style={{ color: paneEnabled ? "var(--accent)" : undefined }}
        >
          <Columns2 size={15} />
        </button>

        {/* Notification bell — UI only */}
        <button className="icon-btn" title="Notifications" type="button">
          <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
            <path d="M3.5 6.5a4.5 4.5 0 119 0v2l1.5 2H2l1.5-2v-2zM6 13a2 2 0 004 0"/>
          </svg>
        </button>

        <button
          className="icon-btn danger"
          title="Shut down server"
          disabled={shuttingDown}
          onClick={() => setShowConfirm(true)}
          type="button"
          style={{ opacity: shuttingDown ? 0.4 : 1 }}
        >
          <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
            <path d="M8 2v6M4.5 4.5a5.5 5.5 0 107 0"/>
          </svg>
        </button>
      </header>

      {showConfirm && (
        <ConfirmDialog
          title="Shut down server?"
          message="This will stop the Dataset Manager server process. You will need to restart it from the terminal."
          confirmLabel="Shut down"
          danger
          onConfirm={handleShutdown}
          onCancel={() => setShowConfirm(false)}
        />
      )}
    </>
  );
}
