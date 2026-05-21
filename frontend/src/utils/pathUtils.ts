export function parentOf(path: string): string | null {
  const p = path.replace(/[\\/]+$/, "");
  const lastSep = Math.max(p.lastIndexOf("/"), p.lastIndexOf("\\"));
  if (lastSep <= 0) return null;
  const parent = p.slice(0, lastSep);
  if (/^[A-Za-z]:$/.test(parent)) return null;
  return parent || null;
}

export function breadcrumbsFromPath(path: string): { label: string; path: string }[] {
  const parts = path.replace(/[\\/]+$/, "").split(/[\\/]/).filter(Boolean);
  const crumbs: { label: string; path: string }[] = [];
  let built = "";
  for (const part of parts) {
    built = built ? `${built}\\${part}` : part;
    crumbs.push({ label: part, path: built });
  }
  return crumbs;
}
