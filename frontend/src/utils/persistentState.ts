/** Load a JSON blob from localStorage, shallow-merged onto `defaults`. Returns a fresh copy of
 *  `defaults` if the key is absent or parsing fails. Shallow merge gives forward-compat: new
 *  fields added to `defaults` later just appear for users with an older stored blob. */
export function loadPersisted<T extends object>(key: string, defaults: T): T {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return { ...defaults };
    const parsed = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return { ...defaults };
    return { ...defaults, ...parsed };
  } catch {
    return { ...defaults };
  }
}

/** Serialize and store a JSON blob. Swallows quota/serialization errors (best-effort). */
export function savePersisted<T extends object>(key: string, value: T): void {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* ignore */ }
}

/** Remove a persisted blob entirely (used by "Reset to defaults"). */
export function clearPersisted(key: string): void {
  try { localStorage.removeItem(key); } catch { /* ignore */ }
}

/** Build the per-dataset key for a "filters" blob. */
export function datasetScopedKey(prefix: string, datasetId: string): string {
  return `${prefix}-${datasetId}`;
}
