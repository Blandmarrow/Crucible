import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Film } from "lucide-react";
import { videosApi } from "../../api/videos";
import { usePaneNavigate } from "../../hooks/usePaneNavigate";
import { formatDuration } from "../../utils/duration";
import { VIDEO_STRIP_COLLAPSED_KEY } from "../../constants/storage";
import { datasetScopedKey } from "../../utils/persistentState";
import type { Video } from "../../types";

const CARD_W = 168;

/**
 * The dataset's source videos, above the image grid.
 *
 * A strip rather than tiles mixed into the grid: a video is a *source*, not a
 * gallery image, and the frames extracted from it are what land in the grid
 * below. Keeping both on one screen is the point — a later phase hangs the
 * "Extract frames" action off this surface, and the frames appear underneath it.
 *
 * Rendered *outside* the gallery's DndContext by GalleryPage, deliberately. In
 * it, the cards would enter the grid's collision detection and the subfolder
 * drop targets; inside the grid's scroll container they would also sit under the
 * drag-and-drop upload handler.
 *
 * There is no selection here yet. Its only consumer would be batch frame
 * extraction, and a checkbox that enables nothing is worse than no checkbox.
 */
export default function VideoStrip({ datasetId }: { datasetId: string | undefined }) {
  const { go } = usePaneNavigate();
  const storageKey = datasetScopedKey(VIDEO_STRIP_COLLAPSED_KEY, datasetId ?? "");
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(storageKey) === "true");

  const { data: videos } = useQuery({
    queryKey: ["videos", datasetId],
    queryFn: () => videosApi.list(datasetId!),
    enabled: !!datasetId,
  });

  // An image-only dataset looks exactly as it did before this component existed.
  if (!videos || videos.length === 0) return null;

  const toggle = () => {
    const next = !collapsed;
    setCollapsed(next);
    localStorage.setItem(storageKey, String(next));
  };

  return (
    <div style={{ borderBottom: "1px solid var(--line)", padding: "8px 16px", flexShrink: 0 }}>
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

      {!collapsed && (
        <div style={{ display: "flex", gap: 10, overflowX: "auto", paddingTop: 8, paddingBottom: 2 }}>
          {videos.map((v) => (
            <VideoCard
              key={v.id}
              video={v}
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
    </div>
  );
}

function VideoCard({ video, onOpen }: { video: Video; onOpen: () => void }) {
  // `has_poster` is false only for a row whose poster has never been cut. The
  // endpoint backfills one on demand, so the <img> is still worth trying — the
  // glyph below is what shows if it comes back 404 (an undecodable video).
  const [posterFailed, setPosterFailed] = useState(false);
  const showPoster = !posterFailed;

  return (
    <button
      onClick={onOpen}
      title={video.filename}
      style={{
        width: CARD_W, flexShrink: 0, padding: 0, textAlign: "left",
        background: "var(--surface-1)", border: "1px solid var(--line)",
        borderRadius: "var(--r-lg)", overflow: "hidden", cursor: "pointer",
      }}
      onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.borderColor = "var(--line-2)"; }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.borderColor = "var(--line)"; }}
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
    </button>
  );
}
