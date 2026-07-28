import { useEffect, useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ChevronLeft, ChevronRight, Pencil, Save, Scissors, Trash2 } from "lucide-react";
import toast from "react-hot-toast";
import { videosApi } from "../api/videos";
import { usePaneDatasetId, usePaneVideoId } from "../hooks/usePaneDatasetId";
import { usePaneNavigate } from "../hooks/usePaneNavigate";
import { usePaneContext } from "../contexts/PaneContext";
import { usePaneStore } from "../store/paneStore";
import ConfirmDialog from "../components/common/ConfirmDialog";
import JobProgressBar from "../components/common/JobProgressBar";
import LicenseBadge from "../components/common/LicenseBadge";
import ExtractFramesModal from "../components/video/ExtractFramesModal";
import { extractPhaseLabel, useVideoExtractJobs } from "../hooks/useVideoExtractJobs";
import { formatDuration } from "../utils/duration";
import { apiErrorDetail } from "../utils/apiError";
import { safeExternalUrl } from "../utils/url";
import type { Video } from "../types";

function formatSize(bytes: number | null) {
  if (!bytes) return "—";
  return bytes < 1_048_576 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1_048_576).toFixed(1)} MB`;
}

/**
 * One source video: player, metadata, rename, delete.
 *
 * Far smaller than ImageDetailPage and deliberately so — none of crop, upscale,
 * LUT, detection or captioning applies to a container. Those belong to the
 * frames extracted from it, which are ordinary Image rows with their own page.
 */
export default function VideoDetailPage() {
  const datasetId = usePaneDatasetId();
  const videoId = usePaneVideoId();
  const { go: paneGo, back: paneBack } = usePaneNavigate();
  const paneCtx = usePaneContext();
  const activePaneId = usePaneStore((s) => s.activePaneId);
  const qc = useQueryClient();

  const [renameMode, setRenameMode] = useState(false);
  const [renameStem, setRenameStem] = useState("");
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [showExtract, setShowExtract] = useState(false);

  // Live for this video whether or not the modal is open — the job outlives the
  // window that started it, and the persisted id survives a reload too.
  const watchedVideoIds = useMemo(() => (videoId ? [videoId] : []), [videoId]);
  const extractJobs = useVideoExtractJobs(watchedVideoIds);
  const extractJob = videoId ? extractJobs.get(videoId) : undefined;

  const { data: video, isError } = useQuery({
    queryKey: ["video", videoId],
    queryFn: () => videosApi.get(videoId!),
    enabled: !!videoId,
    staleTime: 0,
    // A 404 (deleted video) is terminal — don't burn retries on it. Same
    // short-circuit ImageDetailPage uses.
    retry: (failureCount, err) =>
      (err as { response?: { status?: number } })?.response?.status === 404 ? false : failureCount < 1,
  });

  // Prev/next needs no nav-context plumbing. `["videos", datasetId]` is a single
  // unpaginated query, so the strip's order *is* the navigation order and we can
  // index into it directly — none of gallery-nav-*, injectNavId/removeNavId or
  // the boundary prefetch queries apply here.
  const { data: siblings } = useQuery({
    queryKey: ["videos", datasetId],
    queryFn: () => videosApi.list(datasetId!),
    enabled: !!datasetId,
  });

  // Where this video's frames ended up. Also the number in the delete confirm —
  // "the frames survive" is the non-obvious half of that contract, and it reads
  // as a claim rather than a fact without a count attached.
  const { data: framesSummary } = useQuery({
    queryKey: ["video-frames", videoId],
    queryFn: () => videosApi.framesSummary(videoId!),
    enabled: !!videoId,
  });

  const { currentIndex, prevId, nextId } = useMemo(() => {
    const list: Video[] = siblings ?? [];
    const idx = list.findIndex((v) => v.id === videoId);
    return {
      currentIndex: idx,
      prevId: idx > 0 ? list[idx - 1].id : null,
      nextId: idx >= 0 && idx < list.length - 1 ? list[idx + 1].id : null,
    };
  }, [siblings, videoId]);

  const goTo = (id: string) =>
    paneGo(`/datasets/${datasetId}/video/${id}`, { page: "video-detail", datasetId, videoId: id });

  // Reset the rename editor when the page swaps to another video — adjusted
  // during render rather than in an effect, so a half-typed name can never be
  // shown against the video that replaced it for one frame.
  const [renameFor, setRenameFor] = useState(videoId);
  if (renameFor !== videoId) {
    setRenameFor(videoId);
    setRenameMode(false);
    setRenameStem("");
  }

  // Arrow-key navigation. Two guards beyond the usual: this pane must be the
  // active one in split view, and the <video> element must not have focus —
  // the browser binds arrows to seek there, so stealing them would break
  // keyboard scrubbing on the very element the page exists for.
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (paneCtx && paneCtx.paneId !== activePaneId) return;
      if (showDeleteConfirm || showExtract) return;
      const target = e.target as HTMLElement;
      if (
        target.tagName === "INPUT" || target.tagName === "TEXTAREA" ||
        target.tagName === "SELECT" || target.tagName === "VIDEO" || target.isContentEditable
      ) return;
      if (e.key === "ArrowLeft" && prevId) goTo(prevId);
      if (e.key === "ArrowRight" && nextId) goTo(nextId);
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prevId, nextId, showDeleteConfirm, showExtract, paneCtx, activePaneId, datasetId]);

  const renameMutation = useMutation({
    mutationFn: () => videosApi.rename(videoId!, renameStem),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["video", videoId] });
      qc.invalidateQueries({ queryKey: ["videos", datasetId] });
      setRenameMode(false);
      toast.success("Renamed");
    },
    onError: (err) => toast.error(apiErrorDetail(err, "Rename failed")),
  });

  const deleteMutation = useMutation({
    mutationFn: () => videosApi.delete(videoId!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["videos", datasetId] });
      qc.invalidateQueries({ queryKey: ["datasets"] });
      qc.invalidateQueries({ queryKey: ["dataset-stats", datasetId] });
      qc.removeQueries({ queryKey: ["video", videoId] });
      qc.removeQueries({ queryKey: ["video-frames", videoId] });
      // The frames survive with their lineage cut, so every image payload that
      // carried a source_video_id for this video is now wrong.
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      qc.invalidateQueries({ queryKey: ["image"] });
      setShowDeleteConfirm(false);
      toast.success("Video deleted");
      if (nextId) goTo(nextId);
      else if (prevId) goTo(prevId);
      else paneGo(`/datasets/${datasetId}/gallery`, { page: "gallery", datasetId }, { replace: true });
    },
    onError: (err) => toast.error(apiErrorDetail(err, "Delete failed")),
  });

  if (isError && !video) {
    return (
      <div className="p-8" style={{ color: "var(--fg-mute)" }}>
        <p style={{ color: "var(--bad)", marginBottom: 12 }}>
          Video not found — it may have been deleted or moved.
        </p>
        <button
          className="btn-ghost btn-sm flex items-center gap-1.5"
          onClick={() => paneGo(`/datasets/${datasetId}/gallery`, { page: "gallery", datasetId }, { replace: true })}
        >
          <ArrowLeft size={14} /> Back to gallery
        </button>
      </div>
    );
  }

  if (!video) {
    return <div className="p-8" style={{ color: "var(--fg-mute)" }}>Loading…</div>;
  }

  const provenance = (video.provenance ?? {}) as Record<string, string | undefined>;
  const sourceUrl = safeExternalUrl(provenance.source_url);

  return (
    <div className="flex h-full">
      {/* Left: player */}
      <div className="flex-1 flex flex-col min-w-0">
        <div className="p-3 border-b border-gray-700/50 flex items-center gap-3">
          <button className="btn-ghost btn-sm flex items-center gap-1.5" onClick={() => paneBack({ page: "gallery", datasetId: datasetId ?? "" })}>
            <ArrowLeft size={14} /> Back
          </button>

          {currentIndex >= 0 && (siblings?.length ?? 0) > 1 && (
            <div className="flex items-center gap-1">
              <button className="btn-ghost btn-sm p-1" onClick={() => prevId && goTo(prevId)} disabled={!prevId} title="Previous video (←)">
                <ChevronLeft size={16} />
              </button>
              <span className="text-xs text-gray-500 tabular-nums w-16 text-center">
                {currentIndex + 1} / {siblings?.length ?? 0}
              </span>
              <button className="btn-ghost btn-sm p-1" onClick={() => nextId && goTo(nextId)} disabled={!nextId} title="Next video (→)">
                <ChevronRight size={16} />
              </button>
            </div>
          )}

          <span className="text-sm text-gray-400 truncate">{video.filename}</span>
        </div>

        <div className="flex-1 min-h-0 flex items-center justify-center p-4" style={{ background: "var(--surface-0)" }}>
          {/* preload="metadata" so opening the page does not pull a whole clip
              off disk; the poster covers the frame until playback starts.
              Pointed at the endpoint regardless of `has_poster`, same as
              VideoStrip: it cuts one on demand, so a row that has never had a
              poster gets one here rather than showing a blank frame. */}
          <video
            key={video.id}
            controls
            preload="metadata"
            src={videosApi.fileUrl(video.id)}
            poster={videosApi.posterUrlVersioned(video.id, video.updated_at)}
            style={{ maxWidth: "100%", maxHeight: "100%", outline: "none" }}
          />
        </div>
      </div>

      {/* Right: metadata panel */}
      <div className="w-80 bg-surface-card border-l border-gray-700/50 flex flex-col overflow-y-auto">
        <div className="p-4 border-b border-gray-700/50 space-y-2">
          <h3 className="font-medium text-sm text-gray-300 uppercase tracking-wide">Video Info</h3>
          <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
            <span className="text-gray-500">Dimensions</span>
            <span>{video.width && video.height ? `${video.width}×${video.height}` : "—"}</span>
            <span className="text-gray-500">Duration</span>
            {/* null is *unknown*, never 0:00 — see utils/duration.ts. */}
            <span>{formatDuration(video.duration_ms)}</span>
            <span className="text-gray-500">FPS</span>
            <span>{video.fps ? video.fps.toFixed(2) : "—"}</span>
            <span className="text-gray-500">Codec</span>
            <span>{video.codec_label || "—"}</span>
            <span className="text-gray-500">Size</span>
            <span>{formatSize(video.file_size_bytes)}</span>
            <span className="text-gray-500">Filename</span>
            <span className="min-w-0">
              {renameMode ? (
                <span className="flex items-center gap-1">
                  <input
                    className="input"
                    style={{ fontSize: 11, height: 22, padding: "0 4px", flex: 1, minWidth: 0 }}
                    value={renameStem}
                    onChange={(e) => setRenameStem(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && renameStem.trim()) renameMutation.mutate();
                      if (e.key === "Escape") setRenameMode(false);
                    }}
                    autoFocus
                  />
                  <button className="icon-btn" style={{ width: 20, height: 20 }} onClick={() => renameMutation.mutate()} disabled={!renameStem.trim() || renameMutation.isPending}>
                    <Save size={11} />
                  </button>
                  <button className="icon-btn" style={{ width: 20, height: 20 }} onClick={() => setRenameMode(false)}>✕</button>
                </span>
              ) : (
                <span className="flex items-center gap-1 min-w-0">
                  <span className="truncate font-mono" style={{ fontSize: 11 }}>{video.filename}</span>
                  <button
                    className="icon-btn"
                    style={{ width: 18, height: 18, opacity: 0.5 }}
                    title="Rename (the extension is kept)"
                    onClick={() => { setRenameStem(video.filename.replace(/\.[^.]+$/, "")); setRenameMode(true); }}
                  >
                    <Pencil size={11} />
                  </button>
                </span>
              )}
            </span>
          </div>
        </div>

        {/* Provenance, read-only. A video inherits the dataset default at ingest;
            folder import is where it gets set. */}
        <div className="p-4 border-b border-gray-700/50 space-y-2">
          <h3 className="font-medium text-sm text-gray-300 uppercase tracking-wide">Provenance</h3>
          <div className="flex items-center gap-2">
            <LicenseBadge value={provenance.license} />
          </div>
          {provenance.source_name && (
            <div className="text-xs text-gray-400 break-words">{provenance.source_name}</div>
          )}
          {sourceUrl && (
            <a href={sourceUrl} target="_blank" rel="noreferrer noopener" className="text-xs text-blue-400 break-all">
              {sourceUrl}
            </a>
          )}
          {provenance.attribution && (
            <div className="text-xs text-gray-500 break-words">{provenance.attribution}</div>
          )}
        </div>

        {/* Extraction history. Hidden entirely when there is none — an empty
            panel implies something failed. Each row deep-links the gallery at
            that subfolder via the pane view's `subfolder` field. */}
        {(framesSummary?.total ?? 0) > 0 && (
          <div className="p-4 border-b border-gray-700/50 space-y-2">
            <h3 className="font-medium text-sm text-gray-300 uppercase tracking-wide">Extracted frames</h3>
            <div className="space-y-1">
              {framesSummary!.groups.map((g) => (
                <button
                  key={g.subfolder}
                  className="btn-ghost btn-sm w-full flex items-center justify-between gap-2"
                  style={{ fontSize: 11.5 }}
                  onClick={() =>
                    paneGo(
                      `/datasets/${datasetId}/gallery?subfolder=${encodeURIComponent(g.subfolder)}`,
                      { page: "gallery", datasetId, subfolder: g.subfolder },
                    )
                  }
                  title={`Show these frames in the gallery`}
                >
                  <span>{g.count} frame{g.count === 1 ? "" : "s"}</span>
                  <span className="font-mono truncate" style={{ color: "var(--fg-mute)" }}>
                    {g.subfolder || "root"}
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="p-4 space-y-2">
          {extractJob && (
            <JobProgressBar
              message={extractPhaseLabel(extractJob)}
              percent={Math.max(0, extractJob.percent ?? 0)}
            />
          )}
          <button
            className="btn-ghost btn-sm w-full flex items-center justify-center gap-1.5"
            onClick={() => setShowExtract(true)}
            // Deliberately **not** disabled while a job runs. The modal re-attaches
            // to a live run and shows it as a block above step 1, so disabling this
            // made the documented "reopen to watch it" path unreachable from the
            // one entry point that gated on it — `VideoStrip` never did.
            title={extractJob ? "Show the running extraction" : "Turn this video into frames"}
          >
            <Scissors size={14} /> Extract frames
          </button>
          <button
            className="btn-ghost btn-sm w-full flex items-center justify-center gap-1.5"
            style={{ color: "var(--bad)" }}
            onClick={() => setShowDeleteConfirm(true)}
          >
            <Trash2 size={14} /> Delete video
          </button>
        </div>
      </div>

      {showExtract && (
        <ExtractFramesModal
          datasetId={datasetId}
          videos={[video]}
          onClose={() => {
            setShowExtract(false);
            qc.invalidateQueries({ queryKey: ["video-frames", videoId] });
          }}
        />
      )}

      {showDeleteConfirm && (
        <ConfirmDialog
          danger
          title="Delete video?"
          // The Phase 0 contract, stated because it is not what a user expects:
          // DELETE /videos/{id} never touches Image rows. With a count attached —
          // the frames keep their files and lose only the link back here.
          message={
            (framesSummary?.total ?? 0) > 0
              ? `Delete "${video.filename}" and its poster from disk. ${framesSummary!.total} extracted frame(s) keep their files but lose their link back to this video.`
              : `Delete "${video.filename}" and its poster from disk. Extracted frames are not deleted.`
          }
          confirmLabel="Delete"
          onConfirm={() => deleteMutation.mutate()}
          onCancel={() => setShowDeleteConfirm(false)}
        />
      )}
    </div>
  );
}
