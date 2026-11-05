import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { usePopover } from "../../hooks/usePopover";
import { ContextMenuItem, type ContextMenuAction } from "./ContextMenu";
import { MENU_PANEL_STYLE } from "./menuStyle";

interface Props {
  /** The trigger's visible text; a caret is added after it. */
  label: ReactNode;
  actions: ContextMenuAction[];
  /** Which edge of the trigger the menu lines up with, when it fits. */
  align?: "left" | "right";
  className?: string;
  title?: string;
  testId?: string;
}

const EDGE_PAD = 8;

/** The box that would cut the menu off: the nearest scrolling ancestor, else the window. */
function clipBox(el: HTMLElement): { left: number; right: number } {
  for (let p = el.parentElement; p; p = p.parentElement) {
    const { overflowX, overflowY } = getComputedStyle(p);
    if (/(auto|scroll|hidden|clip)/.test(overflowX + overflowY)) {
      const r = p.getBoundingClientRect();
      return { left: r.left, right: r.right };
    }
  }
  return { left: 0, right: window.innerWidth };
}

/**
 * A button that opens a menu anchored under it (M6 §8d's *Add ▾*).
 *
 * The button-anchored sibling of `ContextMenu`, which is fixed at a pointer: the
 * entries are the same `ContextMenuAction`s rendered by the same `ContextMenuItem`,
 * and the open/close plumbing is `usePopover` (outside click, Escape returning focus
 * to the trigger). Like both of those, no arrow-key roving focus: Tab reaches the
 * entries. `GalleryPage`'s select-all caret menu predates this and is a candidate
 * to move onto it.
 *
 * `align` is a preference: a toolbar that wraps can carry the trigger to the other
 * end of a line, so on open the menu measures itself and takes the other edge when
 * the preferred one would run past its scroller and be clipped.
 *
 * An entry's `onClick` runs inside the click, so a hidden file input's `.click()`
 * keeps the user activation the browser's file picker needs.
 */
export default function MenuButton({ label, actions, align = "left", className = "btn ghost sm", title, testId }: Props) {
  const { open, setOpen, anchorRef, triggerRef } = usePopover<HTMLDivElement>();
  const panelRef = useRef<HTMLDivElement>(null);
  const [side, setSide] = useState(align);

  useLayoutEffect(() => {
    if (!open) return;
    const panel = panelRef.current;
    const trigger = triggerRef.current;
    if (!panel || !trigger) return;
    const box = clipBox(anchorRef.current ?? trigger);
    const t = trigger.getBoundingClientRect();
    const w = panel.getBoundingClientRect().width;
    const fitsRight = t.right - w >= box.left + EDGE_PAD; // opens leftward from the right edge
    const fitsLeft = t.left + w <= box.right - EDGE_PAD;  // opens rightward from the left edge
    if (align === "right" && !fitsRight && fitsLeft) setSide("left");
    else if (align === "left" && !fitsLeft && fitsRight) setSide("right");
  }, [open, align, anchorRef, triggerRef]);

  return (
    <div ref={anchorRef} style={{ position: "relative", display: "inline-flex" }}>
      <button
        ref={triggerRef}
        className={className}
        title={title}
        data-testid={testId}
        aria-haspopup="menu"
        aria-expanded={open}
        // Each open starts from the preferred edge; the layout effect may then flip it.
        onClick={() => { setSide(align); setOpen((o) => !o); }}
      >
        {label}
        <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
          <path d="M3 6l5 5 5-5" />
        </svg>
      </button>
      {open && (
        <div
          ref={panelRef}
          role="menu"
          aria-orientation="vertical"
          style={{
            position: "absolute", top: "calc(100% + 4px)", [side]: 0, zIndex: 1000,
            whiteSpace: "nowrap", ...MENU_PANEL_STYLE,
          }}
        >
          {actions.map((a) => (
            <ContextMenuItem
              key={a.label}
              action={a}
              // Focus goes back to the trigger before the entry's dialog mounts, so the
              // dialog records the trigger, not a menu item about to unmount, as the
              // element to hand focus back to when it closes.
              onDone={() => { setOpen(false); triggerRef.current?.focus(); }}
            />
          ))}
        </div>
      )}
    </div>
  );
}
