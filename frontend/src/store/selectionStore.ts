import { create } from "zustand";

interface SelectionStore {
  selectedIds: Set<string>;
  datasetByImageId: Map<string, string>;
  toggle: (id: string, datasetId: string) => void;
  /** Add these ids, keeping everything already selected. Every bulk select is
   *  additive: this store is the *only* record of a selection, so a "select all"
   *  that replaced the set would silently discard whatever the user gathered on
   *  the previous page, in the previous subfolder, or in the other pane — the
   *  ids are not recoverable from anywhere else once dropped. */
  selectMany: (ids: string[], datasetId: string) => void;
  /** Drop these ids, leaving the rest of the selection alone. */
  deselectMany: (ids: string[]) => void;
  /** Drop every id belonging to one dataset. The pane-scoped counterpart to
   *  `clear` — a gallery's "Deselect all" means its own dataset, not the split
   *  pane's unrelated selection. */
  clearDataset: (datasetId: string) => void;
  replaceRange: (toAdd: string[], toRemove: string[], datasetId: string) => void;
  clear: () => void;
  isSelected: (id: string) => boolean;
  count: number;
}

export const useSelectionStore = create<SelectionStore>((set, get) => ({
  selectedIds: new Set(),
  datasetByImageId: new Map(),
  count: 0,
  toggle: (id, datasetId) =>
    set((s) => {
      const next = new Set(s.selectedIds);
      const nextMap = new Map(s.datasetByImageId);
      if (next.has(id)) {
        next.delete(id);
        nextMap.delete(id);
      } else {
        next.add(id);
        nextMap.set(id, datasetId);
      }
      return { selectedIds: next, datasetByImageId: nextMap, count: next.size };
    }),
  selectMany: (ids, datasetId) =>
    set((s) => {
      const next = new Set(s.selectedIds);
      const nextMap = new Map(s.datasetByImageId);
      for (const id of ids) { next.add(id); nextMap.set(id, datasetId); }
      return { selectedIds: next, datasetByImageId: nextMap, count: next.size };
    }),
  deselectMany: (ids) =>
    set((s) => {
      const next = new Set(s.selectedIds);
      const nextMap = new Map(s.datasetByImageId);
      for (const id of ids) { next.delete(id); nextMap.delete(id); }
      return { selectedIds: next, datasetByImageId: nextMap, count: next.size };
    }),
  clearDataset: (datasetId) =>
    set((s) => {
      const next = new Set(s.selectedIds);
      const nextMap = new Map(s.datasetByImageId);
      for (const id of s.selectedIds) {
        if (nextMap.get(id) === datasetId) { next.delete(id); nextMap.delete(id); }
      }
      return { selectedIds: next, datasetByImageId: nextMap, count: next.size };
    }),
  replaceRange: (toAdd, toRemove, datasetId) =>
    set((s) => {
      const next = new Set(s.selectedIds);
      const nextMap = new Map(s.datasetByImageId);
      for (const id of toRemove) { next.delete(id); nextMap.delete(id); }
      for (const id of toAdd) { next.add(id); nextMap.set(id, datasetId); }
      return { selectedIds: next, datasetByImageId: nextMap, count: next.size };
    }),
  clear: () => set({ selectedIds: new Set(), datasetByImageId: new Map(), count: 0 }),
  isSelected: (id) => get().selectedIds.has(id),
}));
