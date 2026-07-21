import { useState } from "react";
import { Cpu } from "lucide-react";
import toast from "react-hot-toast";
import { useQueryClient } from "@tanstack/react-query";
import type { ImageListItem } from "../../types";
import { imagesApi } from "../../api/images";
import { captionsApi } from "../../api/captions";
import { useSelectionStore } from "../../store/selectionStore";
import { useUiPrefsStore } from "../../store/uiPrefsStore";
import GalleryCheckbox from "./GalleryCheckbox";
import { usePaneDatasetId } from "../../hooks/usePaneDatasetId";
import { usePaneNavigate } from "../../hooks/usePaneNavigate";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";

/** Invisible padding around the checkbox that widens its click target. */
const CB_PAD = 4;

function scoreClass(score: number | null) {
  if (score === null) return "";
  if (score >= 6) return "good";
  if (score >= 4) return "warn";
  return "bad";
}

interface Props {
  image: ImageListItem;
  onShowGenMeta?: (image: ImageListItem) => void;
  onSelect?: (id: string, shiftKey: boolean, isCheckbox: boolean) => void;
  isDraggable?: boolean;
  isActiveDrag?: boolean;
  /** Whether the card participates in sort-reordering. When false it stays draggable
   *  (so it can be dropped on a subfolder row) but is not itself a drop target. */
  sortable?: boolean;
}

