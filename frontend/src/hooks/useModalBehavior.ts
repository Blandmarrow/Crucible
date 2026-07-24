import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type MouseEvent, type RefObject } from "react";

/**
 * Keyboard + screen-reader behavior for a modal dialog, as props to spread.
 *
 * A hook rather than a wrapper component on purpose: the app's modals have
 * heterogeneous markup (Tailwind overlays, inline-styled panels, three z-index
 * tiers — DirPickerModal deliberately stacks over ImportFolderModal) and a
 * wrapper would have to reproduce all of it. Spreading `panelProps` onto the
 * existing panel `<div>` changes nothing visual.
 *
 * What it adds:
 * - `role="dialog"` + `aria-modal` + an accessible name, so screen readers
 *   announce the panel as a dialog rather than as loose text.
 * - Focus moves into the panel on mount and back to the previously focused
 *   element on unmount.
 * - Escape closes. Tab and Shift+Tab cycle inside the panel.
 *
 * The handlers live on the **panel**, not on `window`, which is what makes
 * stacked modals behave: only the panel the user is actually in reacts, and the
 * one underneath stays inert. Escape also calls `stopPropagation()`, so a page's
 * own window-level Escape handler (GalleryPage, ImageDetailPage) no longer fires
 * behind an open dialog.
 */

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

interface Options {
  /** Called on Escape, and on a backdrop click when `closeOnBackdrop` is set. */
  onClose: () => void;
  /** Accessible name for the dialog — normally the visible title. */
  label?: string;
  /**
   * Whether a click on the overlay closes. Off by default: it is enabled only
   * where that affordance already existed, and never on a destructive confirm.
   */
  closeOnBackdrop?: boolean;
  /**
   * Move focus into the panel on mount. Pass `false` when the modal already
   * places focus itself (ConfirmDialog picks Cancel or Confirm by preference).
   * An element that self-focuses via the React `autoFocus` attribute is left
   * alone either way — focus is only moved when nothing inside has it yet.
   */
  autoFocus?: boolean;
}

interface ModalBehavior {
  overlayProps: {
    onClick?: (e: MouseEvent<HTMLDivElement>) => void;
    onMouseDown: (e: MouseEvent<HTMLDivElement>) => void;
  };
  panelProps: {
    ref: RefObject<HTMLDivElement | null>;
    role: "dialog";
    "aria-modal": true;
    "aria-label"?: string;
    tabIndex: -1;
    onKeyDown: (e: KeyboardEvent<HTMLDivElement>) => void;
  };
}

export function useModalBehavior({ onClose, label, closeOnBackdrop = false, autoFocus = true }: Options): ModalBehavior {
  const panelRef = useRef<HTMLDivElement>(null);

  // Captured during the first render, which is the only moment it is still the
  // element that opened the modal: React applies `autoFocus` during commit, so
  // by the time effects run the focus has already moved inside the panel.
  const [opener] = useState(() => document.activeElement as HTMLElement | null);
  // The element focus goes to on mount, resolved once and reused. Refs survive
  // StrictMode's mount → unmount → mount, so the dev-only remount lands focus
  // back where the first mount put it instead of on some other control.
  const initialFocusRef = useRef<HTMLElement | null>(null);

  // `onClose` is deliberately not a dependency: callers pass inline arrows, so
  // re-running this would yank focus back to the first field on every keystroke.
  useEffect(() => {
    const panel = panelRef.current;
    if (!panel) return;
    initialFocusRef.current ??=
      (panel.contains(document.activeElement) ? document.activeElement as HTMLElement : null)
      ?? panel.querySelector<HTMLElement>(FOCUSABLE)
      ?? panel;
    if (autoFocus) initialFocusRef.current.focus();
    return () => {
      // A modal that closes by deleting the thing that opened it leaves a
      // detached node behind; focusing it would silently drop focus to <body>.
      if (opener?.isConnected) opener.focus();
    };
  }, [autoFocus, opener]);

  // Fallback for Escape when focus has been lost to <body> — which happens
  // whenever the focused control unmounts (DirPickerModal's inline new-folder
  // row closing, a list row disappearing). The panel handler cannot fire then,
  // because the event never enters the panel. Deliberately narrow: it acts only
  // when nothing at all is focused, and only for the last dialog in the
  // document, so a stacked picker still closes before the modal beneath it.
  // Listening on `document` (page handlers live on `window`) lets it stop the
  // event before a page-level Escape handler sees it, same as the panel does.
  useEffect(() => {
    function onDocumentKeyDown(e: globalThis.KeyboardEvent) {
      const panel = panelRef.current;
      if (e.key !== "Escape" || !panel) return;
      if (document.activeElement && document.activeElement !== document.body) return;
      const dialogs = document.querySelectorAll('[role="dialog"]');
      if (dialogs[dialogs.length - 1] !== panel) return;
      e.stopPropagation();
      onClose();
    }
    document.addEventListener("keydown", onDocumentKeyDown);
    return () => document.removeEventListener("keydown", onDocumentKeyDown);
  }, [onClose]);

  const onKeyDown = useCallback((e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      onClose();
      return;
    }
    if (e.key !== "Tab") return;
    const panel = panelRef.current;
    if (!panel) return;
    // Queried per keydown, not once on mount: DirPickerModal's list of folders
    // changes as the user navigates, and every modal has conditional controls.
    const nodes = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE))
      .filter((el) => el.getClientRects().length > 0);
    if (nodes.length === 0) {
      e.preventDefault();
      panel.focus();
      return;
    }
    const active = document.activeElement;
    const atEdge = e.shiftKey ? nodes[0] : nodes[nodes.length - 1];
    if (active === atEdge || active === panel || !panel.contains(active)) {
      e.preventDefault();
      // A dialog nested inside another (ConfirmDialog inside PromptLibraryModal)
      // has already wrapped within itself — don't let the outer one wrap again.
      e.stopPropagation();
      (e.shiftKey ? nodes[nodes.length - 1] : nodes[0]).focus();
    }
  }, [onClose]);

  return {
    overlayProps: {
      onClick: closeOnBackdrop
        ? (e: MouseEvent<HTMLDivElement>) => { if (e.target === e.currentTarget) onClose(); }
        : undefined,
      // A press on the backdrop otherwise blurs whatever was focused inside the
      // panel, and Escape — handled on the panel — would stop working until the
      // user clicked back in. Suppressing the default focus change keeps it.
      // The click event still fires, so backdrop-close is unaffected.
      onMouseDown: (e: MouseEvent<HTMLDivElement>) => { if (e.target === e.currentTarget) e.preventDefault(); },
    },
    panelProps: {
      ref: panelRef,
      role: "dialog",
      "aria-modal": true,
      "aria-label": label,
      tabIndex: -1,
      onKeyDown,
    },
  };
}
