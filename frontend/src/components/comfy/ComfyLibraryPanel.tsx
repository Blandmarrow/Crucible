import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { ChevronDown, ChevronRight, Folder, GripVertical, Lock, MoreHorizontal, Pencil, X } from "lucide-react";
import { comfyApi, type ComfyPlan, type ComfyRow, type LibraryPrompt, type LinkedMode, type PlanPrompt } from "../../api/comfy";
import { datasetsApi } from "../../api/datasets";
import ConfirmDialog from "../common/ConfirmDialog";
import ContextMenu, { type ContextMenuAction } from "../common/ContextMenu";
import MoveSubfolderModal from "../gallery/MoveSubfolderModal";
import FolderNameModal from "./FolderNameModal";
import DndRow from "./DndRow";
import { DROP_RING, LIBRARY_REPATH_EVENT, dragId, dropId } from "./dnd";
import { COMFY_LIBRARY_VIEW_KEY } from "../../constants/storage";
import { apiErrorDetail } from "../../utils/apiError";
import { loadPersisted, savePersisted } from "../../utils/persistentState";
import { buildSubfolderTree, withMissingAncestors, type SubfolderNode } from "../../utils/subfolderTree";

export type LibraryTab = "library" | "plans";

export interface ComfyLibraryPanelProps {
  /** The page's dataset; Other plans starts on it. */
  datasetId: string;
  /** The plan rows are added to. */
  plan: ComfyPlan;
  /** Every folder path of that plan, for the Add folder select. */
  folders: string[];
  /** "" = (root); the default destination for Add and Copy here. */
  folderInView: string;
  tab: LibraryTab;
  onTabChange: (tab: LibraryTab) => void;
  onClose: () => void;
}

const plural = (n: number, word: string) => `${n} ${word}${n !== 1 ? "s" : ""}`;
/** " (2 skipped: no prompt; 1 left behind: made by the blueprint)", or "" when nothing was.
 *  Same wording as copyRows.ts's toast. */
function skippedNote(noPrompt: number, linked: number): string {
  const parts = [
    noPrompt > 0 ? `${noPrompt} skipped: no prompt` : "",
    linked > 0 ? `${linked} left behind: made by the blueprint` : "",
  ].filter(Boolean);
  return parts.length ? ` (${parts.join("; ")})` : "";
}

const STATUS_BADGE: Record<ComfyRow["status"], string> = {
  pending: "badge dot",
  running: "badge dot info",
  completed: "badge dot good",
  failed: "badge dot bad",
};
const oneLine = (t: string) => t.replace(/\s+/g, " ").trim();

/** The prompt library as a side panel (M6 §8c): a global, categorized store of prompt
 *  text on one tab, and the other plans' rows to copy or move from on the other. */
