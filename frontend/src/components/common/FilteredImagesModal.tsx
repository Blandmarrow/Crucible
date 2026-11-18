import { useState, useEffect, useCallback } from "react";
import { Link } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { ImageListItem } from "../../types";
import toast from "react-hot-toast";
import { imagesApi, type ImageListParams } from "../../api/images";
import { invalidateDatasetContentScope } from "../../constants/queryKeys";
import { apiErrorDetail } from "../../utils/apiError";

// ─── ImageLightbox ────────────────────────────────────────────────────────────
function ImageLightbox({
  images,
  index,
  datasetId,
  onClose,
  onNavigate,
  onDelete,
}: {
  images: ImageListItem[];
  index: number;
  datasetId: string;
  onClose: () => void;
  onNavigate: (i: number) => void;
  onDelete: (imageId: string) => Promise<void>;
}) {
  const img = images[index];
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const prev = useCallback(() => { setConfirmDelete(false); onNavigate(index - 1); }, [index, onNavigate]);
  const next = useCallback(() => { setConfirmDelete(false); onNavigate(index + 1); }, [index, onNavigate]);

  useEffect(() => { setConfirmDelete(false); }, [index]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") { if (confirmDelete) setConfirmDelete(false); else onClose(); }
      if (e.key === "ArrowLeft"  && index > 0)               prev();
      if (e.key === "ArrowRight" && index < images.length - 1) next();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [index, images.length, onClose, prev, next, confirmDelete]);

  const handleDelete = async () => {
    setDeleting(true);
    try { await onDelete(img.id); }
    finally { setDeleting(false); setConfirmDelete(false); }
  };

  const scores: { label: string; value: string | number | null }[] = [
    { label: "Aesthetic",  value: img.aesthetic_score  != null ? img.aesthetic_score.toFixed(2)  : null },
    { label: "Blur",       value: img.blur_score       != null ? img.blur_score.toFixed(1)       : null },
    { label: "Watermark",  value: img.watermark_score  != null ? img.watermark_score.toFixed(2)  : null },
    { label: "Color",      value: img.color_score      != null ? img.color_score.toFixed(1)      : null },
    { label: "Saturation", value: img.saturation_score != null ? img.saturation_score.toFixed(2) : null },
    { label: "Brightness", value: img.luminance_score  != null ? img.luminance_score.toFixed(2)  : null },
  ].filter((s) => s.value != null);

  return (
    <div
      style={{ position: "fixed", inset: 0, zIndex: 60, display: "flex", alignItems: "center", justifyContent: "center", background: "rgba(0,0,0,.85)" }}
      onClick={onClose}
    >
      <div
        style={{ background: "var(--surface-1)", borderRadius: "var(--r-xl)", maxWidth: 900, width: "100%", margin: "0 16px", display: "flex", flexDirection: "column", maxHeight: "90vh", border: "1px solid var(--line-2)", boxShadow: "var(--shadow-lg)", overflow: "hidden" }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 16px", borderBottom: "1px solid var(--line)", gap: 12, flexShrink: 0 }}>
          <span style={{ fontSize: 13, color: "var(--fg-mute)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={img.filename}>
            {img.filename}
          </span>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
            <span style={{ fontSize: 11, color: "var(--fg-dim)" }}>{index + 1} / {images.length}</span>
            <Link
              to={`/datasets/${datasetId}/image/${img.id}`}
              style={{ fontSize: 11.5, padding: "3px 8px", borderRadius: "var(--r)", background: "var(--surface-3)", color: "var(--fg-mute)", textDecoration: "none" }}
            >
              View Details →
            </Link>
            {confirmDelete ? (
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{ fontSize: 12, color: "var(--bad)" }}>Remove image?</span>
                <button onClick={handleDelete} disabled={deleting} className="btn sm danger">{deleting ? "…" : "Remove"}</button>
                <button onClick={() => setConfirmDelete(false)} className="btn sm ghost">Cancel</button>
              </div>
            ) : (
              <button onClick={() => setConfirmDelete(true)} className="btn sm danger">Delete</button>
            )}
            <button onClick={onClose} className="icon-btn" style={{ fontSize: 18 }}>×</button>
          </div>
        </div>

        {/* Image */}
        <div style={{ position: "relative", display: "flex", alignItems: "center", justifyContent: "center", background: "rgba(0,0,0,.4)", flex: 1, overflow: "hidden", minHeight: 0 }}>
          {index > 0 && (
            <button onClick={prev} style={{ position: "absolute", left: 8, zIndex: 10, width: 36, height: 36, borderRadius: "50%", background: "rgba(0,0,0,.6)", color: "#fff", fontSize: 20, border: "none", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center" }}>‹</button>
          )}
          <img key={img.id} src={imagesApi.fileUrl(img.id)} alt={img.filename} style={{ objectFit: "contain", maxHeight: "60vh", maxWidth: "100%" }} />
          {index < images.length - 1 && (
            <button onClick={next} style={{ position: "absolute", right: 8, zIndex: 10, width: 36, height: 36, borderRadius: "50%", background: "rgba(0,0,0,.6)", color: "#fff", fontSize: 20, border: "none", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center" }}>›</button>
          )}
        </div>

        {/* Footer */}
        <div style={{ padding: "10px 16px", borderTop: "1px solid var(--line)", flexShrink: 0, display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
          {img.width && img.height && <span style={{ fontSize: 12, color: "var(--fg-dim)" }}>{img.width}×{img.height}</span>}
          {img.file_size_bytes && (
            <span style={{ fontSize: 12, color: "var(--fg-dim)" }}>
              {img.file_size_bytes > 1_048_576 ? `${(img.file_size_bytes / 1_048_576).toFixed(1)} MB` : `${Math.round(img.file_size_bytes / 1024)} KB`}
            </span>
          )}
          {scores.map(({ label, value }) => (
            <span key={label} style={{ fontSize: 12, color: "var(--fg-dim)" }}>
              <span style={{ color: "var(--fg-soft)" }}>{label}:</span> {value}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─── FilteredImagesModal ─────────────────────────────────────────────────────
/**
 * The images one filter object selects, as a thumbnail grid with a lightbox and a
 * per-image delete. The Stats page opens it from a histogram bar, the Health page
 * from a finding's `filter_spec`. One page of 200, no pagination: the header says
 * "200+" when the page is full.
 */
export default function FilteredImagesModal({
  title,
  params,
  datasetId,
  onClose,
  subfolder,
}: {
  title: string;
  params: Omit<ImageListParams, "dataset_id" | "page" | "limit">;
  datasetId: string;
  onClose: () => void;
  subfolder?: string;
}) {
  const [previewIdx, setPreviewIdx] = useState<number | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const queryKey = ["bucket-images", datasetId, params, subfolder];

  const { data: images = [], isLoading } = useQuery({
    queryKey,
    queryFn: () => imagesApi.list({ dataset_id: datasetId, limit: 200, ...params, subfolder }),
  });

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // The lightbox handles Escape while it is open; one press closes one layer.
      if (e.key !== "Escape" || previewIdx !== null) return;
      if (confirmDeleteId) setConfirmDeleteId(null); else onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose, confirmDeleteId, previewIdx]);

  const handleDelete = async (imageId: string) => {
    try {
      await imagesApi.delete(imageId);
    } catch (err) {
      // A 409 when the file is still open elsewhere (PM-021), a 404 when it is gone.
      toast.error(apiErrorDetail(err, "Could not delete the image"));
      setConfirmDeleteId(null);
      return;
    }
    const deletedIdx = images.findIndex((img) => img.id === imageId);
    const next = images.filter((img) => img.id !== imageId);
    queryClient.setQueryData<ImageListItem[]>(queryKey, next);
    // The shared scope, not a hand list: this modal opens beside a gallery pane
    // too (the Health page), whose grid and navigation must drop the image.
    invalidateDatasetContentScope(queryClient, datasetId);
    queryClient.invalidateQueries({ queryKey: ["detection-stats", datasetId] });
    setConfirmDeleteId(null);
    if (previewIdx !== null && deletedIdx !== -1) {
      if (next.length === 0) setPreviewIdx(null);
      else if (deletedIdx <= previewIdx) setPreviewIdx(Math.max(0, previewIdx - 1));
    }
  };

  return (
    <>
      <div style={{ position: "fixed", inset: 0, zIndex: 50, display: "flex", alignItems: "center", justifyContent: "center", background: "rgba(0,0,0,.7)" }} onClick={onClose}>
        <div style={{ background: "var(--surface-1)", borderRadius: "var(--r-xl)", width: "100%", maxWidth: 900, maxHeight: "85vh", display: "flex", flexDirection: "column", margin: "0 16px", border: "1px solid var(--line-2)", boxShadow: "var(--shadow-lg)" }} onClick={(e) => e.stopPropagation()}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "14px 20px", borderBottom: "1px solid var(--line)", flexShrink: 0 }}>
            <div>
              <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>{title}</h3>
              {!isLoading && (
                <p style={{ margin: "2px 0 0", fontSize: 11.5, color: "var(--fg-mute)" }}>
                  {images.length === 200 ? "200+ images (first 200 shown)" : `${images.length} image${images.length !== 1 ? "s" : ""}`}
                  {images.length > 0 && <span style={{ color: "var(--fg-soft)" }}> · click to preview</span>}
                </p>
              )}
            </div>
            <button onClick={onClose} className="icon-btn" style={{ fontSize: 18 }}>×</button>
          </div>

          <div style={{ overflowY: "auto", padding: 16 }}>
            {isLoading ? (
              <div style={{ textAlign: "center", padding: "60px 0", color: "var(--fg-mute)", fontSize: 13 }}>Loading…</div>
            ) : images.length === 0 ? (
              <div style={{ textAlign: "center", padding: "60px 0", color: "var(--fg-mute)", fontSize: 13 }}>No images found</div>
            ) : (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(90px, 1fr))", gap: 6 }}>
                {images.map((img, i) => (
                  <div key={img.id} style={{ position: "relative", aspectRatio: "1/1" }} className="group">
                    <button
                      style={{ width: "100%", height: "100%", overflow: "hidden", borderRadius: "var(--r)", border: "1px solid var(--line)", background: "var(--surface-2)", cursor: "pointer", padding: 0 }}
                      onClick={() => { setConfirmDeleteId(null); setPreviewIdx(i); }}
                    >
                      <img src={imagesApi.thumbnailUrlVersioned(img.id, img.updated_at)} alt={img.filename} style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }} loading="lazy" />
                    </button>

                    {confirmDeleteId === img.id ? (
                      <div className="on-media" style={{ position: "absolute", inset: 0, borderRadius: "var(--r)", background: "rgba(0,0,0,.85)", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 6, zIndex: 10 }}>
                        <span style={{ fontSize: 11, color: "var(--bad)", fontWeight: 500 }}>Remove?</span>
                        <div style={{ display: "flex", gap: 4 }}>
                          <button onClick={(e) => { e.stopPropagation(); handleDelete(img.id); }} className="btn sm danger" style={{ fontSize: 11 }}>Yes</button>
                          <button onClick={(e) => { e.stopPropagation(); setConfirmDeleteId(null); }} className="btn sm ghost" style={{ fontSize: 11 }}>No</button>
                        </div>
                      </div>
                    ) : (
                      <button
                        onClick={(e) => { e.stopPropagation(); setConfirmDeleteId(img.id); }}
                        style={{ position: "absolute", top: 4, right: 4, width: 18, height: 18, borderRadius: "50%", background: "rgba(0,0,0,.7)", color: "#fff", fontSize: 12, border: "none", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", opacity: 0, transition: "opacity .15s", zIndex: 10 }}
                        className="delete-btn"
                        title="Delete image"
                      >×</button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {previewIdx !== null && images.length > 0 && (
        <ImageLightbox
          images={images}
          index={previewIdx}
          datasetId={datasetId}
          onClose={() => setPreviewIdx(null)}
          onNavigate={setPreviewIdx}
          onDelete={handleDelete}
        />
      )}
      {/* !important: the button's inline `opacity: 0` would otherwise win, and it
          stayed invisible while still catching clicks. */}
      <style>{`.group:hover .delete-btn, .delete-btn:focus-visible { opacity: 1 !important; }`}</style>
    </>
  );
}
