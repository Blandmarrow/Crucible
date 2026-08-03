import { useCallback, type ReactNode } from "react";
import { useDraggable, useDroppable } from "@dnd-kit/core";

// `SyntheticListenerMap` is not re-exported from the package root, so the listener
// and attribute types are derived from the hook rather than deep-imported.
type DraggableBits = ReturnType<typeof useDraggable>;

interface Props {
  /** `subfolder:{path}` — the row as a drop target for image cards and other folders. */
  dropId: string;
  /** `folder-drag:{path}` — the row as a draggable folder. */
  dragId: string;
  children: (p: {
    setNodeRef: (el: HTMLElement | null) => void;
    setActivatorNodeRef: (el: HTMLElement | null) => void;
    listeners: DraggableBits["listeners"];
    attributes: DraggableBits["attributes"];
    isOver: boolean;
    isDragging: boolean;
  }) => ReactNode;
}

/**
 * Render-prop wrapper making one subfolder row *both* a droppable and a draggable.
 *
 * It exists for the two reasons `DropZone`'s docstring gives — `useDroppable` /
 * `useDraggable` only register when they run inside the `DndContext`, which
 * `GalleryPage` renders in its own JSX, and the rows are built inside
 * `renderSubfolderNode`'s closure where a hook cannot go at all.
 *
 * `setNodeRef` merges the two hooks' refs (the same thing `useSortable` does
 * internally). Put it on the **row**, so dnd-kit measures the whole row and the
 * droppable rect stays put — and put `setActivatorNodeRef` + `listeners` on the label
 * button inside it, never on the row: the hover action buttons are siblings of the
 * activator, and `PointerSensor.activators` has no interactive-element filter, so
 * row-level listeners would turn a press-and-slide on `×` into a drag.
 *
 * Both ids are required. The `(root)` row and the sidebar sentinel keep plain
 * `DropZone`, so there is no disabled-draggable branch here to get wrong.
 */
export default function SubfolderRowDnd({ dropId, dragId, children }: Props) {
  const { setNodeRef: setDropRef, isOver } = useDroppable({ id: dropId });
  const {
    setNodeRef: setDragRef, setActivatorNodeRef, listeners, attributes, isDragging,
  } = useDraggable({ id: dragId });

  const setNodeRef = useCallback((el: HTMLElement | null) => {
    setDropRef(el);
    setDragRef(el);
  }, [setDropRef, setDragRef]);

  return <>{children({ setNodeRef, setActivatorNodeRef, listeners, attributes, isOver, isDragging })}</>;
}
