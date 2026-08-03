/** Droppable ids for the gallery subfolder sidebar (drag images onto a row to move
 *  them there). Image ids are UUIDs, so the prefix can never collide with a card id
 *  in the same DndContext. */
export const SUBFOLDER_DROP_PREFIX = "subfolder:";

export const subfolderDropId = (path: string) => SUBFOLDER_DROP_PREFIX + path; // root → "subfolder:"

export const isSubfolderDropId = (id: unknown): id is string =>
  typeof id === "string" && id.startsWith(SUBFOLDER_DROP_PREFIX);

export const subfolderFromDropId = (id: string) => id.slice(SUBFOLDER_DROP_PREFIX.length);

/** Sentinel droppable spanning the whole subfolder sidebar. Never a move target — it
 *  exists so a drop on sidebar chrome ("All", the header, the create form, the padding
 *  below the last row) can be swallowed instead of falling through to the closestCenter
 *  card fallback, which scores by the dragged card's rect and would silently reorder. */
export const SIDEBAR_DROP_ID = "subfolder-sidebar";

/** Draggable ids for the subfolder rows themselves (drag a folder onto another to
 *  re-nest it). A row carries two ids at once — `subfolder:{path}` as a droppable and
 *  `folder-drag:{path}` as a draggable — so this prefix has to clear **both**
 *  `SUBFOLDER_DROP_PREFIX` and `SIDEBAR_DROP_ID`, which are already only one character
 *  apart. Hence a different word rather than a longer `subfolder…` prefix. */
export const SUBFOLDER_DRAG_PREFIX = "folder-drag:";

export const subfolderDragId = (path: string) => SUBFOLDER_DRAG_PREFIX + path;

export const isSubfolderDragId = (id: unknown): id is string =>
  typeof id === "string" && id.startsWith(SUBFOLDER_DRAG_PREFIX);

export const subfolderFromDragId = (id: string) => id.slice(SUBFOLDER_DRAG_PREFIX.length);

/** May `src` be dropped on `dest`? False for itself and for any of its own
 *  descendants — re-pathing `a` to `a/b/a` would orphan the whole subtree. Shared by
 *  collision detection, the drag-end re-check and `MoveSubfolderModal`'s list. */
export const canDropFolderOn = (src: string, dest: string) =>
  dest !== src && !dest.startsWith(src + "/");

export const SORT_OPTIONS = [
  { label: "Newest first",       sort: "created_at",             order: "desc" },
  { label: "Oldest first",       sort: "created_at",             order: "asc"  },
  { label: "Aesthetic ↓",         sort: "aesthetic_score",        order: "desc" },
  { label: "Aesthetic ↑",         sort: "aesthetic_score",        order: "asc"  },
  { label: "Name A-Z",           sort: "filename",               order: "asc"  },
  { label: "Style similarity ↓",  sort: "style_similarity_score", order: "desc" },
  { label: "Colorfulness ↓",      sort: "color_score",            order: "desc" },
  { label: "Custom order",       sort: "sort_order",             order: "asc"  },
  // Append only, never insert: `sortIdx` is persisted as an *index* into this
  // array in `gallery-state-${datasetId}`, so a mid-array insert silently
  // changes the sort of every saved gallery.
  { label: "Brightness ↓",        sort: "luminance_score",        order: "desc" },
  { label: "Brightness ↑",        sort: "luminance_score",        order: "asc"  },
  // Frame lineage — the natural order for reviewing a triage extraction pass.
  // Ascending only: "the last frame of the video first" is not a thing anyone
  // reviews by, and each extra entry costs a persisted index forever. Images
  // that did not come from a video sort last (nulls-last, applied server-side).
  { label: "Video timeline",     sort: "source_timestamp_ms",    order: "asc"  },
  { label: "Shot order",         sort: "source_shot_index",      order: "asc"  },
] as const;

/** Sentinel value for the gallery's "missing license only" filter option. */
export const MISSING_LICENSE = "__missing__";
