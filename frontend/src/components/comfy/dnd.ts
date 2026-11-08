import type { CSSProperties } from "react";

// The Rows tab's drag and drop (M6 §8c), as data: what each draggable and droppable
// carries, and the one drop matrix every drop goes through. Pure, so the rules read in one
// place; ComfyPage turns an action into the same mutation its button route calls
// (docs/dev/comfyui/library-dnd.md § Drag and drop).

/** What a draggable carries (`useDraggable({ data })`). A row drag names one row; the page
 *  widens it to the whole selection when that row is selected, as the gallery does. */
export type DragData =
  | { kind: "row"; rowId: string }
  | { kind: "lib"; ids: string[] }
  | { kind: "xrows"; planId: string; datasetId: string; ids: string[] }
  | { kind: "folder"; path: string }
  | { kind: "category"; path: string };

/** What a droppable carries. `folder` is a tree node, (root) as "", or a group header;
 *  `table` is the table body, meaning the folder in view; `category` "" is the library's
 *  top level. */
export type DropData =
  | { kind: "folder"; path: string }
  | { kind: "plan"; planId: string; name: string }
  | { kind: "category"; path: string }
  | { kind: "table" }
  | { kind: "row"; rowId: string; folder: string };

export type DropAction =
  | { type: "set-folder"; ids: string[]; folder: string }
  | { type: "place"; ids: string[]; anchor: string; position: "before" | "after"; folder: string }
  | { type: "copy-rows"; ids: string[]; planId: string; planName: string; move: boolean }
  | { type: "save-to-library"; ids: string[]; category: string }
  | { type: "add-lib"; ids: string[]; folder: string }
  | { type: "move-lib"; ids: string[]; category: string }
  | { type: "copy-xrows"; planId: string; datasetId: string; ids: string[]; folder: string | null }
  | { type: "move-folder"; path: string; newPath: string }
  | { type: "move-category"; path: string; newPath: string };

export interface DropContext {
  /** The ids a row drag moves: the selection when the dragged row is in it. */
  rowIds: string[];
  currentPlanId: string;
  folderInView: string;
  shift: boolean;
  /** Over a row: which half the dragged item's centre is in. */
  position: "before" | "after";
  /** Each row's folder, to refuse a drop that would change nothing. */
  rowFolders: Map<string, string>;
  /** Each library prompt's category, for the same check. */
  promptCategories: Map<string, string>;
}

/** A folder or category may land on itself or its own subtree: never. (The gallery's
 *  `canDropFolderOn`, for both trees.) */
function canNest(src: string, dest: string): boolean {
  return dest !== src && !dest.startsWith(src + "/");
}

/** The path `src` gets when dropped into `destParent` ("" = the top level), or null when
 *  the drop would change nothing or nest it inside itself. */
function renest(src: string, destParent: string): string | null {
  if (!canNest(src, destParent)) return null;
  const leaf = src.split("/").pop()!;
  const next = destParent ? `${destParent}/${leaf}` : leaf;
  return next === src ? null : next;
}

/** The drop matrix (docs/dev/comfyui/library-dnd.md § Drag and drop). null = not a drop
 *  target for this drag, which the collision filter also uses so an invalid target never
 *  lights up. */
