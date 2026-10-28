import type { CSSProperties, ReactNode } from "react";
import { useModalBehavior } from "../../hooks/useModalBehavior";

/**
 * An overlay + panel pair carrying `useModalBehavior`, for a dialog written inline
 * in a parent that stays mounted.
 *
 * The hook has to mount with the dialog. Calling it in a long-lived parent
 * (`SelectionToolbar`, `DatasetsPage`) traps focus for as long as the parent
 * lives, which is why those overlays stayed bare. Wrapping the JSX in this
 * component mounts the hook with the dialog, without moving the dialog's
 * state out of the parent. See `docs/dev/styling.md` § Modal behavior.
 *
 * It adds no visuals of its own: the overlay and panel classes are passed
 * through unchanged, so each call site keeps its z-index tier. `.dialog-bg` is
 * z-index 60, and the toolbar's Tailwind overlays are `z-50`, the same tier as
 * `ConfirmDialog`. The defaults are the `.dialog-bg`/`.dialog` pair
 * `DatasetsPage` uses.
 */
interface Props {
  /** Accessible name. Normally the visible heading's text. */
  label: string;
  /** Escape, and a backdrop click when `closeOnBackdrop` is set. */
  onClose: () => void;
  /** Only where a backdrop click already closed the dialog; never on a destructive confirm. */
  closeOnBackdrop?: boolean;
  overlayClassName?: string;
  className?: string;
  style?: CSSProperties;
  children: ReactNode;
}

export default function ModalShell({
  label,
  onClose,
  closeOnBackdrop = false,
  overlayClassName = "dialog-bg",
  className = "dialog",
  style,
  children,
}: Props) {
  const { overlayProps, panelProps } = useModalBehavior({ onClose, label, closeOnBackdrop });
  return (
    <div className={overlayClassName} {...overlayProps}>
      <div className={className} style={style} {...panelProps}>
        {children}
      </div>
    </div>
  );
}
