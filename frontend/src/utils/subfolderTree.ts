import type { SubfolderInfo } from "../types";

export interface SubfolderNode extends SubfolderInfo {
  label: string;
  depth: number;
  children: SubfolderNode[];
  totalCount: number;
}

/** A typed folder path spelled as the server stores it (`utils._subfolder_parts`):
 *  backslashes folded, each segment trimmed, blank and `.` segments dropped. For
 *  comparing a typed path against server values; the server still validates it. */
export function normalizeSubfolder(raw: string): string {
  return raw
    .replace(/\\/g, "/")
    .split("/")
    .map((p) => p.trim())
    .filter((p) => p && p !== ".")
    .join("/");
}

export function buildSubfolderTree(items: SubfolderInfo[]): SubfolderNode[] {
  const map = new Map<string, SubfolderNode>();
  for (const item of items) {
    if (!item.path) continue;
    map.set(item.path, {
      ...item,
      label: item.path.split("/").pop()!,
      depth: item.path.split("/").length - 1,
      children: [],
      totalCount: item.image_count,
    });
  }
  const roots: SubfolderNode[] = [];
  for (const node of map.values()) {
    const parts = node.path.split("/");
    if (parts.length === 1) {
      roots.push(node);
    } else {
      const parent = map.get(parts.slice(0, -1).join("/"));
      if (parent) parent.children.push(node); else roots.push(node);
    }
  }
  function computeTotals(n: SubfolderNode): void {
    n.children.sort((a, b) => a.label.localeCompare(b.label));
    for (const c of n.children) computeTotals(c);
    n.totalCount = n.image_count + n.children.reduce((s, c) => s + c.totalCount, 0);
  }
  roots.sort((a, b) => a.label.localeCompare(b.label));
  roots.forEach(computeTotals);
  return roots;
}

/** Every proper ancestor of a subfolder path: "a/b/c" → ["a", "a/b"]. The set of
 *  `expandedPaths` keys that have to be open for that path's row to be visible. */
export function ancestorPaths(path: string): string[] {
  const parts = path.split("/");
  return parts.slice(0, -1).map((_, i) => parts.slice(0, i + 1).join("/"));
}

/** Adds a 0-count row for any parent `list_subfolders` did not return: it lists only
 *  folders holding images directly or declared by hand, so `a` has no row of its own
 *  when its images all sit in `a/b`. Without this, that parent cannot be ticked.
 *
 *  The Export page's alone, on purpose. The gallery sidebar hangs rename and delete
 *  menus off every row, which a made-up parent should not get. */
export function withMissingAncestors(items: SubfolderInfo[]): SubfolderInfo[] {
  const known = new Set(items.map((item) => item.path));
  const missing: SubfolderInfo[] = [];
  for (const { path } of items) {
    for (const ancestor of ancestorPaths(path)) {
      if (known.has(ancestor)) continue;
      known.add(ancestor);
      missing.push({ path: ancestor, image_count: 0 });
    }
  }
  return [...items, ...missing];
}

/** The paths a tick on this node stands for: its whole subtree, minus a parent's own
 *  path when it holds no images directly. Counting that path would leave a parent
 *  part-ticked with every child ticked, over a folder that has nothing to export. An
 *  empty leaf still stands for itself, or its row could never be ticked at all. */
export function subtreeTargets(node: SubfolderNode): string[] {
  const own = node.image_count > 0 || node.children.length === 0 ? [node.path] : [];
  return [...own, ...node.children.flatMap(subtreeTargets)];
}

export type TickState = "all" | "some" | "none";

export function tickState(node: SubfolderNode, selected: ReadonlySet<string>): TickState {
  const targets = subtreeTargets(node);
  const ticked = targets.filter((path) => selected.has(path)).length;
  if (ticked === 0) return "none";
  return ticked === targets.length ? "all" : "some";
}
