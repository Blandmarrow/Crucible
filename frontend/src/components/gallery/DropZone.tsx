import type { ReactNode } from "react";
import { useDroppable } from "@dnd-kit/core";

interface Props {
  id: string;
  children: (p: { setNodeRef: (el: HTMLElement | null) => void; isOver: boolean }) => ReactNode;
}

/** Render-prop wrapper around `useDroppable`. Exists because `useDroppable` only
 *  registers when it runs *inside* the `DndContext`, which `GalleryPage` renders in its
 *  own JSX — so a hook at the top of `GalleryPage` would silently never register, and
 *  the subfolder rows are built inside `renderSubfolderNode`'s closure where a hook
 *  can't go at all. */
export default function DropZone({ id, children }: Props) {
  const { setNodeRef, isOver } = useDroppable({ id });
  return <>{children({ setNodeRef, isOver })}</>;
}
