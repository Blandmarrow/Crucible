import { useState, useMemo, useEffect, useCallback, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { usePaneNavigate } from "../hooks/usePaneNavigate";
import toast from "react-hot-toast";
import { datasetsApi } from "../api/datasets";
import { imagesApi } from "../api/images";
import type { Dataset } from "../types";
import ConfirmDialog from "../components/common/ConfirmDialog";
import { useJobStore } from "../store/jobStore";

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1_048_576) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1_073_741_824) return `${(bytes / 1_048_576).toFixed(1)} MB`;
  return `${(bytes / 1_073_741_824).toFixed(2)} GB`;
}

/* Deterministic placeholder tile gradient per card index */
const TILE_HUES = [
  ["#1f3a32","#10221c","#274d40"],
  ["#2a2520","#181412","#3a3128"],
  ["#1f3142","#0f1e2a","#2a4259"],
  ["#3a3a3a","#222222","#4a4a4a"],
  ["#3d2f24","#22180f","#503a2c"],
  ["#2a2440","#16122a","#3b3358"],
  ["#202020","#0f0f0f","#2a2a2a"],
];
function tileGrad(dsIndex: number, k: number) {
  const h = TILE_HUES[(dsIndex + k) % TILE_HUES.length];
  const angle = 135 + k * 7;
  return `linear-gradient(${angle}deg, ${h[0]}, ${h[1]} 60%, ${h[2]})`;
}