export default function ImageCard({ image, onShowGenMeta, onSelect, isDraggable, isActiveDrag }: Props) {
  const datasetId = usePaneDatasetId();
  const qc = useQueryClient();
  const { go } = usePaneNavigate();
  const { toggle, isSelected } = useSelectionStore();
  const selected = isSelected(image.id);
  const cbSize = useUiPrefsStore((s) => s.galleryCheckboxSize);
  const [captionDragOver, setCaptionDragOver] = useState(false);

  // Drag a .txt file onto a card to apply it as that image's caption.
  // Only intervenes when the drag carries a text file — image drops still bubble
  // up to the gallery grid's upload drop zone.
  const handleCaptionDragOver = (e: React.DragEvent) => {
    const items = e.dataTransfer.items;
    // `.txt` files often report an empty `type` (Windows / some browsers), so accept
    // both. Images always report a concrete `image/*` type and still pass through to
    // the grid uploader; the drop handler's `.endsWith(".txt")` guard is the real filter.
    const hasTxt = items && Array.from(items).some((it) => it.kind === "file" && (it.type === "text/plain" || it.type === ""));
    if (!hasTxt) return;
    e.preventDefault();
    e.stopPropagation();
    if (!captionDragOver) setCaptionDragOver(true);
  };
  const handleCaptionDragLeave = (e: React.DragEvent) => {
    if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setCaptionDragOver(false);
  };
  const handleCaptionDrop = (e: React.DragEvent) => {
    const files = Array.from(e.dataTransfer.files || []);
    const txt = files.find((f) => f.name.toLowerCase().endsWith(".txt"));
    if (!txt) return; // not a caption drop — let it bubble to the image uploader
    e.preventDefault();
    e.stopPropagation();
    setCaptionDragOver(false);
    txt.text()
      .then((text) => captionsApi.update(image.id, { caption_text: text.trim() }))
      .then(() => {
        toast.success("Caption applied");
        // Invalidate every cache the caption feeds, so the detail view (and stats) don't
        // show a stale caption. Mirrors ImageDetailPage's own save-caption invalidations.
        qc.invalidateQueries({ queryKey: ["images", datasetId] });
        qc.invalidateQueries({ queryKey: ["caption", image.id] });
        qc.invalidateQueries({ queryKey: ["image", image.id] });
        qc.invalidateQueries({ queryKey: ["dataset", datasetId] });
        qc.invalidateQueries({ queryKey: ["dataset-stats", datasetId] });
        qc.invalidateQueries({ queryKey: ["tag-stats", datasetId] });
        qc.invalidateQueries({ queryKey: ["tag-cooccurrence", datasetId] });
      })
      .catch(() => toast.error("Failed to apply caption"));
  };
  const isDuplicate = image.quality_flags?.is_duplicate as boolean | undefined;
  const isBlurry = image.quality_flags?.is_blurry as boolean | undefined;
  const hasWatermark = image.quality_flags?.has_watermark as boolean | undefined;
  const isUniform = image.quality_flags?.is_uniform as boolean | undefined;
  const isNsfw = image.quality_flags?.is_nsfw as boolean | undefined;
  const hasAiArtifacts = image.quality_flags?.has_ai_artifacts as boolean | undefined;
  const sc = image.aesthetic_score ?? null;
  const cls = scoreClass(sc);

  return (
    <div
      style={{
        border: captionDragOver ? "1px solid var(--accent)" : selected ? "1px solid var(--accent)" : "1px solid var(--line)",
        boxShadow: selected ? "0 0 0 1px var(--accent), 0 0 24px -8px var(--accent-glow)" : "none",
        borderRadius: "var(--r-lg)",
        overflow: "hidden",
        background: "var(--surface-1)",
        cursor: isActiveDrag ? "grabbing" : isDraggable ? "grab" : "pointer",
        transition: "border-color .12s",
        position: "relative",
      }}
      onDragOver={handleCaptionDragOver}
      onDragLeave={handleCaptionDragLeave}
      onDrop={handleCaptionDrop}
      onClick={(e) => {
        if (e.shiftKey && onSelect) { onSelect(image.id, true, false); }
        else { go(`/datasets/${datasetId}/image/${image.id}`, { page: "image-detail", datasetId, imageId: image.id }); }
      }}
      onMouseDown={(e) => { if (e.shiftKey) e.preventDefault(); }}
      onMouseEnter={(e) => { if (!selected) (e.currentTarget as HTMLElement).style.borderColor = "var(--line-2)"; }}
      onMouseLeave={(e) => { if (!selected) (e.currentTarget as HTMLElement).style.borderColor = "var(--line)"; }}
    >
      {/* Thumbnail */}
      <div style={{ aspectRatio: "1/1", background: "var(--surface-2)", position: "relative", overflow: "hidden" }}>
        {captionDragOver && (
          <div style={{
            position: "absolute", inset: 0, zIndex: 4, pointerEvents: "none",
            background: "rgba(7,9,11,.6)", display: "grid", placeContent: "center",
            border: "2px dashed var(--accent)", borderRadius: "var(--r-lg)",
            color: "var(--accent)", fontSize: 11.5, fontWeight: 600, textAlign: "center", padding: 8,
          }}>
            Drop .txt to set caption
          </div>
        )}
        <img
          src={imagesApi.thumbnailUrlVersioned(image.id, image.updated_at)}
          alt={image.filename}
          style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
          loading="lazy"
        />

        {/* Checkbox — size is user-configurable (Settings → Gallery). The 4px pad
            extends the click target past the visual box; the matching negative
            offset keeps the box itself pinned at the card's 8px corner inset. */}
        <div
          style={{ position: "absolute", top: 8 - CB_PAD, left: 8 - CB_PAD, padding: CB_PAD, zIndex: 3 }}
          onClick={(e) => { e.stopPropagation(); onSelect ? onSelect(image.id, e.shiftKey, true) : toggle(image.id, datasetId ?? ""); }}
        >
          <GalleryCheckbox size={cbSize} selected={selected} />
        </div>

        {/* Quality flags */}
        {(isDuplicate || isBlurry || hasWatermark || isUniform || isNsfw || hasAiArtifacts) && (
          <div style={{ position: "absolute", top: 8, right: 8, zIndex: 3, display: "flex", gap: 4 }}>
            {isNsfw && (
              <span title="NSFW" style={{ width: 18, height: 18, borderRadius: 4, background: "rgba(7,9,11,.7)", backdropFilter: "blur(4px)", display: "grid", placeContent: "center", border: "1px solid var(--line-2)", color: "var(--bad)" }}>
                <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M2 2l12 12M6.5 6.5A3.5 3.5 0 0 0 4.5 9.5M9.5 9.5A3.5 3.5 0 0 0 11.5 6.5M3 4C1.5 5.5 1 7 1 8s.5 2.5 2 4M13 4c1.5 1.5 2 3 2 4s-.5 2.5-2 4"/></svg>
              </span>
            )}
            {hasAiArtifacts && (
              <span title="AI artifacts" style={{ width: 18, height: 18, borderRadius: 4, background: "rgba(7,9,11,.7)", backdropFilter: "blur(4px)", display: "grid", placeContent: "center", border: "1px solid var(--line-2)", color: "var(--warn)" }}>
                <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6"><circle cx="8" cy="8" r="5.5"/><path d="M5.5 7.5C5.5 6.4 6.4 5.5 7.5 5.5h1C9.6 5.5 10.5 6.4 10.5 7.5c0 .8-.5 1.5-1.2 1.8L9 9.5V11"/><circle cx="9" cy="12.5" r=".5" fill="currentColor" stroke="none"/></svg>
              </span>
            )}
            {isDuplicate && (
              <span title="Duplicate" style={{ width: 18, height: 18, borderRadius: 4, background: "rgba(7,9,11,.7)", backdropFilter: "blur(4px)", display: "grid", placeContent: "center", border: "1px solid var(--line-2)", color: "var(--info)" }}>
                <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6"><rect x="2.5" y="2.5" width="9" height="9" rx="1"/><rect x="5.5" y="5.5" width="8" height="8" rx="1"/></svg>
              </span>
            )}
            {isBlurry && (
              <span title="Blurry" style={{ width: 18, height: 18, borderRadius: 4, background: "rgba(7,9,11,.7)", backdropFilter: "blur(4px)", display: "grid", placeContent: "center", border: "1px solid var(--line-2)", color: "var(--warn)" }}>
                <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6"><circle cx="8" cy="8" r="5.5"/><path d="M8 5v3.5"/></svg>
              </span>
            )}
            {hasWatermark && (
              <span title="Watermark" style={{ width: 18, height: 18, borderRadius: 4, background: "rgba(7,9,11,.7)", backdropFilter: "blur(4px)", display: "grid", placeContent: "center", border: "1px solid var(--line-2)", color: "var(--info)" }}>
                <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M3 6h10M3 9h7"/></svg>
              </span>
            )}
            {isUniform && (
              <span title="Near-uniform" style={{ width: 18, height: 18, borderRadius: 4, background: "rgba(7,9,11,.7)", backdropFilter: "blur(4px)", display: "grid", placeContent: "center", border: "1px solid var(--line-2)", color: "var(--warn)" }}>
                <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6"><rect x="3" y="3" width="10" height="10"/></svg>
              </span>
            )}
          </div>
        )}

        {/* Aesthetic score badge */}
        <div style={{ position: "absolute", bottom: 8, right: 8, zIndex: 3 }}>
          <span style={{
            padding: "2px 7px", borderRadius: 4,
            font: '600 11px "Geist Mono", monospace',
            background: "rgba(7,9,11,.75)", backdropFilter: "blur(4px)",
            border: `1px solid ${cls === "good" ? "rgba(16,185,129,.4)" : cls === "warn" ? "rgba(210,154,58,.4)" : cls === "bad" ? "rgba(214,98,74,.4)" : "var(--line-2)"}`,
            color: cls === "good" ? "var(--good)" : cls === "warn" ? "var(--warn)" : cls === "bad" ? "var(--bad)" : "var(--fg-dim)",
          }}>
            {sc !== null ? sc.toFixed(1) : "—"}
          </span>
        </div>
      </div>

      {/* Footer */}
      <div style={{ padding: "8px 10px", display: "flex", flexDirection: "column", gap: 2 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <div style={{ fontSize: 11.5, color: "var(--fg)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", flex: 1 }} title={image.filename}>
            {image.filename}
          </div>
          {image.generation_metadata && onShowGenMeta && (
            <button
              className="icon-btn"
              title="View generation info"
              style={{ width: 18, height: 18, flexShrink: 0, color: "var(--accent)" }}
              onClick={(e) => { e.stopPropagation(); onShowGenMeta(image); }}
            >
              <Cpu size={11} />
            </button>
          )}
        </div>
        {image.width && image.height && (
          <div style={{ fontSize: 10.5, color: "var(--fg-dim)", fontFamily: "Geist Mono, monospace" }}>{image.width}×{image.height}</div>
        )}
        {image.caption_text ? (
          <div style={{ fontSize: 11, color: "var(--fg-mute)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", paddingTop: 4, borderTop: "1px dashed var(--line)", marginTop: 4 }}>
            {image.caption_text}
          </div>
        ) : (
          <div style={{ fontSize: 11, color: "var(--fg-soft)", paddingTop: 4, borderTop: "1px dashed var(--line)", marginTop: 4 }}>No caption</div>
        )}
      </div>
    </div>
  );
}

export function SortableImageCard({ image, onShowGenMeta, onSelect, sortable = true }: Props) {
  // Outside custom-order mode the card must still be draggable (to drop onto a subfolder
  // row) but must not be a drop target itself, so `over.id` can only ever be a subfolder.
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: image.id,
    disabled: { draggable: false, droppable: !sortable },
  });
  return (
    <div
      ref={setNodeRef}
      {...attributes}
      {...listeners}
      style={{
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.45 : 1,
        touchAction: "none",
      }}
    >
      <ImageCard image={image} onShowGenMeta={onShowGenMeta} onSelect={onSelect} isDraggable isActiveDrag={isDragging} />
    </div>
  );
}
