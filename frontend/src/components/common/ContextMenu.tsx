import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { MENU_PANEL_STYLE } from "./menuStyle";

/** One row in a `ContextMenu`. `icon` is optional — a menu whose entries have no
 *  natural glyph renders without the icon gutter rather than with blank slots. */
export interface ContextMenuAction {
  label: string;
  icon?: React.ReactNode;
  onClick: () => void;
  danger?: boolean;
  /** Shown but not clickable; `title` should then say why. */
  disabled?: boolean;
  /** Tooltip, e.g. the reason an entry is disabled. */
  title?: string;
}

export interface ContextMenuProps {
  /** Viewport coordinates of the pointer that opened the menu. */
  x: number;
  y: number;
  actions: ContextMenuAction[];
  onClose: () => void;
}

const EDGE_PAD = 8;

const ENABLED_ITEMS = '[role="menuitem"]:not([aria-disabled="true"])';

function focusOpener(opener: HTMLElement | null) {
  if (opener && opener !== document.body && opener.isConnected) opener.focus({ preventScroll: true });
}

/** Eat the one `click` that follows a press on `opener`, then stand down. Module-level
 *  because the menu that installs it unmounts straight away. */
function swallowNextClick(opener: HTMLElement) {
  const eat = (e: MouseEvent) => {
    if (e.target instanceof Node && opener.contains(e.target)) {
      e.stopPropagation();
      e.preventDefault();
    }
  };
  const stop = () => {
    document.removeEventListener("click", eat, true);
    document.removeEventListener("mouseup", onUp, true);
  };
  // The click is dispatched right after mouseup, so a 0 ms timer from mouseup runs after it.
  const onUp = () => { setTimeout(stop, 0); };
  document.addEventListener("click", eat, true);
  document.addEventListener("mouseup", onUp, true);
}

/**
 * A pointer-anchored context menu, `position: fixed` at `(x, y)`.
 *
 * Shared by `FileBrowserPage`'s entry rows and `GalleryPage`'s subfolder rows —
 * import it, never re-inline it.
 *
 * It deliberately does **not** use `useModalBehavior`: that hook is modal-shaped
 * (focus trap, `role="dialog"`, `aria-modal`) and a context menu is not a dialog.
 * Escape-to-close and outside-click-to-close therefore live here instead, which is
 * this component's answer to the "never hand-roll Escape" rule — one implementation,
 * not one per call site.
 *
 * Keyboard: the first enabled entry takes focus on open; Arrow Up/Down, Home and End
 * move between enabled entries (an `aria-disabled` one is skipped). The element that had
 * focus when the menu opened is the "opener": Escape, and choosing an entry, hand focus
 * back to it right after the entry's `onClick` returns, in the same handler, so before
 * any dialog that entry opens renders: the dialog then records a live element to return to (`useModalBehavior` captures `document.activeElement` while
 * it renders). A menu opened by right-click usually has `<body>` or a tile as opener,
 * and returning focus there is harmless.
 *
 * Pressing the opener again while the menu is open closes it rather than reopening it:
 * the outside `mousedown` closes the menu, and the `click` that follows on the opener
 * is swallowed once so the caller's handler does not open a fresh one. This needs the
 * opener to be a button marked `aria-haspopup="menu"` (the ⋯ and grip triggers) that
 * took focus on press, which Chrome does and Safari and
 * Firefox on macOS do not; there the old close-then-reopen remains.
 *
 * Not implemented on purpose: portaling, submenus. A known
 * lookalike that is *not* this component is `GalleryPage`'s select-all caret menu —
 * it is absolutely positioned to a button rather than fixed at a pointer, so it has
 * a different anchoring contract.
 */
