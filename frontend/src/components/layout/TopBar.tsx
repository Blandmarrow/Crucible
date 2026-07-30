import { useState, useEffect, useRef } from "react";
import { Link, useMatch } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { datasetsApi } from "../../api/datasets";
import { jobsApi } from "../../api/jobs";
import { useJobStore } from "../../store/jobStore";
import { useUploadStore } from "../../store/uploadStore";
import { useAllJobsSSE } from "../../hooks/useSSE";
import ConfirmDialog from "../common/ConfirmDialog";
import CrucibleMark from "../common/CrucibleMark";
import { usePaneStore } from "../../store/paneStore";
import { TERMINAL_JOB_STATUSES } from "../../constants/jobs";
import { invalidateDetectionQueries } from "../../utils/detectionQueries";
import { Columns2, RefreshCw } from "lucide-react";

// Jobs that import/produce images incrementally — refresh the gallery each time
// the done-count advances, not only on completion (#39, ComfyUI queue).
// How long a restart may take before the overlay offers a manual way out. The
// server is still polled after this — it only changes what the overlay says.
const RESTART_SLOW_MS = 25_000;

const LIVE_IMAGE_JOB_TYPES = new Set(["caption", "caption_pipeline", "comfy_generate", "video_extract", "video_reextract"]);
const IMAGE_MODIFYING_JOB_TYPES = new Set(["batch_upscale", "batch_lut", "crop_upscale", "crop_to_detection", "quality_score", "caption", "caption_pipeline", "comfy_generate", "video_extract", "video_reextract"]);
const DATASET_MODIFYING_JOB_TYPES = new Set(["duplicate", "import"]);
// Deliberately in neither set above: comfy_prompts writes queue rows, not images,
// so the image/dataset invalidations would all be pointless. It gets its own
// branch below because its whole point is surviving the modal that started it.
const PROMPT_JOB_TYPE = "comfy_prompts";
// The four jobs that re-cut an image thumbnail as a best-effort post-commit
// epilogue. Each reports a `thumbnails_stale` count; the branch below is the one
// place that turns it into something the user sees. It lives here rather than in
// the five forms that start these jobs (LutForm, UpscaleForm, BulkEditPage,
// SelectionToolbar, ImageDetailPage's three handlers, ReextractFramesForm)
// because TopBar is always mounted — and a 400-frame re-extraction is exactly
// the job you walk away from.
const THUMBNAIL_EPILOGUE_JOB_TYPES = new Set([
  "batch_lut", "batch_upscale", "crop_upscale", "video_reextract",
]);

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
  const [restartSlow, setRestartSlow] = useState(false);
  const { enabled: paneEnabled, toggleEnabled: togglePane } = usePaneStore();

  // Page-level job watchers (e.g. in ImageDetailPage) stop when the component
  // unmounts, so jobs that finish after navigation never invalidate the gallery.
  // TopBar is always mounted, making it the right place for this side effect.
  const processedJobsRef = useRef<Set<string>>(new Set());
  const captionDoneRef = useRef<Map<string, number>>(new Map());
  const promptDoneRef = useRef<Map<string, number>>(new Map());
  const promptTerminalRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    jobs.forEach((progress, jobId) => {
      // Prompt-generation jobs (ComfyUI "generate until N"): rows land per batch,
      // and the modal that started the job may be closed — so the refresh and the
      // outcome toast both have to live here, in a component that never unmounts.
      if (progress.job_type === PROMPT_JOB_TYPE) {
        const done = progress.done ?? 0;
        // Skip optimistic cancel writes here too, or the outcome toast fires at
        // click time and promptTerminalRef dedups the real terminal event away.
        const terminal = TERMINAL_JOB_STATUSES.has(progress.status) && !progress.optimistic;
        const advanced = done > (promptDoneRef.current.get(jobId) ?? -1);
        if (progress.status === "running" && advanced) promptDoneRef.current.set(jobId, done);
        if ((progress.status === "running" && advanced) || terminal) {
          if (progress.plan_id) {
            qc.invalidateQueries({ queryKey: ["comfy", "rows", progress.plan_id] });
            // GeneratePromptsModal may still be open showing its "N in queue"
            // count off this key, and rows land per batch.
            qc.invalidateQueries({ queryKey: ["comfy", "prompts", progress.plan_id] });
          }
          if (progress.dataset_id) qc.invalidateQueries({ queryKey: ["comfy", "plans", progress.dataset_id] });
        }
        if (terminal && !promptTerminalRef.current.has(jobId)) {
          promptTerminalRef.current.add(jobId);
          promptDoneRef.current.delete(jobId);
          const requested = progress.requested;
          if (progress.status === "completed") {
            if (requested !== undefined && done < requested) {
              // Rows genuinely exist, so this is not a failure — but it is a
              // shortfall the user must not discover by counting rows.
              toast(`Added ${done} of ${requested} prompts — the model stopped producing new ones`,
                { icon: "⚠️", duration: 6000 });
            } else {
              toast.success(`Added ${done} generated prompt${done !== 1 ? "s" : ""} to the queue`);
            }
          } else if (progress.status === "failed") {
            toast.error(progress.message || "Prompt generation failed");
          } else {
            toast(`Prompt generation stopped — ${done} prompt${done !== 1 ? "s" : ""} kept`);
          }
        }
      }
      // Every terminal status, not just "completed": these jobs commit per item, so
      // a cancelled or failed run has still changed real images (a ComfyUI run
      // cancelled after 3 of 10 rows imported 3 images). Only invalidating on
      // success left those changes invisible until the next full-page load.
      // But never on the cancel buttons' own optimistic status write — a row can
      // still land between the click and the cooperative cancel, and reacting at
      // click time would consume this job's one invalidation before it does. The
      // backend always emits a terminal SSE event (even for pending jobs, reaped
      // at dequeue), and useSSE clears the flag, so the real event gets through.
      if (
        TERMINAL_JOB_STATUSES.has(progress.status) &&
        !progress.optimistic &&
        !processedJobsRef.current.has(jobId)
      ) {
        processedJobsRef.current.add(jobId);
        captionDoneRef.current.delete(jobId);
        // A stale thumbnail is the one outcome of these four jobs that leaves no
        // trace in the UI: the image is correct and committed, but the gallery
        // keeps drawing the old tile. Read after the `processedJobsRef` add, so
        // crop_upscale's two terminal events (its own, then job_queue's) toast
        // once. Transport is a fetch, not the SSE payload: `broadcaster.emit`
        // silently drops events when a subscriber's 200-slot queue fills, so a
        // piggybacked count would under-report.
        //
        // `cancelled` is included — every one of these workers commits its
        // counts above `raise_if_cancelled`. `failed` is not: `job_queue` fails a
        // raising job from a *separate* session and never persists the worker's
        // dict, so there is nothing to read.
        if (
          THUMBNAIL_EPILOGUE_JOB_TYPES.has(progress.job_type) &&
          progress.status !== "failed"
        ) {
          jobsApi.get(jobId).then((job) => {
            const stale = Number(job.result_data?.thumbnails_stale ?? 0);
            if (stale <= 0) return;
            toast(
              `${stale} preview${stale !== 1 ? "s are" : " is"} out of date — the image${stale !== 1 ? "s were" : " was"} ` +
              "written correctly, but the thumbnail could not be rebuilt. " +
              "Bulk Edit → Thumbnails repairs them.",
              { icon: "⚠️", duration: 10000 },
            );
          }).catch(() => { /* the outcome of the job itself is reported elsewhere */ });
        }
        // Deliberately not in IMAGE_MODIFYING_JOB_TYPES: the repair rewrites
        // thumbnails and bumps `updated_at`, so the rows carrying the `?v=`
        // cache-buster must refetch — but no count, size or score changed, and
        // the four stats queries would all come back identical.
        if (progress.job_type === "regenerate_thumbnails") {
          if (progress.dataset_id) {
            qc.invalidateQueries({ queryKey: ["images", progress.dataset_id] });
          }
          qc.invalidateQueries({ queryKey: ["image"] });
        }
        if (progress.dataset_id && IMAGE_MODIFYING_JOB_TYPES.has(progress.job_type)) {
          qc.invalidateQueries({ queryKey: ["images", progress.dataset_id] });
          // Dataset summary counts (image_count / captioned_count) live on the
          // singular ["dataset", id] query; refresh it whenever images change so
          // the sidebar and gallery "All" counter stay live (e.g. comfy_generate
          // adds images, caption jobs change captioned_count).
          qc.invalidateQueries({ queryKey: ["dataset", progress.dataset_id] });
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
            qc.invalidateQueries({ queryKey: ["dataset", progress.dataset_id] });
            qc.invalidateQueries({ queryKey: ["dataset-stats", progress.dataset_id] });
            qc.invalidateQueries({ queryKey: ["tag-stats", progress.dataset_id] });
            qc.invalidateQueries({ queryKey: ["score-values", progress.dataset_id] });
            qc.invalidateQueries({ queryKey: ["tag-cooccurrence", progress.dataset_id] });
          }
        }
        // Detection jobs (run / manual box+SAM / mask refine) change per-image
        // detections and the dataset label/model/stats rollups, but not the
        // gallery — deliberately NOT in IMAGE_MODIFYING_JOB_TYPES. Detail pages go
        // stale otherwise (bulk detect run finishing after navigation).
        // crop_to_detection is included too: replace-mode crops now remap (and can
        // drop) detection geometry, so those rollups must refresh as well.
        if (progress.job_type === "detection" || progress.job_type === "crop_to_detection") {
          qc.invalidateQueries({ queryKey: ["image"] });
          invalidateDetectionQueries(qc, progress.dataset_id);
        }
        // video_extract writes three things the generic image invalidations above
        // do not cover: a subfolder (all three modes can create one), the Video
        // row itself (the endpoint commits the confirmed crop/deinterlace/trims),
        // and this video's extraction history.
        if (progress.job_type === "video_extract") {
          if (progress.dataset_id) {
            qc.invalidateQueries({ queryKey: ["subfolders", progress.dataset_id] });
            qc.invalidateQueries({ queryKey: ["videos", progress.dataset_id] });
          }
          if (progress.video_id) {
            qc.invalidateQueries({ queryKey: ["video", progress.video_id] });
            qc.invalidateQueries({ queryKey: ["video-frames", progress.video_id] });
          }
        }
        // Pass 2 rewrites frames in place, changing width/height/phash and the
        // file itself. The singular ["image"] key is otherwise only invalidated
        // for detection, so an open detail pane would keep showing the triage
        // dimensions and thumbnail. No subfolder/video invalidation: it creates
        // no subfolder and touches no Video row — but the frames that row *lists*
        // are exactly what changed, and the re-extract dialog can be closed
        // mid-run, so nothing else would refresh the extraction-history panel.
        if (progress.job_type === "video_reextract") {
          qc.invalidateQueries({ queryKey: ["image"] });
          if (progress.dataset_id) {
            qc.invalidateQueries({ queryKey: ["duplicates", progress.dataset_id] });
          }
          if (progress.video_id) {
            qc.invalidateQueries({ queryKey: ["video-frames", progress.video_id] });
          }
        }
      }
      // Live gallery updates while a captioning or ComfyUI-generate job runs — the
      // gallery would otherwise not refresh until the job completes (#39).
      if (
        progress.status === "running" &&
        LIVE_IMAGE_JOB_TYPES.has(progress.job_type) &&
        progress.dataset_id
      ) {
        const prevDone = captionDoneRef.current.get(jobId) ?? -1;
        const currentDone = progress.done ?? 0;
        if (currentDone > prevDone) {
          captionDoneRef.current.set(jobId, currentDone);
          qc.invalidateQueries({ queryKey: ["images", progress.dataset_id] });
          // Frames land in a subfolder that may not exist yet, so the sidebar
          // needs it as the run fills. Deliberately NOT ["dataset", id]: that
          // live invalidation is scoped to workers refreshing Dataset.image_count
          // per row, and video_extract's refresh_stats is terminal-only — it
          // would be one refetch per shot returning an unchanged number.
          if (progress.job_type === "video_extract") {
            qc.invalidateQueries({ queryKey: ["subfolders", progress.dataset_id] });
          }
          if (progress.job_type === "comfy_generate") {
            qc.invalidateQueries({ queryKey: ["subfolders", progress.dataset_id] });
            // Sidebar/gallery image counters, so they climb with the run instead of
            // jumping at the end. Scoped to comfy_generate: its worker refreshes the
            // stored Dataset.image_count per row, so there is something new to read.
            // Caption jobs are in LIVE_IMAGE_JOB_TYPES too but refresh only at the
            // end, so this would be one refetch per image returning the same number.
            qc.invalidateQueries({ queryKey: ["dataset", progress.dataset_id] });
          }
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
    setRestartSlow(false);
    let oldStartTime: number | null = null;
    // Set once a probe fails or the server is unreachable — proof the old
    // process has actually gone down. Only used when we never learned the old
    // start_time (the pre-restart probe below failed): without it, the first
    // 200 we see is most likely the *old* process still shutting down, and
    // reloading into that lands the page on a dying backend.
    let sawDown = false;
    try {
      const res = await fetch("/api/v1/health", { cache: "no-store" });
      if (res.ok) oldStartTime = (await res.json()).start_time ?? null;
    } catch { /* ignore */ }
    await fetch("/api/v1/restart", { method: "POST" }).catch(() => {});

    // Poll until the server reports a *different* start_time (a new process,
    // not the dying old one). Deliberately no deadline: a cold start importing
    // torch can take minutes, and a server not running under the manage-script
    // restart loop needs a manual relaunch. A fixed cutoff that stops polling
    // strands the page here permanently even once the server is back — so
    // instead we keep watching and surface a manual escape hatch below.
    const slowAt = Date.now() + RESTART_SLOW_MS;
    for (;;) {
      await new Promise<void>((r) => setTimeout(r, 1000));
      try {
        const res = await fetch("/api/v1/health", { cache: "no-store" });
        if (res.ok) {
          const data = await res.json();
          // Known old start_time → reload when it changes. Unknown → wait until
          // we've seen the server go down first, so we don't reload into the
          // old process before it exits.
          if (oldStartTime !== null ? data.start_time !== oldStartTime : sawDown) {
            window.location.reload();
            return;
          }
        } else {
          sawDown = true;
        }
      } catch { sawDown = true; /* not up yet */ }
      if (Date.now() > slowAt) setRestartSlow(true);
    }
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
            <span className="pp-label">Uploading files</span>
            <div className="pp-bar">
              <div className="pp-fill" style={{ width: `${Math.round((uploadProgress.done / uploadProgress.total) * 100)}%`, background: uploadProgress.errors > 0 ? "var(--warn)" : undefined }} />
            </div>
            <span className="pp-num mono">{uploadProgress.done} / {uploadProgress.total} files</span>
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
            {/* `total > 0` and not merely `percent >= 0`: a job whose phase
                counts nothing reports a real percent beside a meaningless
                `0 / 0`. Two cases today — `video_extract`'s detection phase,
                which pins both counters to zero because it writes nothing, and
                `ml/download_progress.py`'s `emit_sync`, which hardcodes
                `done: 0, total: 0` while supplying a real percent. Safe for
                everything else: the only jobs created with `total_items=0`
                (import, rescan, duplicate, the three exports, video_extract)
                all set a real total in their own first worker emit. */}
            {(runningJob.percent ?? 0) >= 0 && (runningJob.total ?? 0) > 0 && (
              <span className="pp-num mono">{runningJob.done ?? 0} / {runningJob.total ?? 0}</span>
            )}
            <button
              type="button"
              onClick={() => {
                useJobStore.getState().updateJob(runningJob.job_id, { status: "cancelled", optimistic: true });
                jobsApi.cancel(runningJob.job_id);
              }}
              style={{
                background: "none", border: "none", cursor: "pointer",
                color: "var(--fg-dim)", padding: 0, lineHeight: 1, fontSize: 13,
              }}
              title="Cancel the running job"
            >
              ×
            </button>
          </div>
        )}
        {pendingJobs.length > 0 && (
          <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <span className="badge dot" style={{ color: "var(--fg-dim)" }}>
              {pendingJobs.length} queued
            </span>
            {pendingJobs.slice(0, 3).map((j) => (
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
                    useJobStore.getState().updateJob(j.job_id, { status: "cancelled", optimistic: true });
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
            {pendingJobs.length > 3 && (
              <span
                style={{ fontSize: 11, color: "var(--fg-dim)", padding: "2px 4px", cursor: "default" }}
                title={pendingJobs.slice(3).map((j) => j.label || j.job_type).join("\n")}
              >
                +{pendingJobs.length - 3} more
              </span>
            )}
            <button
              type="button"
              className="btn ghost sm"
              style={{ fontSize: 11, padding: "1px 8px" }}
              title="Cancel every queued job and the currently running one"
              onClick={() => {
                for (const j of pendingJobs) {
                  useJobStore.getState().updateJob(j.job_id, { status: "cancelled", optimistic: true });
                  jobsApi.cancel(j.job_id);
                }
                if (runningJob) {
                  useJobStore.getState().updateJob(runningJob.job_id, { status: "cancelled", optimistic: true });
                  jobsApi.cancel(runningJob.job_id);
                }
              }}
            >
              Cancel all
            </button>
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
      {restarting && (
        <div style={{
          position: "fixed", inset: 0, zIndex: 10000,
          background: "var(--bg)",
          display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "center", gap: 28,
        }}>
          <CrucibleMark size={132} animated />
          <div style={{ textAlign: "center", maxWidth: 380 }}>
            <div style={{ fontSize: 15, fontWeight: 600, letterSpacing: "-0.01em" }}>
              Restarting…
            </div>
            <div style={{
              marginTop: 6, fontSize: 12, color: "var(--fg-dim)",
              fontFamily: "Geist Mono, monospace",
            }}>
              waiting for the server to come back
            </div>
            {restartSlow && (
              <div style={{ marginTop: 20 }}>
                <div style={{ fontSize: 12.5, color: "var(--fg-mute)", lineHeight: 1.5 }}>
                  This is taking longer than usual. Still watching for the server —
                  the page reloads by itself the moment it answers. If it never does,
                  check the terminal: the server only comes back on its own when it
                  was started with <span className="mono">manage</span>.
                </div>
                <button
                  type="button"
                  className="btn sm"
                  style={{ marginTop: 12 }}
                  onClick={() => window.location.reload()}
                >
                  Reload now
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}
