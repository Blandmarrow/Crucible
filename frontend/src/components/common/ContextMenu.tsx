import { useEffect, useLayoutEffect, useRef, useState } from "react";

/** One row in a `ContextMenu`. `icon` is optional — a menu whose entries have no
 *  natural glyph renders without the icon gutter rather than with blank slots. */
export interface ContextMenuAction {
  label: string;
  icon?: React.ReactNode;
  onClick: () => void;
  danger?: boolean;
}

export interface ContextMenuProps {
  /** Viewport coordinates of the pointer that opened the menu. */
  x: number;
  y: number;
  actions: ContextMenuAction[];
  onClose: () => void;
}

const EDGE_PAD = 8;

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
 * Not implemented on purpose: arrow-key roving focus, portaling, submenus. A known
 * lookalike that is *not* this component is `GalleryPage`'s select-all caret menu —
 * it is absolutely positioned to a button rather than fixed at a pointer, so it has
 * a different anchoring contract.
 */
export default function ContextMenu({ x, y, actions, onClose }: ContextMenuProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ x, y });

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

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      // Stop the page's own document-level Escape handler from also firing —
      // dismissing the menu should not, say, clear the gallery's selection.
      e.stopPropagation();
      onClose();
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  return (
    <div
      ref={ref}
      role="menu"
      aria-orientation="vertical"
      style={{
        position: "fixed", left: pos.x, top: pos.y, zIndex: 1000,
        background: "var(--surface-2)", border: "1px solid var(--line-2)",
        borderRadius: "var(--r)", boxShadow: "0 8px 24px rgba(0,0,0,.4)",
        minWidth: 180, padding: "4px 0",
      }}
    >
      {actions.map((a) => (
        <button
          key={a.label}
          role="menuitem"
          onClick={() => { a.onClick(); onClose(); }}
          style={{
            display: "flex", alignItems: "center", gap: 8,
            width: "100%", padding: "7px 14px",
            fontSize: 13, background: "none", border: "none",
            color: a.danger ? "var(--bad)" : "var(--fg)",
            cursor: "pointer", textAlign: "left",
          }}
          onMouseEnter={(e) => (e.currentTarget.style.background = "var(--surface-3)")}
          onMouseLeave={(e) => (e.currentTarget.style.background = "none")}
        >
          {a.icon && <span style={{ opacity: 0.7 }}>{a.icon}</span>}
          {a.label}
        </button>
      ))}
    </div>
  );
}
