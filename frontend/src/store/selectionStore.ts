import { create } from "zustand";

interface SelectionStore {
  selectedIds: Set<string>;
  datasetByImageId: Map<string, string>;
  toggle: (id: string, datasetId: string) => void;
  selectAll: (ids: string[], datasetId: string) => void;
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
  selectAll: (ids, datasetId) =>
    set((s) => {
      const next = new Set(ids);
      const nextMap = new Map(s.datasetByImageId);
      for (const id of s.selectedIds) {
        if (!next.has(id)) nextMap.delete(id);
      }
      for (const id of ids) nextMap.set(id, datasetId);
      return { selectedIds: next, datasetByImageId: nextMap, count: next.size };
    }),
  clear: () => set({ selectedIds: new Set(), datasetByImageId: new Map(), count: 0 }),
  isSelected: (id) => get().selectedIds.has(id),
}));
