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