export function resolveDrop(drag: DragData, drop: DropData, ctx: DropContext): DropAction | null {
  switch (drag.kind) {
    case "row": {
      const ids = ctx.rowIds;
      if (drop.kind === "folder") {
        // Every row already there: nothing to do, so not a drop target.
        if (ids.length > 0 && ids.every((id) => ctx.rowFolders.get(id) === drop.path)) return null;
        return { type: "set-folder", ids, folder: drop.path };
      }
      if (drop.kind === "row") {
        if (ids.includes(drop.rowId)) return null;
        return { type: "place", ids, anchor: drop.rowId, position: ctx.position, folder: drop.folder };
      }
      if (drop.kind === "plan") {
        if (drop.planId === ctx.currentPlanId) return null;
        return { type: "copy-rows", ids, planId: drop.planId, planName: drop.name, move: ctx.shift };
      }
      if (drop.kind === "category" && drop.path) return { type: "save-to-library", ids, category: drop.path };
      return null;
    }
    case "lib": {
      if (drop.kind === "table") return { type: "add-lib", ids: drag.ids, folder: ctx.folderInView };
      if (drop.kind === "folder") return { type: "add-lib", ids: drag.ids, folder: drop.path };
      if (drop.kind === "row") return { type: "add-lib", ids: drag.ids, folder: drop.folder };
      if (drop.kind === "category" && drop.path) {
        if (drag.ids.length > 0 && drag.ids.every((id) => ctx.promptCategories.get(id) === drop.path)) return null;
        return { type: "move-lib", ids: drag.ids, category: drop.path };
      }
      return null;
    }
    case "xrows": {
      if (drag.planId === ctx.currentPlanId) return null;
      const base = { type: "copy-xrows" as const, planId: drag.planId, datasetId: drag.datasetId, ids: drag.ids };
      // On the table each row keeps its own folder; on a folder they all go there.
      if (drop.kind === "table") return { ...base, folder: null };
      if (drop.kind === "folder") return { ...base, folder: drop.path };
      if (drop.kind === "row") return { ...base, folder: drop.folder };
      return null;
    }
    case "folder": {
      if (drop.kind !== "folder") return null;
      const newPath = renest(drag.path, drop.path);
      return newPath ? { type: "move-folder", path: drag.path, newPath } : null;
    }
    case "category": {
      if (drop.kind !== "category") return null;
      const newPath = renest(drag.path, drop.path);
      return newPath ? { type: "move-category", path: drag.path, newPath } : null;
    }
  }
}

/** What the drag overlay's chip says the drop will do. */
export function describeDrop(a: DropAction): string {
  const n = (k: number, w: string) => `${k} ${w}${k !== 1 ? "s" : ""}`;
  const where = (f: string) => f || "(root)";
  switch (a.type) {
    case "set-folder": return `Move ${n(a.ids.length, "row")} to ${where(a.folder)}`;
    case "place": return `Place ${n(a.ids.length, "row")} ${a.position} this row`;
    case "copy-rows": return `${a.move ? "Move" : "Copy"} ${n(a.ids.length, "row")} to ${a.planName}${a.move ? "" : " · Shift moves"}`;
    case "save-to-library": return `Save ${n(a.ids.length, "prompt")} to ${a.category}`;
    case "add-lib": return `Add ${n(a.ids.length, "prompt")} to ${where(a.folder)}`;
    case "move-lib": return `File ${n(a.ids.length, "prompt")} under ${a.category}`;
    case "copy-xrows": return `Copy ${n(a.ids.length, "row")} ${a.folder === null ? "keeping their folders" : `to ${where(a.folder)}`}`;
    case "move-folder": return `Move folder to ${a.newPath}`;
    case "move-category": return `Move category to ${a.newPath}`;
  }
}

// Droppable ids, namespaced so no two kinds collide (row and prompt ids are UUIDs).
export const dropId = {
  folder: (path: string) => `cfolder:${path}`,
  group: (path: string) => `cgroup:${path}`,
  plan: (id: string) => `cplan:${id}`,
  category: (path: string) => `ccat:${path}`,
  row: (id: string) => `crow:${id}`,
  table: "ctable",
};
export const dragId = {
  row: (id: string) => `drow:${id}`,
  lib: (id: string) => `dlib:${id}`,
  xrow: (id: string) => `dxrow:${id}`,
  folder: (path: string) => `dfolder:${path}`,
  category: (path: string) => `dcat:${path}`,
};

/** The drop-target ring: an inset accent outline, never the selected row's colour. */
export const DROP_RING: CSSProperties = {
  boxShadow: "inset 0 0 0 2px var(--accent)",
  background: "var(--accent-glow)",
};

/** Fired on `window` after a dragged category is re-filed, `detail: {from, to}`, so the
 *  library panel re-points its stored folds as its own Rename and Move do. */
export const LIBRARY_REPATH_EVENT = "comfy-library-repath";
