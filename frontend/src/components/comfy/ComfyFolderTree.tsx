import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Folder, Lock, MoreHorizontal, Plus } from "lucide-react";
import ContextMenu, { type ContextMenuAction } from "../common/ContextMenu";
import type { ComfyRow } from "../../api/comfy";
import { buildSubfolderTree, type SubfolderNode } from "../../utils/subfolderTree";
import { BLUEPRINT_FOLDER_REASON, lockedFolderTest, planFolderItems, type FolderFilter } from "./folderTree";

interface Props {
  rows: ComfyRow[];
  declared: string[];
  selected: FolderFilter;
  onSelect: (f: FolderFilter) => void;
  closed: Set<string>;
  onToggle: (path: string) => void;
  /** A run is live: folder writes would 409, so the menu offers none. */
  busy: boolean;
  onNew: (parent: string) => void;
  onRename: (node: SubfolderNode) => void;
  onMove: (node: SubfolderNode) => void;
  onDelete: (node: SubfolderNode) => void;
}

/** The selected plan's folders (M6 §8b), under the plan rail. Built from row folders
 *  plus `declared_folders` with the gallery's `buildSubfolderTree`. Clicking filters
 *  the table; the ⋯ menu or a right-click makes, renames, moves and deletes folders. */
export default function ComfyFolderTree({
  rows, declared, selected, onSelect, closed, onToggle, busy, onNew, onRename, onMove, onDelete,
}: Props) {
  const [menu, setMenu] = useState<{ node: SubfolderNode | null; x: number; y: number } | null>(null);
  const tree = useMemo(() => buildSubfolderTree(planFolderItems(rows, declared)), [rows, declared]);
  const isLocked = useMemo(() => lockedFolderTest(rows), [rows]);
  const rootCount = rows.filter((r) => r.subfolder === "").length;

  function menuActions(node: SubfolderNode | null): ContextMenuAction[] {
    const busyReason = busy ? "Wait for the run to finish" : undefined;
    const newAction: ContextMenuAction = {
      label: node ? "New folder inside…" : "New folder…",
      onClick: () => onNew(node?.path ?? ""),
    };
    if (!node) return [newAction];
    const locked = isLocked(node.path);
    const reason = busyReason ?? (locked ? BLUEPRINT_FOLDER_REASON : undefined);
    return [
      newAction,
      { label: "Rename…", disabled: !!reason, title: reason, onClick: () => onRename(node) },
      { label: "Move to…", disabled: !!reason, title: reason, onClick: () => onMove(node) },
      { label: "Delete…", danger: true, disabled: !!reason, title: reason, onClick: () => onDelete(node) },
    ];
  }

  function openMenu(node: SubfolderNode | null, x: number, y: number) {
    setMenu({ node, x, y });
  }

  function renderNode(node: SubfolderNode) {
    const open = !closed.has(node.path);
    const active = selected === node.path;
    const locked = isLocked(node.path);
    return (
      <div key={node.path}>
        <div
          className={`comfy-rail-item${active ? " active" : ""}`}
          style={{ paddingLeft: node.depth * 14 }}
          onContextMenu={(e) => { e.preventDefault(); openMenu(node, e.clientX, e.clientY); }}
        >
          {node.children.length > 0 ? (
            <button
              className="comfy-rail-twisty"
              aria-label={`${open ? "Collapse" : "Expand"} ${node.path}`}
              aria-expanded={open}
              onClick={() => onToggle(node.path)}
            >
              {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            </button>
          ) : (
            <span style={{ width: 18, flexShrink: 0 }} />
          )}
          <button
            className="comfy-rail-node"
            aria-current={active ? "true" : undefined}
            title={node.path}
            onClick={() => onSelect(node.path)}
          >
            <Folder size={13} style={{ flexShrink: 0, color: "var(--accent)" }} />
            <span className="comfy-rail-label">{node.label}</span>
            {locked && (
              <Lock size={11} style={{ flexShrink: 0, color: "var(--fg-mute)" }} aria-label="From the blueprint" role="img" />
            )}
            <span className="comfy-rail-count mono">{node.totalCount}</span>
          </button>
          <button
            className="icon-btn comfy-rail-more"
            aria-label={`Folder actions for ${node.path}`}
            onClick={(e) => {
              const r = e.currentTarget.getBoundingClientRect();
              openMenu(node, r.left, r.bottom + 2);
            }}
          >
            <MoreHorizontal size={14} />
          </button>
        </div>
        {open && node.children.map(renderNode)}
      </div>
    );
  }

  return (
    <section className="comfy-rail-panel" data-testid="comfy-folder-tree" aria-label="Folders">
      <div className="comfy-rail-h">
        <span style={{ flex: 1 }}>Folders</span>
        <button
          className="icon-btn comfy-rail-more"
          aria-label="New folder"
          title="New folder (inside the selected one)"
          onClick={() => onNew(selected ?? "")}
        >
          <Plus size={13} />
        </button>
      </div>
      <div className={`comfy-rail-item${selected === null ? " active" : ""}`}>
        <span style={{ width: 18, flexShrink: 0 }} />
        <button className="comfy-rail-node" aria-current={selected === null ? "true" : undefined} onClick={() => onSelect(null)}>
          <span className="comfy-rail-label">All rows</span>
          <span className="comfy-rail-count mono">{rows.length}</span>
        </button>
      </div>
      <div
        className={`comfy-rail-item${selected === "" ? " active" : ""}`}
        onContextMenu={(e) => { e.preventDefault(); openMenu(null, e.clientX, e.clientY); }}
      >
        <span style={{ width: 18, flexShrink: 0 }} />
        <button
          className="comfy-rail-node"
          aria-current={selected === "" ? "true" : undefined}
          title="Rows with no folder: their images go straight into the run's base folder"
          onClick={() => onSelect("")}
        >
          <Folder size={13} style={{ flexShrink: 0, color: "var(--fg-mute)" }} />
          <span className="comfy-rail-label">(root)</span>
          <span className="comfy-rail-count mono">{rootCount}</span>
        </button>
      </div>
      {tree.map(renderNode)}

      {menu && (
        <ContextMenu x={menu.x} y={menu.y} onClose={() => setMenu(null)} actions={menuActions(menu.node)} />
      )}
    </section>
  );
}
