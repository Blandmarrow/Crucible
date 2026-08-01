import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Film, Scissors, Trash2 } from "lucide-react";
import toast from "react-hot-toast";
import { videosApi } from "../../api/videos";
import { usePaneContext } from "../../contexts/PaneContext";
import { usePaneNavigate } from "../../hooks/usePaneNavigate";
import { usePaneStore } from "../../store/paneStore";
import { useSelectionStore } from "../../store/selectionStore";
import { useUiPrefsStore } from "../../store/uiPrefsStore";
import { videoExtractJobKey } from "../../hooks/useVideoExtractJobs";
import { apiErrorDetail } from "../../utils/apiError";
import { formatDuration } from "../../utils/duration";
import { VIDEO_STRIP_COLLAPSED_KEY } from "../../constants/storage";
import { clearPersisted, datasetScopedKey } from "../../utils/persistentState";
import ConfirmDialog from "../common/ConfirmDialog";
import ExtractFramesModal from "../video/ExtractFramesModal";
import GalleryCheckbox from "./GalleryCheckbox";
import type { Video } from "../../types";

const CARD_W = 168;

/**
 * The dataset's source videos, above the image grid.
 *
 * A strip rather than tiles mixed into the grid: a video is a *source*, not a
 * gallery image, and the frames extracted from it are what land in the grid
 * below. Keeping both on one screen is the point — "Extract frames" hangs off
 * this surface, and the frames appear underneath it as the job writes them.
 *
 * Rendered *outside* the gallery's DndContext by GalleryPage, deliberately. In
 * it, the cards would enter the grid's collision detection and the subfolder
 * drop targets; inside the grid's scroll container they would also sit under the
 * drag-and-drop upload handler.
 *
 * **Selection is local state, not `selectionStore`.** That store is image-typed
 * down to `datasetByImageId`, and mixing video ids into it would corrupt
 * `SelectionToolbar`'s cross-dataset breakdown and every bulk-op call site that
 * reads it.
 */
