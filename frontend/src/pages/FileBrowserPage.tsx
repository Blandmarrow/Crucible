import { useState, useEffect, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Folder, FolderOpen, File, Film, Image as ImageIcon, HardDrive, Home,
  ChevronRight, Plus, RefreshCw, Trash2, Edit2, FolderInput,
  ArrowUp, SortAsc, SortDesc, X,
} from "lucide-react";
import toast from "react-hot-toast";
import { filesystemApi, type FsEntry } from "../api/filesystem";
import { parentOf, breadcrumbsFromPath } from "../utils/pathUtils";
import { datasetsApi } from "../api/datasets";
import GenerationMetadata from "../components/image/GenerationMetadata";
import type { Dataset, GenerationMetadata as GenMeta } from "../types";

function formatSize(bytes: number | null) {
  if (bytes === null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1_048_576) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1_048_576).toFixed(1)} MB`;
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

// ── Context Menu ──────────────────────────────────────────────────────────────

interface ContextMenuAction {
  label: string;
  icon: React.ReactNode;
  onClick: () => void;
  danger?: boolean;
}

interface ContextMenuProps {
  x: number;
  y: number;
  actions: ContextMenuAction[];
  onClose: () => void;
}

function ContextMenu({ x, y, actions, onClose }: ContextMenuProps) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  return (
    <div
      ref={ref}
      style={{
        position: "fixed", left: x, top: y, zIndex: 1000,
        background: "var(--surface-2)", border: "1px solid var(--line-2)",
        borderRadius: "var(--r)", boxShadow: "0 8px 24px rgba(0,0,0,.4)",
        minWidth: 180, padding: "4px 0",
      }}
    >
      {actions.map((a) => (
        <button
          key={a.label}
          onClick={() => { a.onClick(); onClose(); }}
          style={{
            display: "flex", alignItems: "center", gap: 8,
            width: "100%", padding: "7px 14px",
            fontSize: 13, background: "none", border: "none",
            color: a.danger ? "var(--bad)" : "var(--fg)",
            cursor: "pointer", textAlign: "left",
          }}
          onMouseEnter={(e) => (e.currentTarget.style.background = "var(--surface-3)")}
          onMouseLeave={(e) => (e.currentTarget.style.background = "none")}
        >
          <span style={{ opacity: 0.7 }}>{a.icon}</span>
          {a.label}
        </button>
      ))}
    </div>
  );
}

// ── Inline rename input ───────────────────────────────────────────────────────

function RenameInput({ initial, onConfirm, onCancel }: { initial: string; onConfirm: (v: string) => void; onCancel: () => void }) {
  const [val, setVal] = useState(initial);
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { ref.current?.focus(); ref.current?.select(); }, []);
  return (
    <input
      ref={ref}
      className="input"
      style={{ padding: "2px 6px", fontSize: 12, height: 22, flex: 1 }}
      value={val}
      onChange={(e) => setVal(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") onConfirm(val.trim());
        if (e.key === "Escape") onCancel();
      }}
      onClick={(e) => e.stopPropagation()}
    />
  );
}

// ── Image Preview Panel ───────────────────────────────────────────────────────

interface PreviewPanelProps {
  entry: FsEntry;
  onClose: () => void;
}

function PreviewPanel({ entry, onClose }: PreviewPanelProps) {
  const { data: meta } = useQuery({
    queryKey: ["fs-image-meta", entry.path],
    queryFn: () => filesystemApi.imageMeta(entry.path),
    staleTime: 60_000,
  });

  return (
    <div style={{
      width: 280, borderLeft: "1px solid var(--line)", display: "flex", flexDirection: "column",
      background: "var(--surface-1)", flexShrink: 0, overflowY: "auto",
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 12px", borderBottom: "1px solid var(--line)" }}>
        <span style={{ fontSize: 12, fontWeight: 500, color: "var(--fg-mute)" }}>Preview</span>
        <button className="icon-btn" onClick={onClose}><X size={13} /></button>
      </div>

      <div style={{ background: "var(--surface-2)", display: "flex", alignItems: "center", justifyContent: "center", padding: 8, minHeight: 160 }}>
        <img
          src={filesystemApi.previewUrl(entry.path)}
          alt={entry.name}
          style={{ maxWidth: "100%", maxHeight: 200, objectFit: "contain", borderRadius: 4 }}
        />
      </div>

      <div style={{ padding: "10px 12px", fontSize: 12, display: "flex", flexDirection: "column", gap: 4 }}>
        <p style={{ fontWeight: 500, wordBreak: "break-all", color: "var(--fg)" }}>{entry.name}</p>
        <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "3px 10px", color: "var(--fg-mute)" }}>
          <span>Size</span><span>{formatSize(entry.size_bytes)}</span>
          {meta?.width && <><span>Dimensions</span><span>{meta.width}×{meta.height}</span></>}
          {meta?.format && <><span>Format</span><span>{meta.format}</span></>}
          <span>Modified</span><span>{formatDate(entry.modified_at)}</span>
        </div>

        {meta?.generation_metadata && (
          <GenerationMetadata metadata={meta.generation_metadata as GenMeta} />
        )}
      </div>
    </div>
  );
}

// ── Import Modal ──────────────────────────────────────────────────────────────

function ImportModal({ folderPath, datasets, onClose }: { folderPath: string; datasets: Dataset[]; onClose: () => void }) {
  const [selectedId, setSelectedId] = useState(datasets[0]?.id ?? "");
  // Mirrors ImportFolderModal's default. Offered here too, or browsing to a
  // folder of videos and hitting Import would silently import nothing.
  const [includeVideos, setIncludeVideos] = useState(false);
  const mutation = useMutation({
    mutationFn: () =>
      datasetsApi.importFolder(selectedId, folderPath, "", false, true, undefined, includeVideos),
    onSuccess: () => { toast.success("Import started"); onClose(); },
    onError: () => toast.error("Import failed"),
  });

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.55)", zIndex: 2000, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div style={{ background: "var(--surface-2)", border: "1px solid var(--line-2)", borderRadius: 10, padding: 24, width: 400, boxShadow: "0 20px 60px rgba(0,0,0,.5)" }}>
        <h3 style={{ fontWeight: 600, marginBottom: 16, fontSize: 15 }}>Import into Dataset</h3>
        <p style={{ fontSize: 12, color: "var(--fg-mute)", marginBottom: 12, wordBreak: "break-all" }}>{folderPath}</p>
        <label style={{ fontSize: 12, color: "var(--fg-mute)", display: "block", marginBottom: 6 }}>Target dataset</label>
        <select className="select" style={{ width: "100%", marginBottom: 14 }} value={selectedId} onChange={(e) => setSelectedId(e.target.value)}>
          {datasets.map((ds) => <option key={ds.id} value={ds.id}>{ds.name}</option>)}
        </select>
        <label style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 20, cursor: "pointer" }}>
          <input type="checkbox" className="checkbox" checked={includeVideos} onChange={(e) => setIncludeVideos(e.target.checked)} />
          <span style={{ fontSize: 13 }}>Include videos</span>
        </label>
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" onClick={() => mutation.mutate()} disabled={!selectedId || mutation.isPending}>
            {mutation.isPending ? "Starting…" : "Import"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Mkdir Modal ───────────────────────────────────────────────────────────────

function MkdirModal({ parent, onClose, onCreated }: { parent: string; onClose: () => void; onCreated: (path: string) => void }) {
  const [name, setName] = useState("");
  const mutation = useMutation({
    mutationFn: () => filesystemApi.mkdir(parent, name.trim()),
    onSuccess: (data) => { toast.success("Folder created"); onCreated(data.path); onClose(); },
    onError: (e: Error) => toast.error(e.message ?? "Failed to create folder"),
  });

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.55)", zIndex: 2000, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div style={{ background: "var(--surface-2)", border: "1px solid var(--line-2)", borderRadius: 10, padding: 24, width: 360, boxShadow: "0 20px 60px rgba(0,0,0,.5)" }}>
        <h3 style={{ fontWeight: 600, marginBottom: 16, fontSize: 15 }}>New Folder</h3>
        <input
          className="input"
          style={{ width: "100%", marginBottom: 20 }}
          placeholder="Folder name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && name.trim()) mutation.mutate(); if (e.key === "Escape") onClose(); }}
          autoFocus
        />
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" onClick={() => mutation.mutate()} disabled={!name.trim() || mutation.isPending}>Create</button>
        </div>
      </div>
    </div>
  );
}

// ── Delete Confirm ────────────────────────────────────────────────────────────

function DeleteConfirm({ entry, onClose, onDeleted }: { entry: FsEntry; onClose: () => void; onDeleted: () => void }) {
  const qc = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => filesystemApi.delete(entry.path),
    onSuccess: () => {
      toast.success(`Deleted ${entry.name}`);
      qc.invalidateQueries({ queryKey: ["fs-list"] });
      onDeleted();
      onClose();
    },
    onError: () => toast.error("Delete failed"),
  });

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.55)", zIndex: 2000, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div style={{ background: "var(--surface-2)", border: "1px solid var(--line-2)", borderRadius: 10, padding: 24, width: 380, boxShadow: "0 20px 60px rgba(0,0,0,.5)" }}>
        <h3 style={{ fontWeight: 600, marginBottom: 8, fontSize: 15 }}>Delete "{entry.name}"?</h3>
        <p style={{ fontSize: 13, color: "var(--fg-mute)", marginBottom: 20 }}>
          {entry.type === "dir" ? "This will permanently delete the folder and all its contents." : "This will permanently delete the file."}
          {" "}This cannot be undone.
        </p>
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn danger" onClick={() => mutation.mutate()} disabled={mutation.isPending}>
            {mutation.isPending ? "Deleting…" : "Delete"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

type SortKey = "name" | "size" | "date";
type SortDir = "asc" | "desc";
type Modal = { type: "import"; path: string } | { type: "mkdir" } | { type: "delete"; entry: FsEntry } | null;

export default function FileBrowserPage() {
  const qc = useQueryClient();
  const [currentPath, setCurrentPath] = useState<string>("");
  const [selectedEntry, setSelectedEntry] = useState<FsEntry | null>(null);
  const [renamingPath, setRenamingPath] = useState<string | null>(null);
  const [mediaOnly, setMediaOnly] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const [modal, setModal] = useState<Modal>(null);
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number; entry: FsEntry } | null>(null);

  // Load drive roots on mount
  const { data: rootsData } = useQuery({
    queryKey: ["fs-roots"],
    queryFn: filesystemApi.roots,
    staleTime: Infinity,
  });

  // Navigate to datasets_dir by default once roots are loaded
  useEffect(() => {
    if (rootsData?.datasets_dir && !currentPath) {
      setCurrentPath(rootsData.datasets_dir);
    }
  }, [rootsData, currentPath]);

  // Load directory listing
  const { data: listing, isLoading, error, refetch } = useQuery({
    queryKey: ["fs-list", currentPath],
    queryFn: () => filesystemApi.list(currentPath),
    enabled: !!currentPath,
    staleTime: 5_000,
  });

  // Datasets for import modal
  const { data: datasets = [] } = useQuery({
    queryKey: ["datasets"],
    queryFn: datasetsApi.list,
    staleTime: 30_000,
  });

  const renameMutation = useMutation({
    mutationFn: ({ path, name }: { path: string; name: string }) => filesystemApi.rename(path, name),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["fs-list"] }); setRenamingPath(null); },
    onError: () => toast.error("Rename failed"),
  });

  // Sorted + filtered entries
  const entries = (listing?.entries ?? [])
    .filter((e) => !mediaOnly || e.type === "dir" || e.media_kind !== null)
    .sort((a, b) => {
      // Dirs always first
      if (a.type !== b.type) return a.type === "dir" ? -1 : 1;
      let cmp = 0;
      if (sortKey === "name") cmp = a.name.localeCompare(b.name, undefined, { numeric: true });
      else if (sortKey === "size") cmp = (a.size_bytes ?? 0) - (b.size_bytes ?? 0);
      else cmp = a.modified_at.localeCompare(b.modified_at);
      return sortDir === "asc" ? cmp : -cmp;
    });

  const imageCount = entries.filter((e) => e.media_kind === "image").length;
  const videoCount = entries.filter((e) => e.media_kind === "video").length;

  const breadcrumbs = () => currentPath ? breadcrumbsFromPath(currentPath) : [];

  const navigateTo = (path: string) => {
    setCurrentPath(path);
    setSelectedEntry(null);
  };

  const goUp = () => {
    if (!currentPath) return;
    const parent = parentOf(currentPath);
    if (parent) navigateTo(parent);
    else { setCurrentPath(""); setSelectedEntry(null); }
  };

  const handleEntryClick = (e: React.MouseEvent, entry: FsEntry) => {
    e.stopPropagation();
    if (entry.type === "dir") {
      navigateTo(entry.path);
    } else {
      // The preview pane renders a still image; a video row is selectable in the
      // listing but has no preview until poster frames exist.
      setSelectedEntry(entry.media_kind === "image" ? entry : null);
    }
  };

  const handleEntryDoubleClick = (entry: FsEntry) => {
    if (entry.type === "dir") navigateTo(entry.path);
  };

  const handleContextMenu = (e: React.MouseEvent, entry: FsEntry) => {
    e.preventDefault();
    e.stopPropagation();
    setCtxMenu({ x: e.clientX, y: e.clientY, entry });
  };

  const ctxActions = (entry: FsEntry): ContextMenuAction[] => {
    const actions: ContextMenuAction[] = [
      { label: "Rename", icon: <Edit2 size={13} />, onClick: () => setRenamingPath(entry.path) },
      { label: "Delete", icon: <Trash2 size={13} />, onClick: () => setModal({ type: "delete", entry }), danger: true },
    ];
    if (entry.type === "dir") {
      actions.unshift({ label: "Import into Dataset", icon: <FolderInput size={13} />, onClick: () => setModal({ type: "import", path: entry.path }) });
    }
    return actions;
  };

  const cycleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(key); setSortDir("asc"); }
  };

  const SortIcon = ({ k }: { k: SortKey }) =>
    sortKey === k ? (sortDir === "asc" ? <SortAsc size={11} /> : <SortDesc size={11} />) : null;

  return (
    <div style={{ display: "flex", height: "100%", overflow: "hidden" }}>

      {/* ── Left: Quick Access ── */}
      <div style={{
        width: 200, borderRight: "1px solid var(--line)", background: "var(--surface-1)",
        display: "flex", flexDirection: "column", flexShrink: 0, overflowY: "auto", padding: "10px 0",
      }}>
        <div style={{ padding: "4px 12px", fontSize: 10, letterSpacing: ".12em", textTransform: "uppercase", color: "var(--fg-dim)", marginBottom: 2 }}>
          Quick Access
        </div>

        {rootsData?.datasets_dir && (
          <SidebarLink
            icon={<Home size={14} />}
            label="Datasets Folder"
            active={currentPath === rootsData.datasets_dir}
            onClick={() => navigateTo(rootsData.datasets_dir)}
          />
        )}

        <div style={{ padding: "10px 12px 4px", fontSize: 10, letterSpacing: ".12em", textTransform: "uppercase", color: "var(--fg-dim)", marginBottom: 2 }}>
          Drives
        </div>
        {(rootsData?.roots ?? []).map((r) => (
          <SidebarLink
            key={r.path}
            icon={<HardDrive size={14} />}
            label={r.label}
            active={currentPath === r.path || currentPath.startsWith(r.path)}
            onClick={() => navigateTo(r.path)}
          />
        ))}
      </div>

      {/* ── Middle: File list ── */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0, overflow: "hidden" }}>

        {/* Toolbar */}
        <div style={{
          display: "flex", alignItems: "center", gap: 8, padding: "8px 12px",
          borderBottom: "1px solid var(--line)", background: "var(--surface-1)", flexShrink: 0,
        }}>
          <button className="icon-btn" onClick={goUp} title="Go up" disabled={!currentPath}>
            <ArrowUp size={14} />
          </button>

          {/* Breadcrumbs */}
          <div style={{ display: "flex", alignItems: "center", gap: 2, flex: 1, overflow: "hidden", fontSize: 12 }}>
            {!currentPath ? (
              <span style={{ color: "var(--fg-mute)" }}>Select a drive</span>
            ) : (
              breadcrumbs().map((crumb, i, arr) => (
                <span key={crumb.path} style={{ display: "flex", alignItems: "center", gap: 2, minWidth: 0 }}>
                  {i > 0 && <ChevronRight size={11} style={{ opacity: 0.4, flexShrink: 0 }} />}
                  <button
                    onClick={() => navigateTo(crumb.path)}
                    style={{
                      background: "none", border: "none", cursor: "pointer", fontSize: 12,
                      color: i === arr.length - 1 ? "var(--fg)" : "var(--fg-mute)",
                      padding: "2px 4px", borderRadius: 3, whiteSpace: "nowrap",
                      fontWeight: i === arr.length - 1 ? 500 : 400,
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "var(--surface-3)")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "none")}
                  >
                    {crumb.label}
                  </button>
                </span>
              ))
            )}
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12, color: "var(--fg-mute)", cursor: "pointer", userSelect: "none" }}>
              <input type="checkbox" className="checkbox" checked={mediaOnly} onChange={(e) => setMediaOnly(e.target.checked)} />
              <ImageIcon size={12} /> Media only
            </label>
            <button className="icon-btn" onClick={() => refetch()} title="Refresh"><RefreshCw size={13} /></button>
            <button className="btn sm" onClick={() => setModal({ type: "mkdir" })} style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <Plus size={12} /> New Folder
            </button>
          </div>
        </div>

        {/* Column headers */}
        {currentPath && (
          <div style={{
            display: "grid", gridTemplateColumns: "1fr 90px 120px",
            padding: "4px 12px", borderBottom: "1px solid var(--line)",
            fontSize: 11, color: "var(--fg-dim)", background: "var(--surface-1)", flexShrink: 0,
          }}>
            <button style={{ background: "none", border: "none", cursor: "pointer", color: "inherit", display: "flex", alignItems: "center", gap: 4, textAlign: "left" }} onClick={() => cycleSort("name")}>
              Name <SortIcon k="name" />
            </button>
            <button style={{ background: "none", border: "none", cursor: "pointer", color: "inherit", display: "flex", alignItems: "center", gap: 4 }} onClick={() => cycleSort("size")}>
              Size <SortIcon k="size" />
            </button>
            <button style={{ background: "none", border: "none", cursor: "pointer", color: "inherit", display: "flex", alignItems: "center", gap: 4 }} onClick={() => cycleSort("date")}>
              Modified <SortIcon k="date" />
            </button>
          </div>
        )}

        {/* File list */}
        <div style={{ flex: 1, overflowY: "auto" }} onClick={() => setSelectedEntry(null)}>
          {!currentPath ? (
            <div style={{ padding: 24, textAlign: "center", color: "var(--fg-mute)", fontSize: 13 }}>
              Select a drive from the left panel to browse.
            </div>
          ) : isLoading ? (
            <div style={{ padding: 24, textAlign: "center", color: "var(--fg-mute)", fontSize: 13 }}>Loading…</div>
          ) : error ? (
            <div style={{ padding: 24, textAlign: "center", color: "var(--bad)", fontSize: 13 }}>
              {(error as Error).message ?? "Failed to load directory"}
            </div>
          ) : entries.length === 0 ? (
            <div style={{ padding: 24, textAlign: "center", color: "var(--fg-mute)", fontSize: 13 }}>
              {mediaOnly ? "No images or videos in this folder." : "This folder is empty."}
            </div>
          ) : (
            entries.map((entry) => {
              const isSelected = selectedEntry?.path === entry.path;
              const isRenaming = renamingPath === entry.path;
              return (
                <div
                  key={entry.path}
                  onClick={(e) => handleEntryClick(e, entry)}
                  onDoubleClick={() => handleEntryDoubleClick(entry)}
                  onContextMenu={(e) => handleContextMenu(e, entry)}
                  style={{
                    display: "grid", gridTemplateColumns: "1fr 90px 120px",
                    alignItems: "center", padding: "5px 12px", cursor: "pointer",
                    background: isSelected ? "var(--surface-3)" : "transparent",
                    borderLeft: isSelected ? "2px solid var(--accent)" : "2px solid transparent",
                    fontSize: 13,
                  }}
                  onMouseEnter={(e) => { if (!isSelected) e.currentTarget.style.background = "var(--surface-2)"; }}
                  onMouseLeave={(e) => { if (!isSelected) e.currentTarget.style.background = "transparent"; }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
                    <span style={{ flexShrink: 0, color: entry.type === "dir" ? "var(--accent)" : entry.media_kind ? "var(--info)" : "var(--fg-dim)" }}>
                      {entry.type === "dir"
                        ? (isSelected ? <FolderOpen size={15} /> : <Folder size={15} />)
                        : entry.media_kind === "image" ? <ImageIcon size={15} />
                        : entry.media_kind === "video" ? <Film size={15} />
                        : <File size={15} />}
                    </span>
                    {isRenaming ? (
                      <RenameInput
                        initial={entry.name}
                        onConfirm={(name) => renameMutation.mutate({ path: entry.path, name })}
                        onCancel={() => setRenamingPath(null)}
                      />
                    ) : (
                      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{entry.name}</span>
                    )}
                  </div>
                  <span style={{ color: "var(--fg-mute)", fontSize: 11, fontFamily: "Geist Mono, monospace" }}>
                    {entry.type === "file" ? formatSize(entry.size_bytes) : ""}
                  </span>
                  <span style={{ color: "var(--fg-mute)", fontSize: 11 }}>
                    {formatDate(entry.modified_at)}
                  </span>
                </div>
              );
            })
          )}
        </div>

        {/* Status bar */}
        <div style={{
          padding: "4px 12px", borderTop: "1px solid var(--line)", fontSize: 11,
          color: "var(--fg-dim)", background: "var(--surface-1)", flexShrink: 0, display: "flex", gap: 12,
        }}>
          <span>{entries.length} item{entries.length !== 1 ? "s" : ""}</span>
          <span>{entries.filter((e) => e.type === "dir").length} folder{entries.filter((e) => e.type === "dir").length !== 1 ? "s" : ""}</span>
          <span>{imageCount} image{imageCount !== 1 ? "s" : ""}</span>
          {videoCount > 0 && <span>{videoCount} video{videoCount !== 1 ? "s" : ""}</span>}
        </div>
      </div>

      {/* ── Right: Preview panel ── */}
      {selectedEntry && (
        <PreviewPanel entry={selectedEntry} onClose={() => setSelectedEntry(null)} />
      )}

      {/* Context menu */}
      {ctxMenu && (
        <ContextMenu
          x={ctxMenu.x}
          y={ctxMenu.y}
          actions={ctxActions(ctxMenu.entry)}
          onClose={() => setCtxMenu(null)}
        />
      )}

      {/* Modals */}
      {modal?.type === "import" && (
        <ImportModal
          folderPath={modal.path}
          datasets={datasets}
          onClose={() => setModal(null)}
        />
      )}
      {modal?.type === "mkdir" && currentPath && (
        <MkdirModal
          parent={currentPath}
          onClose={() => setModal(null)}
          onCreated={() => qc.invalidateQueries({ queryKey: ["fs-list", currentPath] })}
        />
      )}
      {modal?.type === "delete" && (
        <DeleteConfirm
          entry={modal.entry}
          onClose={() => setModal(null)}
          onDeleted={() => { if (selectedEntry?.path === modal.entry.path) setSelectedEntry(null); }}
        />
      )}
    </div>
  );
}

function SidebarLink({ icon, label, active, onClick }: { icon: React.ReactNode; label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      style={{
        display: "flex", alignItems: "center", gap: 8, width: "100%",
        padding: "6px 12px", background: active ? "var(--surface-3)" : "none",
        borderLeft: active ? "2px solid var(--accent)" : "2px solid transparent",
        border: "none", borderRight: "none", borderTop: "none", borderBottom: "none",
        cursor: "pointer", fontSize: 12, color: active ? "var(--fg)" : "var(--fg-mute)",
        textAlign: "left", marginLeft: -0,
      }}
      onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = "var(--surface-2)"; }}
      onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = "none"; }}
    >
      <span style={{ opacity: 0.8 }}>{icon}</span>
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</span>
    </button>
  );
}
