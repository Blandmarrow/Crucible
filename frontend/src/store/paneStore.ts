import { create } from "zustand";
import { nanoid } from "./nanoid";
import { isPageType, type PageType, type PaneView } from "../contexts/PaneContext";
import { PANE_LAYOUT_KEY } from "../constants/storage";

export type { PageType, PaneView };

export interface PaneLeaf {
  type: "leaf";
  id: string;
  view: PaneView;
}

export interface PaneSplit {
  type: "split";
  id: string;
  direction: "horizontal" | "vertical";
  sizes: [number, number];
  children: [PaneTree, PaneTree];
}

export type PaneTree = PaneLeaf | PaneSplit;

interface PaneStore {
  enabled: boolean;
  layout: PaneTree;
  activePaneId: string;

  toggleEnabled: () => void;
  setEnabled: (v: boolean) => void;
  splitPane: (paneId: string, direction: "horizontal" | "vertical") => void;
  closePane: (paneId: string) => void;
  setView: (paneId: string, view: PaneView) => void;
  setActivePaneId: (id: string) => void;
  updateSizes: (splitId: string, sizes: [number, number]) => void;
  syncFromRoute: (view: PaneView) => void;
  /** Show `view` beside `fromPaneId` (the routed first pane when null): in its
   *  neighbour, or in a new pane split off it, turning split view on if it was off.
   *  Never in the first pane, which the route drives. Returns the pane. */
  openBeside: (fromPaneId: string | null, view: PaneView) => string;
}

function makeLeaf(view: PaneView): PaneLeaf {
  return { type: "leaf", id: nanoid(), view };
}

// ── Tree helpers ──────────────────────────────────────────────────────────────

function findLeaf(tree: PaneTree, id: string): PaneLeaf | null {
  if (tree.type === "leaf") return tree.id === id ? tree : null;
  return findLeaf(tree.children[0], id) ?? findLeaf(tree.children[1], id);
}

function firstLeafId(tree: PaneTree): string {
  if (tree.type === "leaf") return tree.id;
  return firstLeafId(tree.children[0]);
}

function splitNode(tree: PaneTree, targetId: string, direction: "horizontal" | "vertical"): PaneTree {
  if (tree.type === "leaf") {
    if (tree.id !== targetId) return tree;
    const newLeaf = makeLeaf({ ...tree.view });
    return {
      type: "split",
      id: nanoid(),
      direction,
      sizes: [50, 50],
      children: [tree, newLeaf],
    };
  }
  return {
    ...tree,
    children: [
      splitNode(tree.children[0], targetId, direction) as PaneLeaf | PaneSplit,
      splitNode(tree.children[1], targetId, direction) as PaneLeaf | PaneSplit,
    ],
  };
}

function closeNode(tree: PaneTree, targetId: string): PaneTree | null {
  if (tree.type === "leaf") return tree.id === targetId ? null : tree;
  const left = closeNode(tree.children[0], targetId);
  const right = closeNode(tree.children[1], targetId);
  if (left === null) return right;
  if (right === null) return left;
  return { ...tree, children: [left as PaneTree, right as PaneTree] };
}

function updateLeafView(tree: PaneTree, targetId: string, view: PaneView): PaneTree {
  if (tree.type === "leaf") {
    return tree.id === targetId ? { ...tree, view } : tree;
  }
  return {
    ...tree,
    children: [
      updateLeafView(tree.children[0], targetId, view) as PaneLeaf | PaneSplit,
      updateLeafView(tree.children[1], targetId, view) as PaneLeaf | PaneSplit,
    ],
  };
}

function updateSplitSizes(tree: PaneTree, splitId: string, sizes: [number, number]): PaneTree {
  if (tree.type === "leaf") return tree;
  if (tree.id === splitId) return { ...tree, sizes };
  return {
    ...tree,
    children: [
      updateSplitSizes(tree.children[0], splitId, sizes) as PaneLeaf | PaneSplit,
      updateSplitSizes(tree.children[1], splitId, sizes) as PaneLeaf | PaneSplit,
    ],
  };
}

function updateFirstLeaf(tree: PaneTree, view: PaneView): PaneTree {
  if (tree.type === "leaf") {
    // The route carries no chat id: a sync that leaves the pane on the same chat page
    // (a reload, the same link again) keeps the chat it was showing.
    const same = tree.view.page === view.page && tree.view.datasetId === view.datasetId;
    return { ...tree, view: { ...view, chatId: view.chatId ?? (same ? tree.view.chatId : undefined) } };
  }
  return {
    ...tree,
    children: [
      updateFirstLeaf(tree.children[0], view) as PaneLeaf | PaneSplit,
      tree.children[1],
    ],
  };
}

