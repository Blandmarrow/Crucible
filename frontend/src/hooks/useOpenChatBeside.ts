import { useCallback } from "react";
import { useLocation } from "react-router";
import { usePaneContext } from "../contexts/PaneContext";
import { findLeafWhere, usePaneStore } from "../store/paneStore";

/**
 * Open a dataset's analyst chat beside the current page (M12 phase 2e): in the
 * neighbouring pane, or in a new pane split off this one, turning split view on if it
 * is off. A pane already showing that dataset's chat is focused instead, so the action
 * never stacks a second chat. Works from inside a pane and from outside one (the
 * sidebar), where "this one" is the active pane.
 */
export function useOpenChatBeside() {
  const ctx = usePaneContext();
  const { pathname } = useLocation();
  return useCallback(
    (datasetId: string) => {
      const store = usePaneStore.getState();
      // Already reading this dataset's chat, routed: there is nothing to open beside.
      if (!store.enabled && pathname.startsWith(`/datasets/${datasetId}/chat`)) return;
      const open = store.enabled
        ? findLeafWhere(store.layout, (v) => v.page === "chat" && v.datasetId === datasetId)
        : null;
      const target = open ? open.id : store.openBeside(ctx?.paneId ?? null, { page: "chat", datasetId });
      // After the click has finished bubbling, as in `useOpenBeside`: the calling pane's
      // wrapper makes itself active on click, and would otherwise win.
      queueMicrotask(() => usePaneStore.getState().setActivePaneId(target));
    },
    [ctx, pathname],
  );
}