export default function ComfyLibraryPanel(props: ComfyLibraryPanelProps) {
  const { tab, onTabChange, onClose } = props;
  return (
    <section className="comfy-lib" data-testid="comfy-library-panel" aria-label="Prompt library"
      style={{ display: "flex", flexDirection: "column", gap: 8, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        {/* Real tabs, not pressed buttons: the toolbar's toggle is the page's only
            "Library" button, so the e2e (and a screen reader) can tell the two apart. */}
        <div className="tabs" role="tablist" aria-label="Library view" style={{ marginBottom: 0, flex: 1 }}>
          <button role="tab" className={`tab${tab === "library" ? " active" : ""}`} aria-selected={tab === "library"}
            onClick={() => onTabChange("library")}>Library</button>
          <button role="tab" className={`tab${tab === "plans" ? " active" : ""}`} aria-selected={tab === "plans"}
            onClick={() => onTabChange("plans")}>Other plans</button>
        </div>
        <button className="icon-btn" aria-label="Close the library" title="Close" onClick={onClose}><X size={14} /></button>
      </div>
      {tab === "library" ? <LibraryTabBody {...props} /> : <OtherPlansTabBody {...props} />}
    </section>
  );
}

// ── Library tab ───────────────────────────────────────────────────────────────

function loadClosed(): string[] {
  const v = loadPersisted<{ closed: string[] }>(COMFY_LIBRARY_VIEW_KEY, { closed: [] });
  return Array.isArray(v.closed) ? v.closed.filter((p) => typeof p === "string") : [];
}

function LibraryTabBody({ datasetId, plan, folders, folderInView }: ComfyLibraryPanelProps) {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [closed, setClosed] = useState<string[]>(loadClosed);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [editingId, setEditingId] = useState<string | null>(null);
  const [menu, setMenu] = useState<{ node: SubfolderNode; x: number; y: number } | null>(null);
  const [renaming, setRenaming] = useState<SubfolderNode | null>(null);
  const [moving, setMoving] = useState<SubfolderNode | null>(null);
  const [deleting, setDeleting] = useState<SubfolderNode | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [folder, setFolder] = useState(folderInView);
  const [seenFolderInView, setSeenFolderInView] = useState(folderInView);
  const [moveTo, setMoveTo] = useState("");
  const [showMove, setShowMove] = useState(false);

  // Re-sync the Add destination when the table's folder changes (during render, no effect).
  if (seenFolderInView !== folderInView) {
    setSeenFolderInView(folderInView);
    setFolder(folderInView);
  }

  const { data: prompts = [], isLoading } = useQuery({
    queryKey: ["comfy", "library"],
    queryFn: comfyApi.libraryList,
  });

  const needle = search.trim().toLowerCase();
  const searching = needle !== "";

  // Prompts that survive the search: a category-name match keeps all of its prompts.
  const shown = useMemo(() => {
    if (!searching) return prompts;
    return prompts.filter((p) => p.category.toLowerCase().includes(needle) || p.text.toLowerCase().includes(needle));
  }, [prompts, needle, searching]);

  const byCategory = useMemo(() => {
    const m = new Map<string, LibraryPrompt[]>();
    for (const p of shown) {
      const list = m.get(p.category);
      if (list) list.push(p); else m.set(p.category, [p]);
    }
    return m;
  }, [shown]);

  const tree = useMemo(() => {
    const own = [...byCategory.entries()].map(([path, list]) => ({ path, image_count: list.length }));
    return buildSubfolderTree(withMissingAncestors(own));
  }, [byCategory]);

  // Every category the library holds (not just the ones a search shows), for Move to….
  const allCategories = useMemo(() => {
    const own = [...new Set(prompts.map((p) => p.category))].filter(Boolean).map((path) => ({ path, image_count: 0 }));
    return withMissingAncestors(own).sort((a, b) => a.path.localeCompare(b.path));
  }, [prompts]);

  // The selection acts only on cards on show: a search that hides a selected card, or a
  // folded category above it, drops it from what Add, Move, Delete and a drag act on (the
  // page's rule: never act on what the user cannot see). Ids a refetch removed fall away
  // here too, without an effect.
  const visibleIds = useMemo(() => {
    const visible = new Set<string>();
    for (const p of shown) {
      const parts = p.category.split("/");
      const folded = !searching && parts.some((_, i) => closed.includes(parts.slice(0, i + 1).join("/")));
      if (!folded) visible.add(p.id);
    }
    return visible;
  }, [shown, closed, searching]);
  const selected = useMemo(() => [...selectedIds].filter((id) => visibleIds.has(id)), [selectedIds, visibleIds]);
  const allSelected = visibleIds.size > 0 && selected.length === visibleIds.size;

  function toggleClosed(path: string) {
    setClosed((prev) => {
      const next = prev.includes(path) ? prev.filter((p) => p !== path) : [...prev, path];
      savePersisted(COMFY_LIBRARY_VIEW_KEY, { closed: next });
      return next;
    });
  }

  function repathClosed(from: string, to: string) {
    setClosed((prev) => {
      const next = prev.map((p) => (p === from ? to : p.startsWith(from + "/") ? to + p.slice(from.length) : p));
      savePersisted(COMFY_LIBRARY_VIEW_KEY, { closed: next });
      return next;
    });
  }

  // A category dragged onto another (handled by ComfyPage's drop) re-points the stored
  // folds the same way the menu's Rename and Move do.
  useEffect(() => {
    const on = (e: Event) => {
      const { from, to } = (e as CustomEvent<{ from: string; to: string }>).detail;
      repathClosed(from, to);
    };
    window.addEventListener(LIBRARY_REPATH_EVENT, on);
    return () => window.removeEventListener(LIBRARY_REPATH_EVENT, on);
  }, []);

  // A deleted category takes its folds with it, or a later category of the same name
  // would come back folded.
  function pruneClosed(path: string) {
    setClosed((prev) => {
      const next = prev.filter((p) => p !== path && !p.startsWith(path + "/"));
      if (next.length === prev.length) return prev;
      savePersisted(COMFY_LIBRARY_VIEW_KEY, { closed: next });
      return next;
    });
  }

  function toggleSelected(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  const invalidateLibrary = () => qc.invalidateQueries({ queryKey: ["comfy", "library"] });
  const clearSelection = () => setSelectedIds(new Set());

  const updateMutation = useMutation({
    mutationFn: ({ id, text }: { id: string; text: string }) => comfyApi.libraryUpdate(id, text),
    onSuccess: () => {
      invalidateLibrary();
      setEditingId(null);
    },
    onError: (err: unknown) => toast.error(apiErrorDetail(err, "Failed to save the prompt")),
  });

  const addMutation = useMutation({
    mutationFn: () => {
      const lines = prompts.filter((p) => selected.includes(p.id)).map((p) => oneLine(p.text)).filter(Boolean);
      return comfyApi.bulkAddRows(plan.id, lines, folder);
    },
    onSuccess: ({ created }) => {
      qc.invalidateQueries({ queryKey: ["comfy", "rows", plan.id] });
      qc.invalidateQueries({ queryKey: ["comfy", "plans", datasetId] });
      qc.invalidateQueries({ queryKey: ["comfy", "prompts", plan.id] });
      toast.success(`Added ${plural(created, "row")} to ${folder || "(root)"}`);
      clearSelection();
    },
    onError: (err: unknown) => toast.error(apiErrorDetail(err, "Failed to add the prompts")),
  });

  const moveMutation = useMutation({
    mutationFn: (category: string) => comfyApi.libraryMove(selected, category),
    onSuccess: ({ moved, merged }, category) => {
      invalidateLibrary();
      clearSelection();
      setMoveTo("");
      setShowMove(false);
      toast.success(`Moved ${plural(moved, "prompt")} to "${category}"` + (merged > 0 ? ` (${merged} merged with prompts already there)` : ""));
    },
    onError: (err: unknown) => toast.error(apiErrorDetail(err, "Failed to move the prompts")),
  });

  const deleteMutation = useMutation({
    mutationFn: () => comfyApi.libraryDelete(selected),
    onSuccess: ({ deleted }) => {
      invalidateLibrary();
      clearSelection();
      setConfirmDelete(false);
      toast.success(`Deleted ${plural(deleted, "prompt")} from the library`);
    },
    onError: (err: unknown) => toast.error(apiErrorDetail(err, "Failed to delete the prompts")),
  });

  const moveCategoryMutation = useMutation({
    mutationFn: ({ path, newPath }: { path: string; newPath: string }) => comfyApi.libraryMoveCategory(path, newPath),
    onSuccess: ({ moved, merged }, { path, newPath }) => {
      invalidateLibrary();
      repathClosed(path, newPath);
      setRenaming(null);
      setMoving(null);
      toast.success(`Moved ${plural(moved, "prompt")} to "${newPath}"` + (merged > 0 ? ` (${merged} merged with prompts already there)` : ""));
    },
    onError: (err: unknown) => toast.error(apiErrorDetail(err, "Failed to move the category")),
  });

  const deleteCategoryMutation = useMutation({
    mutationFn: (path: string) => comfyApi.libraryDeleteCategory(path),
    onSuccess: ({ deleted }, path) => {
      invalidateLibrary();
      pruneClosed(path);
      setDeleting(null);
      toast.success(`Deleted ${plural(deleted, "prompt")} from the library`);
    },
    onError: (err: unknown) => {
      setDeleting(null);
      toast.error(apiErrorDetail(err, "Failed to delete the category"));
    },
  });

  const hasPromptPin = plan.pinned_params.some((p) => p.is_prompt);
  const folderOptions = folders.includes(folder) || folder === "" ? folders : [...folders, folder];
  const moveTarget = moveTo.trim().replace(/^\/+|\/+$/g, "").replace(/\/+/g, "/");

  // The whole subtree from the full list: a search hides prompts the delete still takes.
  const subtreeCount = (path: string) =>
    prompts.filter((p) => p.category === path || p.category.startsWith(path + "/")).length;

  function menuActions(node: SubfolderNode): ContextMenuAction[] {
    return [
      { label: "Rename…", onClick: () => setRenaming(node) },
      { label: "Move to…", onClick: () => setMoving(node) },
      { label: "Delete…", danger: true, onClick: () => setDeleting(node) },
    ];
  }

  function renderNode(node: SubfolderNode) {
    // A search shows every surviving category open: collapse state is for browsing.
    const open = searching || !closed.includes(node.path);
    const own = byCategory.get(node.path) ?? [];
    return (
      <div key={node.path}>
        <LibraryCategoryRow
          node={node}
          open={open}
          onToggle={() => toggleClosed(node.path)}
          onMenu={(x, y) => setMenu({ node, x, y })}
        />
        {open && own.map((p) => (
          <LibraryPromptCard
            key={p.id}
            prompt={p}
            depth={node.depth + 1}
            checked={selected.includes(p.id)}
            dragIds={selected.includes(p.id) ? selected : [p.id]}
            editing={editingId === p.id}
            saving={updateMutation.isPending}
            onToggle={() => toggleSelected(p.id)}
            onEdit={() => setEditingId(p.id)}
            onCancelEdit={() => setEditingId(null)}
            onSave={(text) => updateMutation.mutate({ id: p.id, text })}
          />
        ))}
        {open && node.children.map(renderNode)}
      </div>
    );
  }

  return (
    <>
      <input
        className="input" type="search" aria-label="Search the library" placeholder="Search prompts and categories"
        style={{ fontSize: 12 }} value={search} onChange={(e) => setSearch(e.target.value)}
      />

      {visibleIds.size > 0 && (
        <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
          <button className="btn ghost sm"
            onClick={() => setSelectedIds(allSelected ? new Set() : new Set(visibleIds))}>
            {allSelected ? "Select none" : "Select all"}
          </button>
          <span style={{ color: "var(--fg-mute)" }}>{plural(visibleIds.size, "prompt")} shown</span>
        </div>
      )}

      {selected.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 12, padding: "6px 0", borderBottom: "1px solid var(--border)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ color: "var(--fg-mute)", flex: 1 }}>{selected.length} selected</span>
            <button className="btn ghost sm" onClick={clearSelection}>Clear</button>
          </div>
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <select
              className="select" aria-label="Add to folder" style={{ fontSize: 12, minWidth: 0, flex: 1 }}
              value={folder} onChange={(e) => setFolder(e.target.value)}
            >
              <option value="">(root)</option>
              {folderOptions.filter((f) => f !== "").map((f) => <option key={f} value={f}>{f}</option>)}
            </select>
            <button
              className="btn primary sm"
              disabled={!hasPromptPin || addMutation.isPending}
              title={hasPromptPin ? undefined : "This plan has no prompt pin, so there is nowhere to put the text"}
              onClick={() => addMutation.mutate()}
            >
              Add {selected.length} to plan
            </button>
          </div>
          {showMove ? (
            <div style={{ display: "flex", gap: 6 }}>
              <input
                className="input" list="comfy-lib-categories" aria-label="Move to category" placeholder="Category" maxLength={512}
                style={{ fontSize: 12, minWidth: 0, flex: 1 }} autoFocus
                value={moveTo} onChange={(e) => setMoveTo(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && moveTarget) moveMutation.mutate(moveTarget); }}
              />
              <datalist id="comfy-lib-categories">
                {allCategories.map((c) => <option key={c.path} value={c.path} />)}
              </datalist>
              <button className="btn sm" disabled={!moveTarget || moveMutation.isPending} onClick={() => moveMutation.mutate(moveTarget)}>Move</button>
              <button className="btn ghost sm" onClick={() => { setShowMove(false); setMoveTo(""); }}>Cancel</button>
            </div>
          ) : (
            <div style={{ display: "flex", gap: 6 }}>
              <button className="btn ghost sm" onClick={() => setShowMove(true)}>Move to…</button>
              <button className="btn ghost sm" style={{ color: "var(--bad)" }} onClick={() => setConfirmDelete(true)}>
                Delete ({selected.length})
              </button>
            </div>
          )}
        </div>
      )}

      <div style={{ overflowY: "auto", minHeight: 0, flex: 1 }}>
        {isLoading ? (
          <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>Loading…</p>
        ) : prompts.length === 0 ? (
          <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>
            The library is empty. Select rows and use Save to library to add prompts.
          </p>
        ) : tree.length === 0 ? (
          <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>Nothing matches “{search.trim()}”.</p>
        ) : (
          <>
            <LibraryRootTarget />
            {tree.map(renderNode)}
          </>
        )}
      </div>

      {menu && <ContextMenu x={menu.x} y={menu.y} onClose={() => setMenu(null)} actions={menuActions(menu.node)} />}

      {renaming && (
        <FolderNameModal
          noun="category" mode="rename" path={renaming.path} isPending={moveCategoryMutation.isPending}
          onConfirm={(newPath) => moveCategoryMutation.mutate({ path: renaming.path, newPath })}
          onClose={() => setRenaming(null)}
        />
      )}
      {moving && (
        <MoveSubfolderModal
          node={moving} subfolders={allCategories} isPending={moveCategoryMutation.isPending}
          onConfirm={(newPath) => moveCategoryMutation.mutate({ path: moving.path, newPath })}
          onClose={() => setMoving(null)}
        />
      )}
      {deleting && (
        <ConfirmDialog
          title="Delete category"
          message={`Delete category ${deleting.path} and its ${plural(subtreeCount(deleting.path), "prompt")}? Plans that already use them are unaffected.`}
          confirmLabel="Delete" danger
          onConfirm={() => deleteCategoryMutation.mutate(deleting.path)}
          onCancel={() => setDeleting(null)}
        />
      )}
      {confirmDelete && (
        <ConfirmDialog
          title="Delete library prompts"
          message={`Delete ${plural(selected.length, "prompt")} from the library? Plans that already use them are unaffected.`}
          confirmLabel="Delete" danger
          onConfirm={() => deleteMutation.mutate()}
          onCancel={() => setConfirmDelete(false)}
        />
      )}
    </>
  );
}

/** One category in the tree. Everything arrives by props, so the drag-and-drop stage
 *  can wrap it without a hook assuming a parent context. */
export function LibraryCategoryRow({ node, open, onToggle, onMenu }: {
  node: SubfolderNode;
  open: boolean;
  onToggle: () => void;
  onMenu: (x: number, y: number) => void;
}) {
  return (
    <DndRow
      drop={{ id: dropId.category(node.path), data: { kind: "category", path: node.path } }}
      drag={{ id: dragId.category(node.path), data: { kind: "category", path: node.path } }}
      className="comfy-rail-item"
      data-testid="comfy-lib-category"
      data-category={node.path}
      style={{ paddingLeft: node.depth * 14 }}
      onContextMenu={(e) => { e.preventDefault(); onMenu(e.clientX, e.clientY); }}
    >
      {({ handle, isOver }) => (<>
      {isOver && <span aria-hidden style={{ position: "absolute", inset: 0, borderRadius: "var(--r)", pointerEvents: "none", ...DROP_RING }} />}
      <button
        className="comfy-rail-twisty"
        aria-label={`${open ? "Collapse" : "Expand"} ${node.path}`}
        aria-expanded={open}
        onClick={onToggle}
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
      </button>
      <button {...handle?.props} ref={handle?.attach} className="comfy-rail-node" title={node.path} onClick={onToggle}>
        <Folder size={13} style={{ flexShrink: 0, color: "var(--accent)" }} />
        <span className="comfy-rail-label">{node.label}</span>
        <span className="comfy-rail-count mono">{node.totalCount}</span>
      </button>
      <button
        className="icon-btn comfy-rail-more"
        aria-label={`Category actions for ${node.path}`}
        aria-haspopup="menu"
        onClick={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          onMenu(r.left, r.bottom + 2);
        }}
      >
        <MoreHorizontal size={14} />
      </button>
      </>)}
    </DndRow>
  );
}

