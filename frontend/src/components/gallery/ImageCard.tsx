import { Cpu } from "lucide-react";
import type { ImageListItem } from "../../types";
import { imagesApi } from "../../api/images";
import { useSelectionStore } from "../../store/selectionStore";
import { usePaneDatasetId } from "../../hooks/usePaneDatasetId";
import { usePaneNavigate } from "../../hooks/usePaneNavigate";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";

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
}

export default function ImageCard({ image, onShowGenMeta, onSelect, isDraggable, isActiveDrag }: Props) {
  const datasetId = usePaneDatasetId();
  const { go } = usePaneNavigate();
  const { toggle, isSelected } = useSelectionStore();
  const selected = isSelected(image.id);
  const isDuplicate = image.quality_flags?.is_duplicate as boolean | undefined;
  const isBlurry = image.quality_flags?.is_blurry as boolean | undefined;
  const hasWatermark = image.quality_flags?.has_watermark as boolean | undefined;
  const isUniform = image.quality_flags?.is_uniform as boolean | undefined;
  const sc = image.aesthetic_score ?? null;
  const cls = scoreClass(sc);

  return (
    <div
      style={{
        border: selected ? "1px solid var(--accent)" : "1px solid var(--line)",
        boxShadow: selected ? "0 0 0 1px var(--accent), 0 0 24px -8px var(--accent-glow)" : "none",
        borderRadius: "var(--r-lg)",
        overflow: "hidden",
        background: "var(--surface-1)",
        cursor: isActiveDrag ? "grabbing" : isDraggable ? "grab" : "pointer",
        transition: "border-color .12s",
        position: "relative",
      }}
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
        <img
          src={imagesApi.thumbnailUrlVersioned(image.id, image.updated_at)}
          alt={image.filename}
          style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
          loading="lazy"
        />

        {/* Checkbox */}
        <div
          style={{ position: "absolute", top: 8, left: 8, zIndex: 3 }}
          onClick={(e) => { e.stopPropagation(); onSelect ? onSelect(image.id, e.shiftKey, true) : toggle(image.id, datasetId ?? ""); }}
        >
          <div style={{
            width: 18, height: 18,
            background: selected ? "var(--accent)" : "rgba(7,9,11,.55)",
            border: selected ? "1.5px solid var(--accent)" : "1.5px solid rgba(255,255,255,.5)",
            borderRadius: 4,
            display: "grid", placeContent: "center",
            backdropFilter: "blur(4px)",
          }}>
            {selected && (
              <svg viewBox="0 0 12 10" width="9" height="9" fill="none">
                <path d="M1 5l3 4L11 1" stroke="#03130d" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            )}
          </div>
        </div>

        {/* Quality flags */}
        {(isDuplicate || isBlurry || hasWatermark || isUniform) && (
          <div style={{ position: "absolute", top: 8, right: 8, zIndex: 3, display: "flex", gap: 4 }}>
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

export function SortableImageCard({ image, onShowGenMeta, onSelect }: Props) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: image.id });
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
