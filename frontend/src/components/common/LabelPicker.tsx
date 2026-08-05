import { useEffect, useId, useMemo, useRef, useState } from "react";

import type { Label } from "../../api/labels";
import { usePopover } from "../../hooks/usePopover";

interface CheckListProps {
  labels: Label[];
  /** Ids currently ticked. Accepts either shape — call sites hold both. */
  selected: string[] | Set<string>;
  onToggle: (id: string) => void;
  /** Optional per-label tally, rendered right-aligned. */
  counts?: Record<string, number>;
  disabled?: boolean;
  searchPlaceholder?: string;
  /** Names the `role="group"` wrapper. */
  ariaLabel: string;
  /** Rendered under the list — the Any/All + Unlabelled controls at two call sites. */
  footer?: React.ReactNode;
  /** Focus the search box on mount (the popover placement wants it, inline does not). */
  autoFocus?: boolean;
}

/**
 * A searchable checkbox list over the label vocabulary.
 *
 * This is the shared body behind every place labels are *picked* — the gallery
 * filter, the image detail rail, the Export filter panel and the bulk modal —
 * and it replaced a chip-per-label row at all four. A chip row costs permanent
 * width per vocabulary entry, which does not scale past a handful of labels;
 * a fixed-height scrolling list costs the same whether there are three labels
 * or three hundred.
 *
 * Deliberately not a `listbox`: these are real checkboxes, so Tab/Space and the
 * focus ring come from the platform. No arrow-key roving — the same non-goal
 * `ContextMenu` documents, and no other list in the app has it either.
 *
 * Knows nothing about filter semantics. Mutual exclusion between a label
 * selection and "unlabelled", the match mode, and any request live at the call
 * site, which passes them in as `footer`.
 */
export function LabelCheckList({
  labels, selected, onToggle, counts, disabled,
  searchPlaceholder = "Filter labels…", ariaLabel, footer, autoFocus,
}: CheckListProps) {
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (autoFocus) searchRef.current?.focus();
  }, [autoFocus]);

  const has = useMemo(
    () => (id: string) => (Array.isArray(selected) ? selected.includes(id) : selected.has(id)),
    [selected],
  );

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return labels;
    return labels.filter((l) => l.name.toLowerCase().includes(q));
  }, [labels, query]);

  return (
    <div role="group" aria-label={ariaLabel} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {/* No `.search-wrap` and no clear-×: `gallery-restore.spec.ts` scopes its
          `getByTitle('Clear')` to that class, and a second one inside the
          toolbar would make it ambiguous. */}
      <input
        ref={searchRef}
        className="input"
        placeholder={searchPlaceholder}
        aria-label={searchPlaceholder}
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") e.preventDefault(); }}
        style={{ width: "100%" }}
      />

      <div style={{ maxHeight: 240, overflowY: "auto", display: "flex", flexDirection: "column" }}>
        {visible.length === 0 && (
          <span style={{ fontSize: 12, color: "var(--fg-mute)", padding: "4px 2px" }}>
            No labels match “{query.trim()}”.
          </span>
        )}
        {visible.map((l) => (
          <label
            key={l.id}
            style={{
              display: "flex", alignItems: "center", gap: 7, padding: "4px 2px",
              fontSize: 12.5, cursor: disabled ? "default" : "pointer",
              opacity: disabled ? 0.6 : 1, whiteSpace: "nowrap",
            }}
          >
            <input
              type="checkbox"
              className="checkbox"
              checked={has(l.id)}
              disabled={disabled}
              onChange={() => onToggle(l.id)}
            />
            <span aria-hidden style={{ width: 7, height: 7, borderRadius: "50%", background: l.color, flex: "0 0 auto" }} />
            <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis" }}>{l.name}</span>
            {counts && (
              <span style={{ color: "var(--fg-mute)", fontSize: 11 }}>{counts[l.id] ?? 0}</span>
            )}
          </label>
        ))}
      </div>

      {footer && (
        <div style={{ borderTop: "1px solid var(--line)", paddingTop: 6 }}>{footer}</div>
      )}
    </div>
  );
}

interface PickerProps extends Omit<CheckListProps, "autoFocus"> {
  /** What the trigger button reads when closed — the caller summarises its own state. */
  triggerContent: React.ReactNode;
  triggerAriaLabel: string;
  /** Outlines the trigger in `--accent`, so an engaged filter stays visible collapsed. */
  active?: boolean;
  /**
   * `popover` floats the panel absolutely; `inline` renders it in flow. The
   * choice is dictated by scroll ancestors, not taste — an absolute panel
   * inside an `overflow-y: auto` rail is clipped at the rail's bottom edge, so
   * the image detail and Export call sites must be `inline`.
   */
  placement?: "popover" | "inline";
  align?: "left" | "right";
  panelWidth?: number;
}

/** `LabelCheckList` behind a disclosure button. */
export default function LabelPicker({
  triggerContent, triggerAriaLabel, active, placement = "popover",
  align = "left", panelWidth = 240, ...list
}: PickerProps) {
  const { open, setOpen, anchorRef, triggerRef } = usePopover<HTMLDivElement>();
  const panelId = useId();

  const panel = (
    <LabelCheckList {...list} autoFocus />
  );

  return (
    <div ref={anchorRef} style={{ position: "relative", display: placement === "inline" ? "block" : "inline-flex" }}>
      <button
        type="button"
        className="btn ghost sm"
        ref={triggerRef}
        aria-label={triggerAriaLabel}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((o) => !o)}
        style={{
          whiteSpace: "nowrap",
          border: active ? "1px solid var(--accent)" : undefined,
        }}
      >
        {triggerContent}
        <svg width="9" height="9" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <path d="M3 6l5 5 5-5" />
        </svg>
      </button>

      {open && (
        placement === "popover" ? (
          <div
            id={panelId}
            style={{
              position: "absolute", top: "calc(100% + 4px)", zIndex: 1000,
              ...(align === "right" ? { right: 0 } : { left: 0 }),
              background: "var(--surface-2)", border: "1px solid var(--line-2)",
              borderRadius: "var(--r)", boxShadow: "0 8px 24px rgba(0,0,0,.4)",
              padding: 8, width: panelWidth,
            }}
          >
            {panel}
          </div>
        ) : (
          <div
            id={panelId}
            style={{
              marginTop: 6, background: "var(--surface-2)",
              border: "1px solid var(--line-2)", borderRadius: "var(--r)", padding: 8,
            }}
          >
            {panel}
          </div>
        )
      )}
    </div>
  );
}