/** The library's top level as a drop target, so a dragged category can leave its parent
 *  (the folder tree's (root) does the same for folders). */
function LibraryRootTarget() {
  return (
    <DndRow
      drop={{ id: dropId.category(""), data: { kind: "category", path: "" } }}
      className="comfy-rail-item"
      style={{ fontSize: 11.5, color: "var(--fg-mute)", padding: "3px 6px" }}
    >
      {({ isOver }) => (<>
        {isOver && <span aria-hidden style={{ position: "absolute", inset: 0, borderRadius: "var(--r)", pointerEvents: "none", ...DROP_RING }} />}
        <span>Top level</span>
      </>)}
    </DndRow>
  );
}

/** One library prompt as a compact card, or its editor while `editing`. */
export function LibraryPromptCard({ prompt, depth, checked, dragIds, editing, saving, onToggle, onEdit, onCancelEdit, onSave }: {
  prompt: LibraryPrompt;
  depth: number;
  checked: boolean;
  /** What a drag of this card moves: the selection when the card is in it. */
  dragIds: string[];
  editing: boolean;
  saving: boolean;
  onToggle: () => void;
  onEdit: () => void;
  onCancelEdit: () => void;
  onSave: (text: string) => void;
}) {
  return (
    <DndRow
      drag={editing ? null : { id: dragId.lib(prompt.id), data: { kind: "lib", ids: dragIds } }}
      data-testid="comfy-lib-prompt"
      data-prompt-id={prompt.id}
      style={{
        display: "flex", gap: 6, alignItems: "flex-start", padding: "4px 4px", marginLeft: depth * 14 + 4,
        fontSize: 12, borderBottom: "1px solid var(--border)", minWidth: 0,
      }}
    >
      {({ handle }) => (<>
      {handle && (
        <button {...handle.props} ref={handle.attach} className="comfy-rail-twisty"
          aria-label="Drag prompt" title="Drag prompt" style={{ cursor: "grab", height: 18 }}>
          <GripVertical size={12} />
        </button>
      )}
      <input type="checkbox" className="checkbox" aria-label="Select prompt" checked={checked} onChange={onToggle} style={{ marginTop: 2 }} />
      {editing ? (
        <PromptEditor initial={prompt.text} saving={saving} onCancel={onCancelEdit} onSave={onSave} />
      ) : (
        <>
          <span style={{ flex: 1, minWidth: 0, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{prompt.text}</span>
          <button className="icon-btn" aria-label="Edit prompt" title="Edit the text" onClick={onEdit}><Pencil size={12} /></button>
        </>
      )}
      </>)}
    </DndRow>
  );
}

function PromptEditor({ initial, saving, onCancel, onSave }: {
  initial: string; saving: boolean; onCancel: () => void; onSave: (text: string) => void;
}) {
  const [draft, setDraft] = useState(initial);
  const trimmed = draft.trim();
  const canSave = !!trimmed && trimmed !== initial.trim() && !saving;
  return (
    <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 4 }}>
      <textarea
        className="input" aria-label="Prompt text" autoFocus rows={3} style={{ fontSize: 12, width: "100%", resize: "vertical" }}
        value={draft} onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (canSave) onSave(trimmed); }
          else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onCancel(); }
        }}
      />
      <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
        <button className="btn ghost sm" onClick={onCancel}>Cancel</button>
        <button className="btn primary sm" disabled={!canSave} onClick={() => onSave(trimmed)}>Save</button>
      </div>
    </div>
  );
}

