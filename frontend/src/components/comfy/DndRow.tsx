import { useId, type CSSProperties, type HTMLAttributes, type ReactNode } from "react";
import { useDraggable, useDroppable } from "@dnd-kit/core";
import type { DragData, DropData } from "./dnd";

/** Spread onto the element that starts a drag: a grip or a label button, never a whole
 *  row holding inputs (as the gallery's folder rows put it on the label). */
export interface DragHandle {
  attach: (el: HTMLElement | null) => void;
  props: Record<string, unknown>;
}

interface Props extends Omit<HTMLAttributes<HTMLDivElement>, "children"> {
  drop?: { id: string; data: DropData } | null;
  drag?: { id: string; data: DragData } | null;
  /** Another ref the row needs, e.g. the virtualizer's `measureElement`. */
  nodeRef?: (el: HTMLDivElement | null) => void;
  style?: CSSProperties;
  children: (api: { handle: DragHandle | null; isOver: boolean; isDragging: boolean }) => ReactNode;
}

/** One row of the Rows tab that is a drop target, a drag source, or both (M6 §8c). Both
 *  dnd-kit hooks always run (hooks cannot be conditional) and are disabled when unused. */
export default function DndRow({ drop, drag, nodeRef, style, children, ...rest }: Props) {
  const uid = useId();
  const droppable = useDroppable({ id: drop?.id ?? `nodrop:${uid}`, data: drop?.data, disabled: !drop });
  const draggable = useDraggable({ id: drag?.id ?? `nodrag:${uid}`, data: drag?.data, disabled: !drag });
  const handle: DragHandle | null = drag
    ? {
        attach: draggable.setActivatorNodeRef,
        props: { ...draggable.attributes, ...draggable.listeners },
      }
    : null;
  return (
    <div
      ref={(el) => {
        droppable.setNodeRef(el);
        draggable.setNodeRef(el);
        nodeRef?.(el);
      }}
      style={{ ...style, opacity: draggable.isDragging ? 0.45 : style?.opacity }}
      {...rest}
    >
      {children({ handle, isOver: !!drop && droppable.isOver, isDragging: draggable.isDragging })}
    </div>
  );
}
