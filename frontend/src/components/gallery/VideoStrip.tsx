import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Film, Scissors } from "lucide-react";
import { videosApi } from "../../api/videos";
import { usePaneNavigate } from "../../hooks/usePaneNavigate";
import { useUiPrefsStore } from "../../store/uiPrefsStore";
import { formatDuration } from "../../utils/duration";
import { VIDEO_STRIP_COLLAPSED_KEY } from "../../constants/storage";
import { datasetScopedKey } from "../../utils/persistentState";
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
  const [selectionFor, setSelectionFor] = useState(datasetId);
  if (selectionFor !== datasetId) {
    setSelectionFor(datasetId);
    setSelected(new Set());
  }

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
  const [posterFailed, setPosterFailed] = useState(false);
  const showPoster = !posterFailed;
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
            src={videosApi.posterUrlVersioned(video.id, video.updated_at)}
            alt={video.filename}
            onError={() => setPosterFailed(true)}
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