// ── Store ─────────────────────────────────────────────────────────────────────

const initialLeaf = makeLeaf({ page: "datasets" });
const restored = loadLayout();

// The new pane's share when something opens beside a page: the page keeps the room.
const BESIDE_SIZES: [number, number] = [62, 38];

export const usePaneStore = create<PaneStore>((set, get) => ({
  enabled: restored?.enabled ?? false,
  layout: restored?.layout ?? initialLeaf,
  activePaneId: restored?.activePaneId ?? initialLeaf.id,

  toggleEnabled: () => set((s) => ({ enabled: !s.enabled })),
  setEnabled: (v) => set({ enabled: v }),

  splitPane: (paneId, direction) =>
    set((s) => {
      const newLayout = splitNode(s.layout, paneId, direction);
      // The new pane is always the second child of the newly created split
      const split = findSplit(newLayout, paneId);
      const newLeafId = split ? firstLeafId(split.children[1]) : paneId;
      return { layout: newLayout, activePaneId: newLeafId };
    }),

  closePane: (paneId) =>
    set((s) => {
      const leaf = findLeaf(s.layout, paneId);
      if (!leaf) return s;
      const newLayout = closeNode(s.layout, paneId);
      if (!newLayout) return s; // last pane, can't close
      const newActiveId = s.activePaneId === paneId ? firstLeafId(newLayout) : s.activePaneId;
      return { layout: newLayout, activePaneId: newActiveId };
    }),

  setView: (paneId, view) =>
    set((s) => ({ layout: updateLeafView(s.layout, paneId, view) })),

  setActivePaneId: (id) => set({ activePaneId: id }),

  updateSizes: (splitId, sizes) =>
    set((s) => ({ layout: updateSplitSizes(s.layout, splitId, sizes) })),

  syncFromRoute: (view) =>
    set((s) => ({ layout: updateFirstLeaf(s.layout, view) })),

  openBeside: (fromPaneId, view) => {
    const s = get();
    if (!s.enabled) {
      // The left pane is a placeholder: RouteSyncer fills it from the route as split
      // view turns on, so the page that asked stays where it was.
      const leaf = makeLeaf(view);
      set({
        enabled: true,
        layout: { type: "split", id: nanoid(), direction: "horizontal", sizes: BESIDE_SIZES, children: [makeLeaf({ page: "datasets" }), leaf] },
        activePaneId: leaf.id,
      });
      return leaf.id;
    }
    // From outside any pane (the sidebar), "this page" is the routed one: the first pane.
    const routed = firstLeafId(s.layout);
    const from = fromPaneId && findLeaf(s.layout, fromPaneId) ? fromPaneId : routed;
    const neighbour = neighbourLeafId(s.layout, from);
    // The first pane follows the sidebar, so whatever opened there would be replaced
    // by the next click: split instead.
    if (neighbour && neighbour !== routed) {
      set({ layout: updateLeafView(s.layout, neighbour, view), activePaneId: neighbour });
      return neighbour;
    }
    const leaf = makeLeaf(view);
    set({ layout: replaceLeaf(s.layout, from, (old) => ({
      type: "split", id: nanoid(), direction: "horizontal", sizes: BESIDE_SIZES, children: [old, leaf],
    })), activePaneId: leaf.id });
    return leaf.id;
  },
}));

function replaceLeaf(tree: PaneTree, id: string, f: (leaf: PaneLeaf) => PaneTree): PaneTree {
  if (tree.type === "leaf") return tree.id === id ? f(tree) : tree;
  return { ...tree, children: [replaceLeaf(tree.children[0], id, f), replaceLeaf(tree.children[1], id, f)] };
}

/** The first leaf for which `match` holds, in reading order. */
export function findLeafWhere(tree: PaneTree, match: (view: PaneView) => boolean): PaneLeaf | null {
  if (tree.type === "leaf") return match(tree.view) ? tree : null;
  return findLeafWhere(tree.children[0], match) ?? findLeafWhere(tree.children[1], match);
}

// ── Persistence ───────────────────────────────────────────────────────────────
//
// The layout is saved so a pane pinned beside the work (the analyst chat, M12) survives
// a reload. One-shot deep-link targets are dropped on the way out: each is applied
// once per change, so restoring it would re-apply it over what the user has chosen
// since. Read back, the tree is checked node by node and dropped whole if anything is
// off, since a dev and a production install on one origin share this key and may not
// know the same pages. See docs/dev/panes-routing.md § The layout survives a reload.