export default function ContextMenu({ x, y, actions, onClose }: ContextMenuProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ x, y });
  // Read while rendering, before the menu takes focus: the element that opened it.
  const [opener] = useState(() => document.activeElement as HTMLElement | null);

  // Clamp into the viewport after measuring. Required rather than cosmetic: the
  // gallery sidebar is 180 px wide at the left edge, so a menu opened on a row near
  // the bottom of a long tree would otherwise hang off the window.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const maxX = window.innerWidth - r.width - EDGE_PAD;
    const maxY = window.innerHeight - r.height - EDGE_PAD;
    const nx = Math.max(EDGE_PAD, Math.min(x, maxX));
    const ny = Math.max(EDGE_PAD, Math.min(y, maxY));
    if (nx !== pos.x || ny !== pos.y) setPos({ x: nx, y: ny });
  }, [x, y, pos.x, pos.y, actions.length]);

  const returnFocus = () => focusOpener(opener);

  // Focus the first enabled entry on open.
  useEffect(() => {
    ref.current?.querySelector<HTMLElement>(ENABLED_ITEMS)?.focus({ preventScroll: true });
  }, []);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (!ref.current || ref.current.contains(e.target as Node)) return;
      // A press on the button that opened the menu is a toggle: close, and keep the
      // click that follows from reopening.
      // Only a real menu trigger toggles. A right-click focuses whatever button it lands on
      // (a gallery folder row's name), and that button's next click must still do its own job.
      if (opener && opener.getAttribute("aria-haspopup") === "menu" && opener.contains(e.target as Node)) swallowNextClick(opener);
      onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        // Stop a page-level Escape handler (ImageDetailPage's draw-cancel) or an
        // enclosing dialog from also reacting: dismissing the menu dismisses only it.
        e.stopPropagation();
        focusOpener(opener);
        onClose();
        return;
      }
      if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key) || !ref.current) return;
      const items = Array.from(ref.current.querySelectorAll<HTMLElement>(ENABLED_ITEMS));
      if (items.length === 0) return;
      e.preventDefault();
      e.stopPropagation();
      const i = items.indexOf(document.activeElement as HTMLElement);
      let next: number;
      if (e.key === "Home") next = 0;
      else if (e.key === "End") next = items.length - 1;
      else if (e.key === "ArrowDown") next = i < 0 ? 0 : (i + 1) % items.length;
      else next = i < 0 ? items.length - 1 : (i - 1 + items.length) % items.length;
      items[next].focus({ preventScroll: true });
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose, opener]);

  return (
    <div
      ref={ref}
      role="menu"
      aria-orientation="vertical"
      style={{ position: "fixed", left: pos.x, top: pos.y, zIndex: 1000, ...MENU_PANEL_STYLE }}
    >
      {actions.map((a) => <ContextMenuItem key={a.label} action={a} onDone={() => { returnFocus(); onClose(); }} />)}
    </div>
  );
}

/**
 * One entry of a menu: `ContextMenu`'s rows and `MenuButton`'s. A disabled entry
 * swallows the click and leaves the menu open, so its `title` can say why.
 */
export function ContextMenuItem({ action: a, onDone }: { action: ContextMenuAction; onDone: () => void }) {
  return (
    <button
      role="menuitem"
      // aria-disabled, not `disabled`: a disabled button shows no tooltip, and
      // `title` is where the reason lives.
      aria-disabled={a.disabled || undefined}
      title={a.title}
      onClick={() => { if (a.disabled) return; a.onClick(); onDone(); }}
      style={{
        display: "flex", alignItems: "center", gap: 8,
        width: "100%", padding: "7px 14px",
        fontSize: 13, background: "none", border: "none",
        color: a.danger ? "var(--bad)" : "var(--fg)",
        cursor: a.disabled ? "default" : "pointer", textAlign: "left",
        opacity: a.disabled ? 0.45 : 1,
      }}
      onMouseEnter={(e) => { if (!a.disabled) e.currentTarget.style.background = "var(--surface-3)"; }}
      onMouseLeave={(e) => (e.currentTarget.style.background = "none")}
    >
      {a.icon && <span style={{ opacity: 0.7 }}>{a.icon}</span>}
      {a.label}
    </button>
  );
}
