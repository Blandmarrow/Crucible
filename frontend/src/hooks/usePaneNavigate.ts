import { useCallback } from "react";
import { useNavigate } from "react-router";
import { usePaneContext } from "../contexts/PaneContext";
import { usePaneStore } from "../store/paneStore";
import type { PaneView } from "../contexts/PaneContext";

export function usePaneNavigate() {
  const navigate = useNavigate();
  const ctx = usePaneContext();
  const setView = usePaneStore((s) => s.setView);

  // Navigate to a page — updates pane view when inside a pane, URL when not
  const go = useCallback(
    (url: string, view: PaneView, opts?: { replace?: boolean }) => {
      if (ctx) {
        setView(ctx.paneId, view);
      } else {
        navigate(url, opts?.replace ? { replace: true } : undefined);
      }
    },
    [ctx, setView, navigate],
  );

  // Go back — navigates to fallbackView inside a pane, history.back() when not
  const back = useCallback(
    (fallbackView: PaneView) => {
      if (ctx) {
        setView(ctx.paneId, fallbackView);
      } else {
        navigate(-1);
      }
    },
    [ctx, setView, navigate],
  );

  return { go, back };
}
