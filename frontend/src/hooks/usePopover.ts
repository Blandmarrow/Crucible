import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Open/close plumbing for a panel anchored to a trigger button.
 *
 * The non-modal sibling of `useModalBehavior`: that hook is dialog-shaped
 * (`role="dialog"`, `aria-modal`, focus trap) and `docs/dev/styling.md` rules it
 * out for anything that is not a dialog. A caret menu or a dropdown filter is
 * not, so outside-click and Escape live here instead — one implementation
 * rather than one per call site, which is the same answer `ContextMenu` gives
 * for pointer-anchored menus.
 *
 * `anchorRef` goes on the `position: relative` wrapper that contains *both* the
 * trigger and the panel — outside-click is measured against it, so a click
 * inside the panel must not count as outside. `triggerRef` goes on the button,
 * and exists only so Escape can hand focus back rather than dropping it on
 * `<body>`.
 *
 * Escape calls `stopPropagation`: `GalleryPage` and `ImageDetailPage` both carry
 * window-level Escape handlers that would otherwise clear the selection or close
 * the pane behind the popover being dismissed.
 *
 * Not implemented on purpose (the `ContextMenu` precedent): arrow-key roving
 * focus, portaling, viewport clamping. A panel that needs to escape a scrolling
 * ancestor renders inline instead of absolutely — see `LabelPicker`'s
 * `placement` prop.
 */
export function usePopover<T extends HTMLElement = HTMLDivElement>() {
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<T>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  const close = useCallback((returnFocus: boolean) => {
    setOpen(false);
    if (returnFocus) triggerRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (anchorRef.current && !anchorRef.current.contains(e.target as Node)) close(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      close(true);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);

  return { open, setOpen, anchorRef, triggerRef };
}
