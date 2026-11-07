import type { ReactNode, Ref } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

/** What BlueprintPanel hands each editor: whether its section is open, and whether it is
 *  editable. The section's element reaches the jump bar as a separate `ref` prop. */
export interface SectionControl {
  open: boolean;
  setOpen: (open: boolean) => void;
  /** A run is live on the plan: the server would refuse Apply, so editing is off. */
  readOnly: boolean;
}

/** The `ref` prop each editor takes and passes on (React 19: a plain prop). */
export type SectionRef = Ref<HTMLDivElement>;

interface Props {
  ref?: SectionRef;
  title: string;
  /** Counts shown beside the title, such as "3 axes · 11 entities". */
  summary: string;
  control: SectionControl;
  /** The section's *+ Add …* button. It stays visible when the section is collapsed. */
  action: ReactNode;
  children: ReactNode;
}

const bare = { border: 0, padding: 0, margin: 0, minWidth: 0 } as const;

/**
 * One collapsible Blueprint editor section.
 *
 * - A collapsed body is **hidden, never unmounted**: the variations' pieces box keeps
 *   typed text that can differ from what is saved (an over-limit line and its warning),
 *   and an unmount would lose it.
 * - The read-only `<fieldset>` wraps the action and the body, not the toggle, so a
 *   section still opens and closes while a run is live.
 */
export default function BlueprintSection({ ref, title, summary, control, action, children }: Props) {
  const { open, setOpen, readOnly } = control;
  return (
    <div ref={ref} className="panel bp-section">
      <div className="panel-h" style={open ? undefined : { borderBottom: 0 }}>
        <h3>
          <button type="button" className="bp-section-toggle" aria-expanded={open}
            onClick={() => setOpen(!open)}>
            {open ? <ChevronDown size={14} aria-hidden /> : <ChevronRight size={14} aria-hidden />}
            {title}
            <span className="bp-summary">{summary}</span>
          </button>
        </h3>
        <div style={{ flex: 1 }} />
        <fieldset disabled={readOnly} style={bare}>{action}</fieldset>
      </div>
      <fieldset disabled={readOnly} hidden={!open} className="panel-b"
        style={{ ...bare, padding: 18, display: open ? "flex" : "none", flexDirection: "column", gap: 14 }}>
        {children}
      </fieldset>
    </div>
  );
}
