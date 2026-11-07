import type { ComfyRow } from "../../api/comfy";
import { withMissingAncestors } from "../../utils/subfolderTree";

// The Rows tab's folder helpers (M6 §8b), shared by ComfyFolderTree, ComfyRowsTable and
// ComfyPage. Kept out of the component files so Fast Refresh keeps working.

/** null = every row; "" = rows with no folder; a path = that folder and everything in it. */
export type FolderFilter = string | null;

export const BLUEPRINT_FOLDER_REASON =
  "This folder comes from the blueprint. Rename it in the Blueprint tab.";

/** Does `row` fall under the tree selection `filter`? A folder selects its whole subtree. */
export function inFolder(row: ComfyRow, filter: FolderFilter): boolean {
  if (filter === null) return true;
  if (filter === "") return row.subfolder === "";
  return row.subfolder === filter || row.subfolder.startsWith(filter + "/");
}

/** Every folder of a plan: the rows' folders plus the declared ones, as tree input.
 *  `image_count` carries the rows filed *directly* in each folder. */
export function planFolderItems(rows: ComfyRow[], declared: string[]) {
  const counts = new Map<string, number>();
  for (const r of rows) if (r.subfolder) counts.set(r.subfolder, (counts.get(r.subfolder) ?? 0) + 1);
  for (const d of declared) if (d && !counts.has(d)) counts.set(d, 0);
  return withMissingAncestors([...counts].map(([path, image_count]) => ({ path, image_count })));
}

/** Folders whose subtree holds rows the blueprint still counts: their tree menu is locked. */
export function lockedFolderTest(rows: ComfyRow[]) {
  const linked = new Set(rows.filter((r) => r.blueprint_linked && r.subfolder).map((r) => r.subfolder));
  return (path: string) => {
    for (const p of linked) if (p === path || p.startsWith(path + "/")) return true;
    return false;
  };
}

/** Tree order: (root) first, then each path segment by segment, as the folder tree. */
export function compareFolders(a: string, b: string): number {
  if (a === b) return 0;
  if (a === "") return -1;
  if (b === "") return 1;
  const pa = a.split("/"), pb = b.split("/");
  for (let i = 0; i < Math.min(pa.length, pb.length); i++) {
    const c = pa[i].localeCompare(pb[i]);
    if (c !== 0) return c;
  }
  return pa.length - pb.length;
}