// ── Other plans tab ───────────────────────────────────────────────────────────

function OtherPlansTabBody({ datasetId, plan }: ComfyLibraryPanelProps) {
  const qc = useQueryClient();
  const [sourceDatasetId, setSourceDatasetId] = useState(datasetId);
  const [sourcePlanId, setSourcePlanId] = useState("");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [asking, setAsking] = useState(false);

  const { data: datasets = [] } = useQuery({ queryKey: ["datasets"], queryFn: () => datasetsApi.list() });
  const { data: plans = [] } = useQuery({
    queryKey: ["comfy", "plans", sourceDatasetId],
    queryFn: () => comfyApi.listPlans(sourceDatasetId),
    enabled: !!sourceDatasetId,
  });

  // Not the plan being filled; fall back to the first other plan, derived rather than an effect.
  const eligible = useMemo(() => plans.filter((p) => p.id !== plan.id), [plans, plan.id]);
  const srcId = eligible.some((p) => p.id === sourcePlanId) ? sourcePlanId : eligible[0]?.id ?? "";

  const { data: prompts = [], isLoading } = useQuery({
    queryKey: ["comfy", "prompts", srcId],
    queryFn: () => comfyApi.listPlanPrompts(srcId),
    enabled: !!srcId,
  });
  const { data: srcRows = [] } = useQuery({
    queryKey: ["comfy", "rows", srcId],
    queryFn: () => comfyApi.listRows(srcId),
    enabled: !!srcId,
  });

  const rowById = useMemo(() => new Map<string, ComfyRow>(srcRows.map((r) => [r.id, r])), [srcRows]);
  const items = useMemo(
    () => prompts.map((p) => ({ prompt: p, row: rowById.get(p.row_id) })),
    [prompts, rowById],
  );
  const selected = useMemo(() => {
    const live = new Set(prompts.map((p) => p.row_id));
    return [...selectedIds].filter((id) => live.has(id));
  }, [selectedIds, prompts]);
  const linkedCount = selected.filter((id) => rowById.get(id)?.blueprint_linked).length;
  const allSelected = prompts.length > 0 && selected.length === prompts.length;

  function pickDataset(id: string) {
    setSourceDatasetId(id);
    setSourcePlanId("");
    setSelectedIds(new Set());
    setAsking(false);
  }
  function pickPlan(id: string) {
    setSourcePlanId(id);
    setSelectedIds(new Set());
    setAsking(false);
  }
  function toggle(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  const copyMutation = useMutation({
    mutationFn: ({ move, linked }: { move: boolean; linked: LinkedMode }) =>
      comfyApi.copyRows(plan.id, { source_plan_id: srcId, row_ids: selected, move, subfolder: null, linked }),
    onSuccess: (res, { move }) => {
      qc.invalidateQueries({ queryKey: ["comfy", "rows", plan.id] });
      qc.invalidateQueries({ queryKey: ["comfy", "plans", plan.dataset_id] });
      qc.invalidateQueries({ queryKey: ["comfy", "prompts", plan.id] });
      qc.invalidateQueries({ queryKey: ["comfy", "rows", srcId] });
      qc.invalidateQueries({ queryKey: ["comfy", "prompts", srcId] });
      qc.invalidateQueries({ queryKey: ["comfy", "plans", sourceDatasetId] });
      qc.invalidateQueries({ queryKey: ["comfy", "plan", srcId] });
      qc.invalidateQueries({ queryKey: ["comfy", "blueprint-preview", srcId] });
      const n = (move ? res.moved : res.copied) ?? 0;
      toast.success(
        `${move ? "Moved" : "Copied"} ${plural(n, "row")}` + skippedNote(res.skipped_no_prompt, res.skipped_linked),
      );
      setSelectedIds(new Set());
      setAsking(false);
    },
    onError: (err: unknown) => {
      setAsking(false);
      toast.error(apiErrorDetail(err, "Failed to copy the rows"));
    },
  });

  function run(move: boolean) {
    if (copyMutation.isPending) return;
    if (move && linkedCount > 0) setAsking(true);
    else copyMutation.mutate({ move, linked: "refuse" });
  }

  return (
    <>
      <div style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 12 }}>
        <select className="select" aria-label="Dataset" style={{ fontSize: 12, minWidth: 0 }}
          value={sourceDatasetId} onChange={(e) => pickDataset(e.target.value)}>
          {datasets.map((d) => (
            <option key={d.id} value={d.id}>{d.name}{d.id === datasetId ? " (this dataset)" : ""}</option>
          ))}
        </select>
        <select className="select" aria-label="Plan" style={{ fontSize: 12, minWidth: 0 }}
          value={srcId} disabled={eligible.length === 0} onChange={(e) => pickPlan(e.target.value)}>
          {eligible.length === 0 && <option value="">(no other plans)</option>}
          {eligible.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.row_count})</option>)}
        </select>
        <p style={{ fontSize: 11.5, color: "var(--fg-mute)", margin: 0 }}>
          Copies keep each row's folder and the values this plan also has per row.
        </p>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
        <button className="btn ghost sm" disabled={prompts.length === 0}
          onClick={() => setSelectedIds(allSelected ? new Set() : new Set(prompts.map((p) => p.row_id)))}>
          {allSelected ? "Select none" : "Select all"}
        </button>
        <span style={{ color: "var(--fg-mute)" }}>{selected.length} selected</span>
      </div>

      <div style={{ overflowY: "auto", minHeight: 0, flex: 1 }}>
        {isLoading ? (
          <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>Loading…</p>
        ) : items.length === 0 ? (
          <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>
            {srcId ? "This plan has no prompts." : "Pick a plan to browse its rows."}
          </p>
        ) : (
          items.map(({ prompt, row }) => (
            <OtherPlanRowCard
              key={prompt.row_id}
              prompt={prompt}
              folder={row?.subfolder ?? ""}
              linked={!!row?.blueprint_linked}
              status={row?.status}
              error={row?.error_msg ?? null}
              checked={selected.includes(prompt.row_id)}
              onToggle={() => toggle(prompt.row_id)}
              sourcePlanId={srcId}
              sourceDatasetId={sourceDatasetId}
              dragIds={selected.includes(prompt.row_id) ? selected : [prompt.row_id]}
            />
          ))
        )}
      </div>

      {asking ? (
        <div role="alertdialog" aria-label="Move blueprint rows" data-testid="comfy-linked-confirm" style={{
          display: "flex", flexDirection: "column", gap: 8, padding: "8px 10px",
          border: "1px solid var(--warn)", background: "var(--warn-bg)", borderRadius: "var(--r)", fontSize: 12,
        }}>
          <span style={{ lineHeight: 1.45 }}>
            {linkedCount} of these {plural(selected.length, "row")} came from the blueprint of the source plan.
            Moving them takes them out of that blueprint, and it then counts their folder as short, so its
            next Top up refills it.
          </span>
          <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "flex-end", gap: 6 }}>
            <button className="btn ghost sm" onClick={() => setAsking(false)}>Cancel</button>
            <button className="btn sm" disabled={copyMutation.isPending}
              onClick={() => copyMutation.mutate({ move: true, linked: "skip" })}>
              Move hand-made rows only
            </button>
            <button className="btn primary sm" disabled={copyMutation.isPending}
              onClick={() => copyMutation.mutate({ move: true, linked: "unlink" })}>
              Move and unlink
            </button>
          </div>
        </div>
      ) : (
        <div style={{ display: "flex", gap: 6 }}>
          <button className="btn primary sm" disabled={selected.length === 0 || copyMutation.isPending} onClick={() => run(false)}>
            Copy here ({selected.length})
          </button>
          <button className="btn sm" disabled={selected.length === 0 || copyMutation.isPending} onClick={() => run(true)}>
            Move here ({selected.length})
          </button>
        </div>
      )}
    </>
  );
}