export default function VideoStrip({ datasetId }: { datasetId: string | undefined }) {
  const { go } = usePaneNavigate();
  const qc = useQueryClient();
  const storageKey = datasetScopedKey(VIDEO_STRIP_COLLAPSED_KEY, datasetId ?? "");
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(storageKey) === "true");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [showExtract, setShowExtract] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  // The *image* selection, read only to stand down from the Delete key — see the
  // effect below. The strip still never writes to this store.
  const imageSelectionCount = useSelectionStore((s) => s.count);
  // For the Delete binding's active-pane guard. Read here rather than passed
  // down: GalleryPage consumes neither, so a prop would exist only to thread
  // these two values through it.
  const paneCtx = usePaneContext();
  const activePaneId = usePaneStore((s) => s.activePaneId);
  // Shift-click range anchors — the same pair GalleryPage keeps, resolved here
  // against the strip's own order and its own set.
  const lastSelectedId = useRef<string | null>(null);
  const lastRangeEndId = useRef<string | null>(null);

  const { data: videos } = useQuery({
    queryKey: ["videos", datasetId],
    queryFn: () => videosApi.list(datasetId!),
    enabled: !!datasetId,
  });

  // A selection that outlived the dataset it was made in would extract from
  // videos the user can no longer see. Adjusted during render rather than in an
  // effect, so the stale selection is never painted. The range anchors are left
  // alone deliberately — an id from the previous dataset is simply not in the new
  // `videos` order, so `indexOf` misses and the next click re-anchors.
  //
  // The collapse flag rides along for the same reason: `storageKey` is recomputed
  // per render and is already the *new* dataset's key here, but `collapsed` came
  // from a lazy initializer that ran only on mount — and GalleryPage is not
  // remounted on a dataset change. Worse than merely wrong, it is sticky: `toggle`
  // writes back the key it read, so one click stamps the previous dataset's state
  // onto this one.
  const [selectionFor, setSelectionFor] = useState(datasetId);
  if (selectionFor !== datasetId) {
    setSelectionFor(datasetId);
    setSelected(new Set());
    setShowDeleteConfirm(false);
    setCollapsed(localStorage.getItem(storageKey) === "true");
  }

  /** Delete the selected videos, one request each.
   *
   *  Sequential rather than concurrent and with no bulk endpoint: a strip-sized
   *  selection is a handful of clips, and each `DELETE /videos/{id}` runs its own
   *  `refresh_stats`. Never rejects — a partial failure is the normal outcome
   *  worth reporting (a locked file 409s on Windows), so both halves come back
   *  in the result and the caller decides what to say.
   */
  const deleteMutation = useMutation({
    mutationFn: async (ids: string[]) => {
      const deleted: string[] = [];
      const errors: string[] = [];
      const failedIds: string[] = [];
      for (const id of ids) {
        try {
          await videosApi.delete(id);
          deleted.push(id);
        } catch (err) {
          failedIds.push(id);
          errors.push(apiErrorDetail(err, "Delete failed"));
        }
      }
      return { deleted, failedIds, errors };
    },
    onSuccess: ({ deleted, failedIds, errors }) => {
      for (const id of deleted) {
        qc.removeQueries({ queryKey: ["video", id] });
        // The video is gone, so is any extraction job for it — leaving the key
        // behind means every future mount re-fetches a dead id and 404s on it.
        clearPersisted(videoExtractJobKey(id));
      }
      if (deleted.length > 0) {
        // Mirrors VideoDetailPage's deleteMutation. `images`/`image` because the
        // frames survive with their lineage cut, so every payload that carried a
        // source_video_id for these videos is now wrong.
        qc.invalidateQueries({ queryKey: ["videos", datasetId] });
        qc.invalidateQueries({ queryKey: ["datasets"] });
        qc.invalidateQueries({ queryKey: ["dataset-stats", datasetId] });
        qc.invalidateQueries({ queryKey: ["images", datasetId] });
        qc.invalidateQueries({ queryKey: ["image"] });
        qc.invalidateQueries({ queryKey: ["video-frames"] });
        toast.success(`${deleted.length} video${deleted.length === 1 ? "" : "s"} deleted`);
      }
      // The first detail verbatim, so a 409 from a locked file reaches the user
      // with its actionable wording intact rather than as "3 failed".
      if (errors.length > 0) toast.error(errors[0]);
      setShowDeleteConfirm(false);
      // What failed stays selected, so retrying is one keypress rather than a
      // fresh selection.
      setSelected(new Set(failedIds));
    },
  });

  // Delete opens the confirm. Beyond SelectionToolbar's guards (text fields, any
  // open modal) there are three more: only the *active* pane responds, a focused
  // <video> owns its own keys, and the *image* selection wins outright — with
  // both kinds selected Delete keeps its existing image behaviour.
  //
  // The pane guard is what keeps one keypress from opening two confirms:
  // `splitPane` clones the current view, so two gallery panes mount two strips,
  // and a gallery pane beside an ImageDetailPage gives one of each. The
  // `paneCtx &&` short-circuit is load-bearing — in non-pane mode (the default)
  // `paneCtx` is null, and an unconditional compare would kill the binding
  // outright. Same idiom as VideoDetailPage/ImageDetailPage.
  //
  // `SelectionToolbar`'s own Delete binding is knowingly unguarded; that is
  // pre-existing and out of scope here.
  useEffect(() => {
    if (selected.size === 0) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Delete") return;
      if (paneCtx && paneCtx.paneId !== activePaneId) return;
      if (showExtract || showDeleteConfirm) return;
      if (imageSelectionCount > 0) return;
      const target = e.target as HTMLElement;
      if (
        target.tagName === "INPUT" || target.tagName === "TEXTAREA" ||
        target.tagName === "SELECT" || target.tagName === "VIDEO" || target.isContentEditable
      ) return;
      e.preventDefault();
      setShowDeleteConfirm(true);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selected, showExtract, showDeleteConfirm, imageSelectionCount, paneCtx, activePaneId]);

  // An image-only dataset looks exactly as it did before this component existed.
  if (!videos || videos.length === 0) return null;

  const toggle = () => {
    const next = !collapsed;
    setCollapsed(next);
    localStorage.setItem(storageKey, String(next));
  };

  /** Ported from GalleryPage's `handleSelect`: shift extends from the anchor and
   *  *replaces* the previous range, so dragging the end backwards deselects what
   *  it passes rather than leaving a trail. */
  const handleSelect = (id: string, shiftKey: boolean) => {
    const ids = videos.map((v) => v.id);
    setSelected((prev) => {
      const next = new Set(prev);
      if (shiftKey && lastSelectedId.current !== null) {
        const a = ids.indexOf(lastSelectedId.current);
        const b = ids.indexOf(id);
        if (a !== -1 && b !== -1) {
          if (lastRangeEndId.current !== null) {
            const prevB = ids.indexOf(lastRangeEndId.current);
            if (prevB !== -1) {
              for (const rid of ids.slice(Math.min(a, prevB), Math.max(a, prevB) + 1)) next.delete(rid);
            }
          }
          for (const rid of ids.slice(Math.min(a, b), Math.max(a, b) + 1)) next.add(rid);
          lastRangeEndId.current = id;
          return next;
        }
      }
      if (next.has(id)) next.delete(id);
      else next.add(id);
      lastSelectedId.current = id;
      lastRangeEndId.current = null;
      return next;
    });
  };

  const selectedVideos = videos.filter((v) => selected.has(v.id));

  return (
    <div style={{ borderBottom: "1px solid var(--line)", padding: "8px 16px", flexShrink: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <button
          className="btn-ghost btn-sm"
          style={{ display: "flex", alignItems: "center", gap: 6, padding: "2px 6px" }}
          onClick={toggle}
          aria-expanded={!collapsed}
        >
          {collapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
          <span style={{ fontSize: 12, fontWeight: 600 }}>Videos</span>
          <span style={{ fontSize: 12, color: "var(--fg-mute)" }}>({videos.length})</span>
        </button>

        {selectedVideos.length > 0 && (
          <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
            <span style={{ color: "var(--fg-mute)" }}>{selectedVideos.length} selected</span>
            <button
              className="btn primary sm"
              style={{ display: "flex", alignItems: "center", gap: 5 }}
              onClick={() => setShowExtract(true)}
            >
              <Scissors size={13} /> Extract frames
            </button>
            <button
              className="btn ghost sm"
              style={{ display: "flex", alignItems: "center", gap: 5, color: "var(--bad)" }}
              onClick={() => setShowDeleteConfirm(true)}
              disabled={deleteMutation.isPending}
              title="Delete the selected videos (Delete)"
            >
              <Trash2 size={13} /> Delete
            </button>
            <button className="btn ghost sm" onClick={() => setSelected(new Set())}>Clear</button>
          </div>
        )}
      </div>

      {!collapsed && (
        <div style={{ display: "flex", gap: 10, overflowX: "auto", paddingTop: 8, paddingBottom: 2 }}>
          {videos.map((v) => (
            <VideoCard
              key={v.id}
              video={v}
              selected={selected.has(v.id)}
              onSelect={(shiftKey) => handleSelect(v.id, shiftKey)}
              onOpen={() =>
                go(`/datasets/${datasetId}/video/${v.id}`, {
                  page: "video-detail",
                  datasetId,
                  videoId: v.id,
                })
              }
            />
          ))}
        </div>
      )}

      {showDeleteConfirm && selectedVideos.length > 0 && (
        <ConfirmDialog
          danger
          title={`Delete ${selectedVideos.length} video${selectedVideos.length === 1 ? "" : "s"}?`}
          // The Phase 0 contract, stated because it is not what a user expects:
          // DELETE /videos/{id} never touches Image rows. Deliberately with no
          // frame count — VideoDetailPage gets one from GET /frames-summary, but
          // a bulk confirm would need one request per video to say the same thing.
          message={
            `Delete ${selectedVideos.length === 1 ? `"${selectedVideos[0].filename}"` : `${selectedVideos.length} videos`} and their posters from disk. ` +
            "Extracted frames keep their files and lose only their link back to the video."
          }
          confirmLabel="Delete"
          onConfirm={() => deleteMutation.mutate(selectedVideos.map((v) => v.id))}
          onCancel={() => setShowDeleteConfirm(false)}
        />
      )}

      {showExtract && selectedVideos.length > 0 && (
        <ExtractFramesModal
          datasetId={datasetId}
          videos={selectedVideos}
          onClose={() => {
            setShowExtract(false);
            qc.invalidateQueries({ queryKey: ["video-frames"] });
          }}
        />
      )}
    </div>
  );
}

function VideoCard({
  video, selected, onSelect, onOpen,
}: {
  video: Video;
  selected: boolean;
  onSelect: (shiftKey: boolean) => void;
  onOpen: () => void;
}) {
  // `has_poster` is false only for a row whose poster has never been cut. The
  // endpoint backfills one on demand, so the <img> is still worth trying — the
  // glyph below is what shows if it comes back 404 (an undecodable video).
  //
  // The failure is scoped to the *URL*, not the mount: `posterUrlVersioned`
  // carries `updated_at`, which bumps when an extraction backfills a poster, so a
  // new URL is retried while the one that 404'd stays on the glyph. A boolean
  // would keep showing the glyph for the rest of the mount. The comparison is the
  // reset — no render-adjust needed.
  const posterUrl = videosApi.posterUrlVersioned(video.id, video.updated_at);
  const [failedPosterUrl, setFailedPosterUrl] = useState<string | null>(null);
  const showPoster = failedPosterUrl !== posterUrl;
  const cbSize = useUiPrefsStore((s) => s.galleryCheckboxSize);

  // A <div role="button">, not a <button>: the checkbox is an interactive
  // control and cannot legally nest inside one. Enter/Space are re-implemented
  // because that is what the element type stopped providing.
  return (
    <div
      role="button"
      tabIndex={0}
      // Without this the card takes its name from its contents — the checkbox's
      // "Select clip.mp4", the duration badge and the filename all concatenated,
      // so a screen reader announces "Select clip.mp4 0:02 clip.mp4".
      aria-label={video.filename}
      onClick={(e) => { if (e.shiftKey) onSelect(true); else onOpen(); }}
      onMouseDown={(e) => { if (e.shiftKey) e.preventDefault(); }}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(); }
      }}
      title={video.filename}
      style={{
        width: CARD_W, flexShrink: 0, padding: 0, textAlign: "left",
        background: "var(--surface-1)",
        border: `1px solid ${selected ? "var(--accent)" : "var(--line)"}`,
        boxShadow: selected ? "0 0 0 1px var(--accent)" : "none",
        borderRadius: "var(--r-lg)", overflow: "hidden", cursor: "pointer",
      }}
      onMouseEnter={(e) => { if (!selected) (e.currentTarget as HTMLElement).style.borderColor = "var(--line-2)"; }}
      onMouseLeave={(e) => { if (!selected) (e.currentTarget as HTMLElement).style.borderColor = "var(--line)"; }}
    >
      <div style={{ aspectRatio: "16/9", background: "var(--surface-3)", position: "relative" }}>
        {showPoster ? (
          <img
            src={posterUrl}
            alt={video.filename}
            onError={() => setFailedPosterUrl(posterUrl)}
            style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
            loading="lazy"
          />
        ) : (
          <div style={{ width: "100%", height: "100%", display: "grid", placeContent: "center", color: "var(--fg-mute)" }}>
            <Film size={24} />
          </div>
        )}

        {/* Same size as the grid's checkboxes (Settings → Gallery), so the two
            surfaces read as one selection model. */}
        <div
          data-testid="video-checkbox"
          role="checkbox"
          aria-checked={selected}
          aria-label={`Select ${video.filename}`}
          tabIndex={0}
          style={{ position: "absolute", top: 4, left: 4, padding: 4, zIndex: 3 }}
          onClick={(e) => { e.stopPropagation(); onSelect(e.shiftKey); }}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); onSelect(e.shiftKey); }
          }}
        >
          <GalleryCheckbox size={cbSize} selected={selected} />
        </div>

        <span
          style={{
            position: "absolute", right: 4, bottom: 4,
            background: "rgba(7,9,11,.78)", color: "var(--fg)",
            fontSize: 10.5, fontVariantNumeric: "tabular-nums",
            padding: "1px 4px", borderRadius: 3,
          }}
        >
          {formatDuration(video.duration_ms)}
        </span>
      </div>
      <div
        style={{
          fontSize: 11, padding: "5px 6px", color: "var(--fg-mute)",
          whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
        }}
      >
        {video.filename}
      </div>
    </div>
  );
}
