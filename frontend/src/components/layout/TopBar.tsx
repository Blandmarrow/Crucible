import { useState, useEffect, useRef } from "react";
import { Link, useMatch } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { datasetsApi } from "../../api/datasets";
import { jobsApi } from "../../api/jobs";
import { useJobStore } from "../../store/jobStore";
import { useUploadStore } from "../../store/uploadStore";
import { useAllJobsSSE } from "../../hooks/useSSE";
import ConfirmDialog from "../common/ConfirmDialog";
import { usePaneStore } from "../../stores/paneStore";
import { Columns2, RefreshCw } from "lucide-react";

const CAPTION_JOB_TYPES = new Set(["caption", "caption_pipeline"]);
const IMAGE_MODIFYING_JOB_TYPES = new Set(["batch_upscale", "batch_lut", "crop_upscale", "quality_score", "caption", "caption_pipeline", "comfy_generate"]);
const DATASET_MODIFYING_JOB_TYPES = new Set(["duplicate", "import"]);

const PAGE_LABELS: Record<string, string> = {
  gallery: "Gallery",
  captioning: "Captioning",
  quality: "Score images",
  stats: "Stats",
  export: "Export",
  image: "Image detail",
  comfy: "ComfyUI",
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
  const runningJob = [...jobs.values()].find((j) => j.status === "running");
  const pendingJobs = [...jobs.values()].filter((j) => j.status === "pending");
  const uploadProgress = useUploadStore((s) => s.progress);
  const [showConfirm, setShowConfirm] = useState(false);
  const [shuttingDown, setShuttingDown] = useState(false);
  const [showRestartConfirm, setShowRestartConfirm] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const { enabled: paneEnabled, toggleEnabled: togglePane } = usePaneStore();

  // Page-level job watchers (e.g. in ImageDetailPage) stop when the component
  // unmounts, so jobs that finish after navigation never invalidate the gallery.
  // TopBar is always mounted, making it the right place for this side effect.
  const processedJobsRef = useRef<Set<string>>(new Set());
  const captionDoneRef = useRef<Map<string, number>>(new Map());
  useEffect(() => {
    jobs.forEach((progress, jobId) => {
      if (progress.status === "completed" && !processedJobsRef.current.has(jobId)) {
        processedJobsRef.current.add(jobId);
        captionDoneRef.current.delete(jobId);
        if (progress.dataset_id && IMAGE_MODIFYING_JOB_TYPES.has(progress.job_type)) {
          qc.invalidateQueries({ queryKey: ["images", progress.dataset_id] });
          qc.invalidateQueries({ queryKey: ["dataset-stats", progress.dataset_id] });
          qc.invalidateQueries({ queryKey: ["tag-stats", progress.dataset_id] });
          qc.invalidateQueries({ queryKey: ["score-values", progress.dataset_id] });
          qc.invalidateQueries({ queryKey: ["tag-cooccurrence", progress.dataset_id] });
          if (progress.job_type === "quality_score") {
            qc.invalidateQueries({ queryKey: ["duplicates", progress.dataset_id] });
          }
        }
        if (DATASET_MODIFYING_JOB_TYPES.has(progress.job_type)) {
          qc.invalidateQueries({ queryKey: ["datasets"] });
          if (progress.job_type === "import" && progress.dataset_id) {
            qc.invalidateQueries({ queryKey: ["images", progress.dataset_id] });
            qc.invalidateQueries({ queryKey: ["dataset-stats", progress.dataset_id] });
            qc.invalidateQueries({ queryKey: ["tag-stats", progress.dataset_id] });
            qc.invalidateQueries({ queryKey: ["score-values", progress.dataset_id] });
            qc.invalidateQueries({ queryKey: ["tag-cooccurrence", progress.dataset_id] });
          }
        }
      }
      // Per-image live updates while a caption job is running (#39)
      if (
        progress.status === "running" &&
        CAPTION_JOB_TYPES.has(progress.job_type) &&
        progress.dataset_id
      ) {
        const prevDone = captionDoneRef.current.get(jobId) ?? -1;
        const currentDone = progress.done ?? 0;
        if (currentDone > prevDone) {
          captionDoneRef.current.set(jobId, currentDone);
          qc.invalidateQueries({ queryKey: ["images", progress.dataset_id] });
          if (progress.image_id) {
            qc.invalidateQueries({ queryKey: ["caption", progress.image_id] });
          }
        }
      }
    });
  }, [jobs, qc]);

  async function handleShutdown() {
    setShowConfirm(false);
    setShuttingDown(true);
    await fetch("/api/v1/shutdown", { method: "POST" }).catch(() => {});
  }

  async function handleRestart() {
    setShowRestartConfirm(false);
    setRestarting(true);
    let oldStartTime: number | null = null;
    try {
      const res = await fetch("/api/v1/health", { cache: "no-store" });
      if (res.ok) oldStartTime = (await res.json()).start_time ?? null;
    } catch { /* ignore */ }
    await fetch("/api/v1/restart", { method: "POST" }).catch(() => {});
    const deadline = Date.now() + 60_000;
    while (Date.now() < deadline) {
      await new Promise<void>(r => setTimeout(r, 1000));
      try {
        const res = await fetch("/api/v1/health", { cache: "no-store" });
        if (res.ok) {
          const data = await res.json();
          if (oldStartTime === null || data.start_time !== oldStartTime) {
            window.location.reload();
            return;
          }
        }
      } catch { /* not up yet */ }
    }
    setRestarting(false);
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
        {runningJob && (
          <div className="progress-pill">
            <span className="pp-dot" />
            <span className="pp-label">{runningJob.label || runningJob.message || runningJob.job_type}</span>
            <div className="pp-bar">
              {(runningJob.percent ?? 0) < 0
                ? <div className="pp-fill-indeterminate" />
                : <div className="pp-fill" style={{ width: `${runningJob.percent ?? 0}%` }} />
              }
            </div>
            {(runningJob.percent ?? 0) >= 0 && (
              <span className="pp-num mono">{runningJob.done ?? 0} / {runningJob.total ?? 0}</span>
            )}
          </div>
        )}
        {pendingJobs.length > 0 && (
          <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <span className="badge dot" style={{ color: "var(--fg-dim)" }}>
              {pendingJobs.length} queued
            </span>
            {pendingJobs.map((j) => (
              <span
                key={j.job_id}
                style={{
                  display: "flex", alignItems: "center", gap: 4,
                  fontSize: 11, color: "var(--fg-dim)",
                  background: "var(--surface-2)", borderRadius: 4,
                  padding: "2px 6px",
                }}
              >
                <span>{j.label || j.job_type}</span>
                <button
                  type="button"
                  onClick={() => {
                    useJobStore.getState().updateJob(j.job_id, { status: "cancelled" });
                    jobsApi.cancel(j.job_id);
                  }}
                  style={{
                    background: "none", border: "none", cursor: "pointer",
                    color: "var(--fg-dim)", padding: 0, lineHeight: 1,
                    fontSize: 13,
                  }}
                  title="Cancel queued job"
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        )}
        {!runningJob && !uploadProgress && pendingJobs.length === 0 && (
          <span style={{ fontSize: 12, color: "var(--fg-dim)" }}>
            {shuttingDown ? "Shutting down…" : restarting ? "Restarting…" : "Ready"}
          </span>
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
          className="icon-btn"
          title="Restart server"
          disabled={shuttingDown || restarting}
          onClick={() => setShowRestartConfirm(true)}
          type="button"
          style={{ opacity: (shuttingDown || restarting) ? 0.4 : 1 }}
        >
          <RefreshCw size={15} />
        </button>

        <button
          className="icon-btn danger"
          title="Shut down server"
          disabled={shuttingDown || restarting}
          onClick={() => setShowConfirm(true)}
          type="button"
          style={{ opacity: (shuttingDown || restarting) ? 0.4 : 1 }}
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
      {showRestartConfirm && (
        <ConfirmDialog
          title="Restart server?"
          message="The server will restart automatically. Any running jobs will be interrupted."
          confirmLabel="Restart"
          onConfirm={handleRestart}
          onCancel={() => setShowRestartConfirm(false)}
        />
      )}
    </>
  );
}
