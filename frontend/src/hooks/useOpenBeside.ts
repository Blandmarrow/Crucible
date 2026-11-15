import { useCallback } from "react";
import { useNavigate } from "react-router";
import { usePaneContext } from "../contexts/PaneContext";
import type { PaneView } from "../contexts/PaneContext";
import { neighbourLeafId, usePaneStore } from "../store/paneStore";

/**
 * Open a page *beside* the current one. In split view that is the neighbouring
 * pane (`neighbourLeafId`), which then becomes the active pane, so whatever asked
 * for it stays on screen: the gallery that opened a comparison, or the comparison
 * that opened an image's detail view. With one pane, or outside split view, it
 * behaves like `usePaneNavigate().go`.
 *
 * `opts.replace` applies to the routed case only, as with `go`.
 */
export function useOpenBeside() {
  const navigate = useNavigate();
  const ctx = usePaneContext();

  return useCallback(
    (url: string, view: PaneView, opts?: { replace?: boolean }) => {
      if (!ctx) {
        navigate(url, opts?.replace ? { replace: true } : undefined);
        return;
      }
      const store = usePaneStore.getState();
      const target = neighbourLeafId(store.layout, ctx.paneId) ?? ctx.paneId;
      store.setView(target, view);
      // After the click has finished bubbling: the calling pane's wrapper
      // (`PaneContainer`) makes *itself* active on click, and set now, that would
      // run second and win, leaving the opened page's key guards dead until the
      // user clicked it. React dispatches the whole bubble inside one native
      // listener, so a microtask lands after it.
      queueMicrotask(() => usePaneStore.getState().setActivePaneId(target));
    },
    [ctx, navigate],
  );
}
