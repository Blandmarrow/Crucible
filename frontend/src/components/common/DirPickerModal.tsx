import { useState, useEffect } from "react";
import { Folder, FolderOpen, HardDrive, ChevronRight, ArrowUp, Plus, X, Check } from "lucide-react";
import { filesystemApi, type FsEntry } from "../../api/filesystem";
import { parentOf, breadcrumbsFromPath } from "../../utils/pathUtils";

interface Props {
  initialPath?: string;
  title?: string;
  confirmLabel?: string;
  onConfirm: (path: string) => void;
  onCancel: () => void;
}

export default function DirPickerModal({ initialPath = "", title = "Select output folder", confirmLabel = "Select folder", onConfirm, onCancel }: Props) {
  const trimmed = initialPath.trim();
  const [currentPath, setCurrentPath] = useState<string | null>(trimmed || null);
  const [entries, setEntries] = useState<FsEntry[]>([]);
  const [roots, setRoots] = useState<{ path: string; label: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedPath, setSelectedPath] = useState(trimmed);
  const [newFolderOpen, setNewFolderOpen] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    filesystemApi.roots().then((r) => setRoots(r.roots));
  }, []);

  useEffect(() => {
    if (currentPath === null) { setEntries([]); return; }
    let cancelled = false;
    setLoading(true);
    setError("");
    filesystemApi.list(currentPath)
      .then((r) => { if (!cancelled) setEntries(r.entries.filter((e) => e.type === "dir")); })
      .catch(() => { if (!cancelled) setError("Could not read directory."); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [currentPath]);

  function navigate(path: string) {
    setCurrentPath(path);
    setSelectedPath(path);
    setNewFolderOpen(false);
    setNewFolderName("");
  }

  async function createFolder() {
    if (!newFolderName.trim() || !currentPath) return;
    try {
      const res = await filesystemApi.mkdir(currentPath, newFolderName.trim());
      navigate(res.path);
    } catch {
      setError("Failed to create folder.");
    }
  }

  const parent = currentPath ? parentOf(currentPath) : null;
  const crumbs = currentPath ? breadcrumbsFromPath(currentPath) : [];

  return (
    <div
      style={{
        position: "fixed", inset: 0, zIndex: 200,
        background: "rgba(0,0,0,.65)",
        display: "flex", alignItems: "center", justifyContent: "center",
      }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}
    >
      <div style={{
        background: "var(--surface-1)", border: "1px solid var(--line)",
        borderRadius: "var(--r)", width: 540, maxHeight: "80vh",
        display: "flex", flexDirection: "column", overflow: "hidden",
      }}>
        {/* Header */}
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "12px 16px", borderBottom: "1px solid var(--line)", flexShrink: 0,
        }}>
          <span style={{ fontWeight: 600, fontSize: 14 }}>{title}</span>
          <button className="icon-btn" onClick={onCancel}><X size={15} /></button>
        </div>

        {/* Breadcrumb / path bar */}
        <div style={{
          display: "flex", alignItems: "center", gap: 4,
          padding: "8px 12px", borderBottom: "1px solid var(--line)",
          flexShrink: 0, flexWrap: "wrap", minHeight: 36,
        }}>
          <button
            className="icon-btn"
            style={{ width: 24, height: 24 }}
            title="Drive roots"
            onClick={() => { setCurrentPath(null); setSelectedPath(""); }}
          >
            <HardDrive size={13} />
          </button>
          {crumbs.map((c, i) => (
            <span key={c.path} style={{ display: "flex", alignItems: "center", gap: 2 }}>
              <ChevronRight size={12} style={{ opacity: 0.4 }} />
              <button
                style={{
                  background: "none", border: "none", cursor: "pointer",
                  color: i === crumbs.length - 1 ? "var(--fg)" : "var(--fg-muted)",
                  fontSize: 12, padding: "1px 3px", borderRadius: 3,
                }}
                onClick={() => navigate(c.path)}
              >
                {c.label}
              </button>
            </span>
          ))}
        </div>

        {/* Toolbar */}
        <div style={{
          display: "flex", alignItems: "center", gap: 6,
          padding: "6px 12px", borderBottom: "1px solid var(--line)", flexShrink: 0,
        }}>
          <button
            className="btn sm"
            disabled={currentPath === null}
            onClick={() => { if (parent) navigate(parent); else { setCurrentPath(null); setSelectedPath(""); } }}
            title="Go up"
          >
            <ArrowUp size={13} />
          </button>
          {currentPath && (
            <button className="btn sm" onClick={() => setNewFolderOpen((v) => !v)}>
              <Plus size={13} /> New folder
            </button>
          )}
          {error && <span style={{ color: "var(--bad)", fontSize: 12 }}>{error}</span>}
        </div>

        {/* New folder inline form */}
        {newFolderOpen && (
          <div style={{
            display: "flex", alignItems: "center", gap: 6,
            padding: "6px 12px", borderBottom: "1px solid var(--line)", flexShrink: 0,
          }}>
            <input
              autoFocus
              className="input"
              placeholder="Folder name"
              value={newFolderName}
              style={{ flex: 1 }}
              onChange={(e) => setNewFolderName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") createFolder(); if (e.key === "Escape") setNewFolderOpen(false); }}
            />
            <button className="btn sm primary" onClick={createFolder} disabled={!newFolderName.trim()}>
              <Check size={13} /> Create
            </button>
            <button className="btn sm" onClick={() => setNewFolderOpen(false)}><X size={13} /></button>
          </div>
        )}

        {/* Directory listing */}
        <div style={{ flex: 1, overflowY: "auto", padding: "4px 0" }}>
          {currentPath === null ? (
            roots.map((r) => (
              <button key={r.path} onClick={() => navigate(r.path)} style={rowStyle(false)}>
                <HardDrive size={15} style={{ opacity: 0.7, flexShrink: 0 }} />
                <span style={{ fontSize: 13 }}>{r.label}</span>
              </button>
            ))
          ) : loading ? (
            <div style={{ padding: "20px 16px", color: "var(--fg-muted)", fontSize: 13 }}>Loading…</div>
          ) : entries.length === 0 ? (
            <div style={{ padding: "20px 16px", color: "var(--fg-muted)", fontSize: 13 }}>No subfolders</div>
          ) : (
            entries.map((e) => (
              <button key={e.path} onClick={() => navigate(e.path)} style={rowStyle(e.path === selectedPath)}>
                {e.path === selectedPath
                  ? <FolderOpen size={15} style={{ color: "var(--accent)", flexShrink: 0 }} />
                  : <Folder size={15} style={{ opacity: 0.6, flexShrink: 0 }} />
                }
                <span style={{ fontSize: 13 }}>{e.name}</span>
              </button>
            ))
          )}
        </div>

        {/* Selected path display + footer */}
        <div style={{
          borderTop: "1px solid var(--line)", padding: "10px 14px",
          flexShrink: 0, display: "flex", flexDirection: "column", gap: 10,
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 11, color: "var(--fg-muted)", flexShrink: 0 }}>Path:</span>
            <input
              className="input"
              style={{ flex: 1, fontSize: 12 }}
              value={selectedPath}
              onChange={(e) => setSelectedPath(e.target.value)}
              placeholder="Type or navigate to a folder"
            />
          </div>
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button className="btn" onClick={onCancel}>Cancel</button>
            <button
              className="btn primary"
              disabled={!selectedPath.trim()}
              onClick={() => onConfirm(selectedPath.trim())}
            >
              {confirmLabel}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function rowStyle(active: boolean): React.CSSProperties {
  return {
    display: "flex", alignItems: "center", gap: 8,
    width: "100%", padding: "6px 16px", textAlign: "left",
    background: active ? "var(--surface-3)" : "none",
    border: "none", cursor: "pointer", color: "var(--fg)",
  };
}