/** One row of another plan, with its folder; a lock marks a row the source blueprint still counts. */
export function OtherPlanRowCard({ prompt, folder, linked, status, error, checked, onToggle, sourcePlanId, sourceDatasetId, dragIds }: {
  prompt: PlanPrompt;
  folder: string;
  linked: boolean;
  /** The row's run status, styled like the rows table's status cell. */
  status?: ComfyRow["status"];
  error?: string | null;
  checked: boolean;
  onToggle: () => void;
  sourcePlanId: string;
  sourceDatasetId: string;
  /** What a drag of this row copies: the selection when the row is in it. */
  dragIds: string[];
}) {
  return (
    <DndRow
      drag={{
        id: dragId.xrow(prompt.row_id),
        data: { kind: "xrows", planId: sourcePlanId, datasetId: sourceDatasetId, ids: dragIds },
      }}
      style={{ display: "flex", alignItems: "flex-start", gap: 2 }}
    >
    {({ handle }) => (<>
    {handle && (
        <button {...handle.props} ref={handle.attach} className="comfy-rail-twisty"
          aria-label="Drag row" title="Drag row" style={{ cursor: "grab", height: 18 }}>
          <GripVertical size={12} />
        </button>
      )}
    <label
      data-testid="comfy-xplan-row"
      data-row-id={prompt.row_id}
      style={{
        flex: 1, display: "flex", gap: 6, alignItems: "flex-start", padding: "4px 4px", fontSize: 12,
        borderBottom: "1px solid var(--border)", cursor: "pointer", minWidth: 0,
      }}
    >
      <input type="checkbox" className="checkbox" aria-label="Select row" checked={checked} onChange={onToggle} style={{ marginTop: 2 }} />
      <span style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 2 }}>
        <span className="mono" style={{ fontSize: 10.5, color: "var(--fg-mute)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {folder || "(root)"}
        </span>
        <span style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{prompt.prompt}</span>
      </span>
      {status && (
        <span className={STATUS_BADGE[status]} title={error ?? undefined}
          style={{ flexShrink: 0, fontSize: 10.5, cursor: error ? "help" : undefined }}>
          {status}
        </span>
      )}
      {linked && <Lock size={11} style={{ flexShrink: 0, color: "var(--fg-mute)", marginTop: 2 }} aria-label="From the blueprint" role="img" />}
    </label>
    </>)}
    </DndRow>
  );
}