export default function DatasetsPage() {
  const { go } = usePaneNavigate();
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Dataset | null>(null);
  const [renameTarget, setRenameTarget] = useState<Dataset | null>(null);
  const [renameName, setRenameName] = useState("");
  const [renameDesc, setRenameDesc] = useState("");
  const [importTarget, setImportTarget] = useState<Dataset | null>(null);
  const [importPath, setImportPath] = useState("");
  const [importSubfolder, setImportSubfolder] = useState("");
  const [importPreserveStructure, setImportPreserveStructure] = useState(false);
  const [importJobId, setImportJobId] = useState<string | null>(null);
  const [dragOverId, setDragOverId] = useState<string | null>(null);
  const dragTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const importJobProgress = useJobStore((s) => s.activeJobs.get(importJobId ?? ""));

  useEffect(() => {
    if (!importJobId || !importJobProgress) return;
    if (importJobProgress.status === "completed") {
      qc.invalidateQueries({ queryKey: ["datasets"] });
      setImportJobId(null);
    } else if (importJobProgress.status === "failed") {
      setImportJobId(null);
    }
  }, [importJobProgress?.status, importJobId, qc]);

  const { data: datasets = [], isLoading } = useQuery({
    queryKey: ["datasets"],
    queryFn: datasetsApi.list,
    staleTime: 0,
  });

  const filtered = useMemo(
    () => datasets.filter((d) => d.name.toLowerCase().includes(search.toLowerCase())),
    [datasets, search]
  );

  const totalImages = datasets.reduce((s, d) => s + d.image_count, 0);
  const totalSize = datasets.reduce((s, d) => s + d.total_size_bytes, 0);

  const createMutation = useMutation({
    mutationFn: () => datasetsApi.create(newName, newDesc),
    onSuccess: (ds) => {
      qc.invalidateQueries({ queryKey: ["datasets"] });
      setShowCreate(false); setNewName(""); setNewDesc("");
      toast.success(`Dataset "${ds.name}" created`);
    },
    onError: () => toast.error("Failed to create dataset"),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => datasetsApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["datasets"] });
      setDeleteTarget(null);
      toast.success("Dataset deleted");
    },
  });

  const renameMutation = useMutation({
    mutationFn: () => datasetsApi.update(renameTarget!.id, { name: renameName, description: renameDesc }),
    onSuccess: (ds) => {
      qc.invalidateQueries({ queryKey: ["datasets"] });
      setRenameTarget(null);
      toast.success(`Updated "${ds.name}"`);
    },
    onError: () => toast.error("Failed to update dataset"),
  });

  const importMutation = useMutation({
    mutationFn: () => datasetsApi.importFolder(importTarget!.id, importPath, importSubfolder, importPreserveStructure),
    onSuccess: (data) => {
      toast.success(`Import started`);
      setImportTarget(null); setImportPath(""); setImportSubfolder(""); setImportPreserveStructure(false);
      setImportJobId(data.job_id);
    },
    onError: () => toast.error("Import failed"),
  });

  const handleCardDrop = useCallback(async (datasetId: string, files: FileList) => {
    if (!files.length) return;
    try {
      await imagesApi.upload(datasetId, Array.from(files));
      qc.invalidateQueries({ queryKey: ["datasets"] });
      toast.success(`Uploaded ${files.length} image(s)`);
    } catch {
      toast.error("Upload failed");
    }
  }, [qc]);

  const pageRef = useRef<HTMLDivElement>(null);
  const dragRafRef = useRef<number | null>(null);

  useEffect(() => {
    const el = pageRef.current;
    if (!el) return;

    const onDragOver = (e: DragEvent) => {
      if (!e.dataTransfer?.types.includes("Files")) return;
      e.preventDefault();
      // Throttle the expensive elementFromPoint lookup to once per animation frame
      if (dragRafRef.current !== null) return;
      const cx = e.clientX;
      const cy = e.clientY;
      dragRafRef.current = requestAnimationFrame(() => {
        dragRafRef.current = null;
        const under = document.elementFromPoint(cx, cy);
        const card = under?.closest<HTMLElement>("[data-dataset-id]");
        const id = card?.dataset.datasetId ?? null;
        setDragOverId(id);
        if (dragTimerRef.current) clearTimeout(dragTimerRef.current);
        if (id) dragTimerRef.current = setTimeout(() => setDragOverId(null), 200);
      });
    };

    const onDrop = (e: DragEvent) => {
      e.preventDefault();
      if (dragTimerRef.current) { clearTimeout(dragTimerRef.current); dragTimerRef.current = null; }
      setDragOverId(null);
      const under = document.elementFromPoint(e.clientX, e.clientY);
      const card = under?.closest<HTMLElement>("[data-dataset-id]");
      const id = card?.dataset.datasetId;
      if (id && e.dataTransfer?.files.length) handleCardDrop(id, e.dataTransfer.files);
    };

    el.addEventListener("dragover", onDragOver);
    el.addEventListener("drop", onDrop);
    return () => {
      el.removeEventListener("dragover", onDragOver);
      el.removeEventListener("drop", onDrop);
      if (dragRafRef.current !== null) cancelAnimationFrame(dragRafRef.current);
    };
  }, [handleCardDrop]);

  return (
    <div ref={pageRef} style={{ padding: "24px 28px", overflowY: "auto", flex: 1 }}>
      {/* Page header */}
      <div className="page-h">
        <div>
          <h1>Datasets</h1>
          <p>
            {datasets.length} datasets · {totalImages.toLocaleString()} images · {formatSize(totalSize)} on disk
          </p>
        </div>
        <div className="phactions">
          <div className="search-wrap">
            <svg className="search-ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
              <circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5l3 3"/>
            </svg>
            <input
              className="input"
              placeholder="Search datasets…"
              style={{ width: 220 }}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <button className="btn" onClick={() => setImportTarget(datasets[0] ?? null)}>
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
              <path d="M2.5 3.5h4l1.5 2h5.5v7h-11v-9z"/>
            </svg>
            Import folder
          </button>
          <button className="btn primary" onClick={() => setShowCreate(true)}>
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6">
              <path d="M8 3v10M3 8h10"/>
            </svg>
            New dataset
          </button>
        </div>
      </div>

      {isLoading && <p style={{ color: "var(--fg-mute)" }}>Loading…</p>}

      {/* Dataset grid */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: 14 }}>
        {filtered.map((ds, i) => {
          const pct = ds.image_count ? Math.round((ds.captioned_count / ds.image_count) * 100) : 0;
          return (
            <div
              key={ds.id}
              data-dataset-id={ds.id}
              style={{
                background: "var(--surface-1)",
                border: `1px solid ${dragOverId === ds.id ? "var(--accent)" : "var(--line)"}`,
                borderRadius: "var(--r-lg)", overflow: "hidden",
                cursor: "pointer", display: "flex", flexDirection: "column",
                position: "relative", transition: "border-color .15s",
              }}
              onClick={() => go(`/datasets/${ds.id}/gallery`, { page: "gallery", datasetId: ds.id })}
              onMouseEnter={(e) => { if (dragOverId !== ds.id) (e.currentTarget as HTMLElement).style.borderColor = "var(--line-2)"; }}
              onMouseLeave={(e) => { if (dragOverId !== ds.id) (e.currentTarget as HTMLElement).style.borderColor = "var(--line)"; }}
              className="ds-card-wrapper"
            >
              {/* Drag-over overlay */}
              {dragOverId === ds.id && (
                <div style={{
                  position: "absolute", inset: 0, zIndex: 10, pointerEvents: "none",
                  background: "rgba(0,0,0,0.55)", borderRadius: "var(--r-lg)",
                  display: "flex", alignItems: "center", justifyContent: "center",
                }}>
                  <div style={{
                    background: "var(--surface-2)", border: "1px solid var(--accent)",
                    borderRadius: "var(--r)", padding: "6px 14px",
                    color: "var(--accent)", fontSize: 13, fontWeight: 600,
                    display: "flex", alignItems: "center", gap: 6,
                  }}>
                    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6">
                      <path d="M8 10V2M5 5l3-3 3 3M2.5 13.5h11"/>
                    </svg>
                    Drop to upload
                  </div>
                </div>
              )}

              {/* Preview tile strip */}
              <div style={{ height: 110, background: "var(--surface-2)", display: "grid", gridTemplateColumns: "repeat(8, 1fr)", gridTemplateRows: "1fr", gap: 1, position: "relative" }}>
                {(ds.preview_image_ids ?? []).length > 0
                  ? Array.from({ length: 8 }).map((_, k) => {
                      const imgId = ds.preview_image_ids[k % ds.preview_image_ids.length];
                      return (
                        <div key={k} style={{ height: 110, overflow: "hidden", background: "var(--surface-3)" }}>
                          <img
                            src={`/api/v1/images/${imgId}/thumbnail`}
                            alt=""
                            style={{ width: "100%", height: 110, objectFit: "cover", display: "block" }}
                          />
                        </div>
                      );
                    })
                  : Array.from({ length: 8 }).map((_, k) => (
                      <div key={k} style={{ background: tileGrad(i, k) }} />
                    ))
                }
                {/* Fade overlay */}
                <div style={{ position: "absolute", inset: 0, background: "linear-gradient(180deg, transparent 30%, var(--surface-1))", pointerEvents: "none" }} />
              </div>

              {/* Row actions (shown on hover via CSS hack) */}
              <div
                className="ds-row-actions"
                style={{ position: "absolute", top: 10, right: 10, display: "flex", gap: 4, opacity: 0, transition: "opacity .15s", zIndex: 2 }}
                onClick={(e) => e.stopPropagation()}
              >
                <button
                  className="icon-btn"
                  title="Edit"
                  style={{ width: 26, height: 26, background: "rgba(7,9,11,.7)", border: "1px solid var(--line-2)", backdropFilter: "blur(8px)" }}
                  onClick={() => { setRenameTarget(ds); setRenameName(ds.name); setRenameDesc(ds.description ?? ""); }}
                >
                  <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
                    <path d="M11.5 2.5l2 2-8 8H3.5v-2l8-8z"/>
                  </svg>
                </button>
                <button
                  className="icon-btn"
                  title="Import folder"
                  style={{ width: 26, height: 26, background: "rgba(7,9,11,.7)", border: "1px solid var(--line-2)", backdropFilter: "blur(8px)" }}
                  onClick={() => { setImportTarget(ds); setImportPath(""); }}
                >
                  <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
                    <path d="M2.5 3.5h4l1.5 2h5.5v7h-11v-9z"/>
                  </svg>
                </button>
                <button
                  className="icon-btn danger"
                  title="Delete"
                  style={{ width: 26, height: 26, background: "rgba(7,9,11,.7)", border: "1px solid var(--line-2)", backdropFilter: "blur(8px)" }}
                  onClick={() => setDeleteTarget(ds)}
                >
                  <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
                    <path d="M3 4.5h10M5.5 4.5V3a1 1 0 011-1h3a1 1 0 011 1v1.5M5.5 7.5v4M10.5 7.5v4M4 4.5l1 9h6l1-9"/>
                  </svg>
                </button>
              </div>

              {/* Body */}
              <div style={{ padding: "14px 16px 16px", display: "flex", flexDirection: "column", gap: 12 }}>
                <div>
                  <h3 style={{ margin: "0 0 4px", fontSize: 14.5, fontWeight: 600, letterSpacing: "-.01em" }}>{ds.name}</h3>
                  <p style={{ margin: 0, color: "var(--fg-mute)", fontSize: 12, lineHeight: 1.5, minHeight: 18 }}>
                    {ds.description || <span style={{ color: "var(--fg-soft)" }}>No description</span>}
                  </p>
                </div>

                {/* Stats row */}
                <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8 }}>
                  {[
                    { k: "Images", v: ds.image_count.toLocaleString(), accent: false },
                    { k: "Captioned", v: `${pct}%`, accent: true },
                    { k: "Size", v: formatSize(ds.total_size_bytes), accent: false },
                  ].map(({ k, v, accent }) => (
                    <div key={k} style={{ background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: "var(--r)", padding: "8px 10px" }}>
                      <div style={{ color: "var(--fg-dim)", fontSize: 10.5, letterSpacing: ".04em", textTransform: "uppercase" }}>{k}</div>
                      <div style={{ color: accent ? "var(--accent)" : "var(--fg)", fontSize: accent ? 16 : 14, fontWeight: 600, marginTop: 2, fontFeatureSettings: '"tnum"' }}>{v}</div>
                    </div>
                  ))}
                </div>

                {/* Meta */}
                <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--fg-dim)", fontSize: 11.5 }}>
                  <span className="mono">{ds.captioned_count}/{ds.image_count} captioned</span>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Hover row-actions reveal via style injection */}
      <style>{`.ds-card-wrapper:hover .ds-row-actions { opacity: 1 !important; }`}</style>

      {/* Edit Dataset Modal */}
      {renameTarget && (
        <div className="dialog-bg">
          <div className="dialog">
            <h3>Edit Dataset</h3>
            <div style={{ display: "flex", flexDirection: "column", gap: 12, marginBottom: 18 }}>
              <div>
                <label className="label">Name</label>
                <input
                  className="input"
                  value={renameName}
                  autoFocus
                  onChange={(e) => setRenameName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && renameName && (renameName !== renameTarget.name || renameDesc !== (renameTarget.description ?? ""))) {
                      renameMutation.mutate();
                    }
                  }}
                />
              </div>
              <div>
                <label className="label">Description <span style={{ fontWeight: 400, color: "var(--fg-mute)", fontSize: 11 }}>(optional)</span></label>
                <input
                  className="input"
                  placeholder="…"
                  value={renameDesc}
                  onChange={(e) => setRenameDesc(e.target.value)}
                />
              </div>
            </div>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button className="btn ghost" onClick={() => setRenameTarget(null)}>Cancel</button>
              <button
                className="btn primary"
                onClick={() => renameMutation.mutate()}
                disabled={
                  !renameName ||
                  (renameName === renameTarget.name && renameDesc === (renameTarget.description ?? "")) ||
                  renameMutation.isPending
                }
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Create Modal */}
      {showCreate && (
        <div className="dialog-bg">
          <div className="dialog">
            <h3>New Dataset</h3>
            <p>Give it a name and optional description.</p>
            <div style={{ display: "flex", flexDirection: "column", gap: 12, marginBottom: 18 }}>
              <div>
                <label className="label">Name</label>
                <input className="input" placeholder="my_dataset" value={newName} autoFocus
                  onChange={(e) => setNewName(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && newName && createMutation.mutate()} />
              </div>
              <div>
                <label className="label">Description (optional)</label>
                <input className="input" placeholder="…" value={newDesc} onChange={(e) => setNewDesc(e.target.value)} />
              </div>
            </div>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button className="btn ghost" onClick={() => setShowCreate(false)}>Cancel</button>
              <button className="btn primary" onClick={() => createMutation.mutate()} disabled={!newName || createMutation.isPending}>Create</button>
            </div>
          </div>
        </div>
      )}

      {/* Import Modal */}
      {importTarget && (
        <div className="dialog-bg">
          <div className="dialog">
            <h3>Import from folder</h3>
            <p>Into: <strong style={{ color: "var(--fg)" }}>{importTarget.name}</strong></p>
            <div style={{ marginBottom: 14 }}>
              <label className="label">Folder path</label>
              <input className="input" placeholder="D:\datasets\my_images" value={importPath}
                onChange={(e) => setImportPath(e.target.value)} autoFocus />
            </div>
            <div style={{ marginBottom: 14 }}>
              <label className="label">Target subfolder <span style={{ fontWeight: 400, color: "var(--fg-mute)", fontSize: 11 }}>(optional)</span></label>
              <input
                className="input"
                placeholder="e.g. characters (leave blank for root)"
                value={importSubfolder}
                onChange={(e) => setImportSubfolder(e.target.value)}
                disabled={importPreserveStructure}
                style={{ opacity: importPreserveStructure ? 0.5 : 1 }}
              />
            </div>
            <label style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 18, cursor: "pointer" }}>
              <input
                type="checkbox"
                className="checkbox"
                checked={importPreserveStructure}
                onChange={(e) => {
                  setImportPreserveStructure(e.target.checked);
                  if (e.target.checked) setImportSubfolder("");
                }}
              />
              <span style={{ fontSize: 13 }}>Preserve source folder structure</span>
            </label>
            {importPreserveStructure && (
              <p style={{ fontSize: 11.5, color: "var(--fg-mute)", marginTop: -12, marginBottom: 14, paddingLeft: 22 }}>
                Subfolders from the source directory will be recreated as logical subfolders.
              </p>
            )}
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button className="btn ghost" onClick={() => { setImportTarget(null); setImportSubfolder(""); setImportPreserveStructure(false); }}>Cancel</button>
              <button className="btn primary" onClick={() => importMutation.mutate()} disabled={!importPath || importMutation.isPending}>Import</button>
            </div>
          </div>
        </div>
      )}

      {deleteTarget && (
        <ConfirmDialog
          title="Delete dataset"
          message={`Delete "${deleteTarget.name}" and all its images? This cannot be undone.`}
          confirmLabel="Delete"
          danger
          onConfirm={() => deleteMutation.mutate(deleteTarget.id)}
          onCancel={() => setDeleteTarget(null)}
        />
      )}
    </div>
  );
}
