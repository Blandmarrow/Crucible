import { create } from "zustand";
import { nanoid } from "./nanoid";
import type { PageType, PaneView } from "../contexts/PaneContext";

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
  if (tree.type === "leaf") return { ...tree, view };
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

export const usePaneStore = create<PaneStore>((set) => ({
  enabled: false,
  layout: initialLeaf,
  activePaneId: initialLeaf.id,

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
}));

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
