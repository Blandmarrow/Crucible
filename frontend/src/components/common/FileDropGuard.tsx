import { useEffect } from "react";

/** True when a native file input is under the pointer: dropping onto one is the browser's
 *  own default (it fills the input), which a preventDefault here would cancel. */
function overFileInput(e: DragEvent): boolean {
  const t = e.target;
  return t instanceof Element && !!t.closest('input[type="file"]');
}

/**
 * App-wide guard against a stray desktop-file drop. Released on a part of the page with
 * no drop zone, a file makes the browser navigate the tab to it and the app is lost.
 *
 * Window listeners in the bubble phase, so every real zone (the gallery grid, the
 * datasets page, the rows table, the workflow panel, the image cards) has already run and
 * called `preventDefault` on the events it handles. This acts only on a `Files` drag that
 * nothing claimed, and shows the "not allowed" cursor so the user sees the drop is inert.
 * Renders nothing; mount it once beside `RouteSyncer`.
 */
export default function FileDropGuard() {
  useEffect(() => {
    const guard = (e: DragEvent) => {
      if (!e.dataTransfer?.types.includes("Files")) return;
      if (e.defaultPrevented || overFileInput(e)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "none";
    };
    window.addEventListener("dragover", guard);
    window.addEventListener("drop", guard);
    return () => {
      window.removeEventListener("dragover", guard);
      window.removeEventListener("drop", guard);
    };
  }, []);
  return null;
}
