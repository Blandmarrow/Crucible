import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ScanSearch, ChevronUp, ChevronDown, Trash2, Focus, Combine, Save } from "lucide-react";
import toast from "react-hot-toast";
import { detectionApi } from "../../api/detection";
import type { Detection } from "../../types";

const BBOX_COLORS = ["#f87171","#fb923c","#facc15","#4ade80","#34d399","#22d3ee","#818cf8","#c084fc","#f472b6","#94a3b8"];
function labelColor(label: string): string {
  let h = 0;
  for (let i = 0; i < label.length; i++) h = (h * 31 + label.charCodeAt(i)) & 0xffffffff;
  return BBOX_COLORS[Math.abs(h) % BBOX_COLORS.length];
}

interface Props {
  imageId: string;
  datasetId: string;
  detections: Detection[];
  hiddenLabels: Set<string>;
  onToggleLabel: (label: string) => void;
  onOpenDetectModal: () => void;
  onStartRefine: (det: Detection) => void;
  onCropFromDetections: () => void;
  refineTargetId: number | null;
  busy: boolean;
}

export default function DetectionsPanel({
  imageId,
  datasetId,
  detections,
  hiddenLabels,
  onToggleLabel,
  onOpenDetectModal,
  onStartRefine,
  onCropFromDetections,
  refineTargetId,
  busy,
}: Props) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(true);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editLabel, setEditLabel] = useState("");
  const [mergeIds, setMergeIds] = useState<number[]>([]);   // selection order preserved

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["image", imageId] });
    qc.invalidateQueries({ queryKey: ["detection-labels", datasetId] });
  };

  const relabel = useMutation({
    mutationFn: ({ id, label }: { id: number; label: string }) =>
      detectionApi.updateDetection(id, label),
    onSuccess: () => { setEditingId(null); invalidate(); },
    onError: () => toast.error("Failed to rename detection"),
  });

  const remove = useMutation({
    mutationFn: (id: number) => detectionApi.deleteDetection(id),
    onSuccess: (_d, id) => {
      setMergeIds((prev) => prev.filter((m) => m !== id));
      invalidate();
    },
    onError: () => toast.error("Failed to delete detection"),
  });

  const merge = useMutation({
    mutationFn: (ids: number[]) => detectionApi.merge(ids),
    onSuccess: () => { setMergeIds([]); invalidate(); toast.success("Detections merged"); },
    onError: () => toast.error("Failed to merge detections"),
  });

  const toggleMerge = (id: number) =>
    setMergeIds((prev) => (prev.includes(id) ? prev.filter((m) => m !== id) : [...prev, id]));

  const count = detections.length;
  const visibleCount = detections.filter((d) => !hiddenLabels.has(d.label)).length;

  // Label chips (visibility toggles) — grouped counts.
  const labelCounts = Object.entries(
    detections.reduce((acc, d) => { acc[d.label] = (acc[d.label] ?? 0) + 1; return acc; }, {} as Record<string, number>)
  ).sort((a, b) => b[1] - a[1]);

  // Per-detection rows, sorted by label then id.
  const rows = [...detections].sort((a, b) => a.label.localeCompare(b.label) || a.id - b.id);

  const firstMergeLabel = mergeIds.length > 0
    ? detections.find((d) => d.id === mergeIds[0])?.label ?? ""
    : "";

  return (
    <div style={{ marginTop: 10, borderTop: "1px solid var(--line)", paddingTop: 8 }}>
      <button
        style={{ display: "flex", alignItems: "center", gap: 6, width: "100%", justifyContent: "space-between", padding: "2px 0", background: "none", border: "none", cursor: "pointer", color: "inherit" }}
        onClick={() => setOpen((v) => !v)}
      >
        <span style={{ fontSize: 11, fontWeight: 600, color: "var(--fg-mute)", textTransform: "uppercase", letterSpacing: "0.05em", display: "flex", alignItems: "center", gap: 5 }}>
          <ScanSearch size={11} /> Detections
          {count > 0 && <span style={{ fontWeight: 400, textTransform: "none" }}>({count})</span>}
        </span>
        {open ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
      </button>

      {open && (
        <div style={{ marginTop: 6 }}>
          {count > 0 ? (
            <>
              {/* Label chips (overlay visibility) */}
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                {labelCounts.map(([label, n]) => {
                  const hidden = hiddenLabels.has(label);
                  return (
                    <button
                      key={label}
                      onClick={() => onToggleLabel(label)}
                      title={hidden ? "Show boxes" : "Hide boxes"}
                      style={{
                        fontSize: 11, padding: "2px 7px", borderRadius: 4,
                        background: hidden ? "transparent" : labelColor(label) + "33",
                        border: `1px solid ${hidden ? labelColor(label) + "44" : labelColor(label) + "88"}`,
                        color: hidden ? "var(--fg-mute)" : "var(--fg)",
                        whiteSpace: "nowrap", cursor: "pointer",
                        opacity: hidden ? 0.45 : 1,
                        textDecoration: hidden ? "line-through" : "none",
                      }}
                    >
                      {label}{n > 1 && <span style={{ opacity: 0.6, marginLeft: 3 }}>×{n}</span>}
                    </button>
                  );
                })}
              </div>

              {/* Per-detection rows */}
              <div style={{ display: "flex", flexDirection: "column", gap: 2, marginTop: 8 }}>
                {rows.map((det) => {
                  const active = refineTargetId === det.id;
                  return (
                    <div
                      key={det.id}
                      style={{
                        display: "flex", alignItems: "center", gap: 5,
                        padding: "3px 4px", borderRadius: 4, fontSize: 11,
                        background: active ? "var(--surface-3)" : mergeIds.includes(det.id) ? labelColor(det.label) + "22" : "transparent",
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={mergeIds.includes(det.id)}
                        onChange={() => toggleMerge(det.id)}
                        title="Select for merge"
                        style={{ cursor: "pointer" }}
                      />
                      <span style={{ width: 8, height: 8, borderRadius: "50%", background: labelColor(det.label), flexShrink: 0 }} />
                      {editingId === det.id ? (
                        <input
                          className="input"
                          style={{ fontSize: 11, height: 20, padding: "0 4px", flex: 1, minWidth: 0 }}
                          value={editLabel}
                          onChange={(e) => setEditLabel(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" && editLabel.trim()) relabel.mutate({ id: det.id, label: editLabel.trim() });
                            if (e.key === "Escape") setEditingId(null);
                          }}
                          autoFocus
                        />
                      ) : (
                        <button
                          onClick={() => { setEditingId(det.id); setEditLabel(det.label); }}
                          title="Click to rename"
                          style={{ flex: 1, minWidth: 0, textAlign: "left", background: "none", border: "none", color: "var(--fg)", cursor: "pointer", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                        >
                          {det.label}
                        </button>
                      )}
                      {editingId === det.id ? (
                        <button className="icon-btn" style={{ width: 18, height: 18 }} title="Save" onClick={() => editLabel.trim() && relabel.mutate({ id: det.id, label: editLabel.trim() })} disabled={!editLabel.trim() || relabel.isPending}>
                          <Save size={11} />
                        </button>
                      ) : (
                        <>
                          <span style={{ fontFamily: "monospace", color: "var(--fg-mute)", fontSize: 10, minWidth: 26, textAlign: "right" }}>
                            {det.score != null ? det.score.toFixed(2) : "—"}
                          </span>
                          <span style={{ fontSize: 9, color: "var(--fg-mute)", padding: "1px 4px", borderRadius: 3, background: "var(--surface-3)", whiteSpace: "nowrap" }}>
                            {det.model}
                          </span>
                          {det.mask && (
                            <button
                              className="icon-btn"
                              style={{ width: 18, height: 18, opacity: active ? 1 : 0.7 }}
                              title="Refine mask with point prompts"
                              onClick={() => onStartRefine(det)}
                              disabled={busy}
                            >
                              <Focus size={11} />
                            </button>
                          )}
                          <button
                            className="icon-btn"
                            style={{ width: 18, height: 18, opacity: 0.6 }}
                            title="Delete detection"
                            onClick={() => remove.mutate(det.id)}
                            disabled={remove.isPending}
                          >
                            <Trash2 size={11} />
                          </button>
                        </>
                      )}
                    </div>
                  );
                })}
              </div>

              {/* Merge bar */}
              {mergeIds.length >= 2 && (
                <button
                  className="btn btn-ghost btn-sm"
                  style={{ marginTop: 6, display: "flex", alignItems: "center", gap: 5, fontSize: 11, width: "100%", justifyContent: "center" }}
                  onClick={() => merge.mutate(mergeIds)}
                  disabled={merge.isPending}
                >
                  <Combine size={12} /> Merge {mergeIds.length} → {firstMergeLabel}
                </button>
              )}

              {visibleCount > 0 && (
                <button
                  className="btn btn-ghost btn-sm"
                  style={{ marginTop: 6, display: "flex", alignItems: "center", gap: 5, fontSize: 11, width: "100%", justifyContent: "center" }}
                  onClick={onCropFromDetections}
                >
                  <Focus size={12} /> Crop from Detections
                </button>
              )}

              <button
                className="btn btn-ghost btn-sm"
                style={{ marginTop: 6, display: "flex", alignItems: "center", gap: 5, fontSize: 11 }}
                onClick={onOpenDetectModal}
              >
                <ScanSearch size={12} /> Re-run Detection
              </button>
            </>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <p style={{ fontSize: 11, color: "var(--fg-mute)" }}>No detections run yet.</p>
              <button
                className="btn btn-ghost btn-sm"
                style={{ alignSelf: "flex-start", display: "flex", alignItems: "center", gap: 5 }}
                onClick={onOpenDetectModal}
              >
                <ScanSearch size={12} /> Run Detection
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
