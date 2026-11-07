import { useCallback, useEffect, useState } from "react";
import { COMFY_ROWS_VIEW_KEY } from "../../constants/storage";
import { loadPersisted, savePersisted } from "../../utils/persistentState";

/** Which folders are collapsed, per plan: in the folder tree, and as group headers in
 *  the Rows table. Stored as *closed* paths, so a folder nobody has touched is open. */
export interface RowsView {
  closedFolders: string[];
  closedGroups: string[];
}

const DEFAULTS: RowsView = { closedFolders: [], closedGroups: [] };

export const rowsViewKey = (planId: string) => `${COMFY_ROWS_VIEW_KEY}-${planId}`;

function load(planId: string | null): RowsView {
  if (!planId) return { ...DEFAULTS };
  const v = loadPersisted(rowsViewKey(planId), DEFAULTS);
  // A hand-edited or older blob must not crash the tree.
  return {
    closedFolders: Array.isArray(v.closedFolders) ? v.closedFolders.filter((p) => typeof p === "string") : [],
    closedGroups: Array.isArray(v.closedGroups) ? v.closedGroups.filter((p) => typeof p === "string") : [],
  };
}

/** The view for `planId`, reloaded when the plan changes and saved on every toggle. */
export function usePlanRowsView(planId: string | null) {
  const [state, setState] = useState<{ planId: string | null; view: RowsView }>(() => ({ planId, view: load(planId) }));
  // Reload during render on a plan switch, so the first paint of the new plan never
  // shows the old plan's collapsed set.
  let current = state;
  if (state.planId !== planId) {
    current = { planId, view: load(planId) };
    setState(current);
  }

  useEffect(() => {
    if (current.planId) savePersisted(rowsViewKey(current.planId), current.view);
  }, [current]);

  const toggle = useCallback((field: keyof RowsView, path: string) => {
    setState((prev) => {
      const set = new Set(prev.view[field]);
      if (set.has(path)) set.delete(path); else set.add(path);
      return { ...prev, view: { ...prev.view, [field]: [...set] } };
    });
  }, []);

  /** Re-point the stored paths after a folder rename or move. */
  const repath = useCallback((from: string, to: string) => {
    const map = (p: string) => (p === from ? to : p.startsWith(from + "/") ? to + p.slice(from.length) : p);
    setState((prev) => ({
      ...prev,
      view: { closedFolders: prev.view.closedFolders.map(map), closedGroups: prev.view.closedGroups.map(map) },
    }));
  }, []);

  return { view: current.view, toggle, repath };
}