interface SavedLayout {
  v: 1;
  enabled: boolean;
  layout: PaneTree;
  activePaneId: string;
}

const ONE_SHOT: (keyof PaneView)[] = ["subfolder", "sourceVideoId", "filter"];

function forStorage(tree: PaneTree): PaneTree {
  if (tree.type === "leaf") {
    const view = { ...tree.view };
    for (const k of ONE_SHOT) delete view[k];
    return { ...tree, view };
  }
  return { ...tree, children: [forStorage(tree.children[0]), forStorage(tree.children[1])] };
}

function validTree(t: unknown): t is PaneTree {
  if (!t || typeof t !== "object") return false;
  const n = t as Record<string, unknown>;
  if (typeof n.id !== "string") return false;
  if (n.type === "leaf") {
    const v = n.view as Record<string, unknown> | undefined;
    const optString = (x: unknown) => x === undefined || typeof x === "string";
    return !!v && typeof v === "object" && isPageType(v.page)
      && optString(v.datasetId) && optString(v.imageId) && optString(v.videoId) && optString(v.chatId)
      && (v.compareIds === undefined || (Array.isArray(v.compareIds) && v.compareIds.every((x) => typeof x === "string")));
  }
  if (n.type !== "split") return false;
  const sizes = n.sizes as unknown[];
  const children = n.children as unknown[];
  return (n.direction === "horizontal" || n.direction === "vertical")
    && Array.isArray(sizes) && sizes.length === 2 && sizes.every((x) => typeof x === "number" && x > 0)
    && Array.isArray(children) && children.length === 2 && children.every(validTree);
}

function loadLayout(): Omit<SavedLayout, "v"> | null {
  try {
    const raw = localStorage.getItem(PANE_LAYOUT_KEY);
    if (!raw) return null;
    const saved = JSON.parse(raw) as Partial<SavedLayout>;
    if (saved.v !== 1 || typeof saved.enabled !== "boolean" || !validTree(saved.layout)) return null;
    const active = typeof saved.activePaneId === "string" && findLeaf(saved.layout, saved.activePaneId)
      ? saved.activePaneId
      : firstLeafId(saved.layout);
    return { enabled: saved.enabled, layout: saved.layout, activePaneId: active };
  } catch {
    return null;
  }
}

let saveTimer: ReturnType<typeof setTimeout> | undefined;
function saveNow() {
  clearTimeout(saveTimer);
  saveTimer = undefined;
  const s = usePaneStore.getState();
  const saved: SavedLayout = { v: 1, enabled: s.enabled, layout: forStorage(s.layout), activePaneId: s.activePaneId };
  try {
    localStorage.setItem(PANE_LAYOUT_KEY, JSON.stringify(saved));
  } catch {
    // Storage full or blocked: split view still works, it just will not come back.
  }
}
usePaneStore.subscribe((s, prev) => {
  if (s.layout === prev.layout && s.enabled === prev.enabled && s.activePaneId === prev.activePaneId) return;
  clearTimeout(saveTimer);
  // Debounced: dragging a divider updates the sizes on every frame.
  saveTimer = setTimeout(saveNow, 250);
});
// A change still waiting when the page goes (a reload, Restart) is saved first.
if (typeof window !== "undefined") {
  window.addEventListener("pagehide", () => { if (saveTimer !== undefined) saveNow(); });
}

function findSplit(tree: PaneTree, leafId: string): PaneSplit | null {
  if (tree.type === "leaf") return null;
  if (
    (tree.children[0].type === "leaf" && tree.children[0].id === leafId) ||
    (tree.children[1].type === "leaf" && tree.children[1].id === leafId)
  ) {
    return tree;
  }
  return findSplit(tree.children[0], leafId) ?? findSplit(tree.children[1], leafId);
}

/**
 * The pane to open something *beside* `leafId` in: the first leaf of the other
 * half of the split that holds it, or null when `leafId` is the only pane (or
 * is not in the tree). Compare opens here so the gallery that asked for it
 * stays on screen. Pure, and exported so it can be reasoned about on its own.
 */
export function neighbourLeafId(tree: PaneTree, leafId: string): string | null {
  const parent = findSplit(tree, leafId);
  if (!parent) return null;
  const [first, second] = parent.children;
  const other = first.type === "leaf" && first.id === leafId ? second : first;
  return firstLeafId(other);
}

/** A pane's view as the store holds it now, ahead of any render still pending:
 *  `setView` is synchronous, while the render it starts is a React transition. */
export function currentPaneView(paneId: string): PaneView | null {
  return findLeaf(usePaneStore.getState().layout, paneId)?.view ?? null;
}
