import type { QueryClient, QueryKey } from "@tanstack/react-query";

/** How often a running job may refetch the open gallery (M9 stage 11). */
export const LIVE_REFETCH_INTERVAL_MS = 2000;

/**
 * Coalesces query invalidations per group (a dataset id): the first call in a
 * quiet group goes out at once, and every call after it inside `intervalMs` is
 * folded into one trailing invalidation.
 *
 * Built for `TopBar`'s live job branch, which used to invalidate the gallery on
 * every advance of a job's `done` count, so a caption job over N images
 * refetched the open page up to N times, payload and all. The keys are kept per
 * group rather than per call, so two jobs on one dataset never drop each
 * other's extra keys (a ComfyUI run's `["subfolders", id]`).
 *
 * `takePending` is for the terminal branch: it cancels the trailing call and
 * hands back what it would have invalidated, so a job's final refetch is not
 * followed by a stale one.
 */
export function createThrottledInvalidator(qc: QueryClient, intervalMs = LIVE_REFETCH_INTERVAL_MS) {
  type Group = { last: number; timer: ReturnType<typeof setTimeout> | null; pending: Map<string, QueryKey> };
  const groups = new Map<string, Group>();

  const flush = (g: Group) => {
    g.timer = null;
    g.last = Date.now();
    const keys = [...g.pending.values()];
    g.pending.clear();
    for (const queryKey of keys) qc.invalidateQueries({ queryKey });
  };

  return {
    invalidate(group: string, keys: QueryKey[]) {
      let g = groups.get(group);
      if (!g) {
        g = { last: -Infinity, timer: null, pending: new Map() };
        groups.set(group, g);
      }
      for (const k of keys) g.pending.set(JSON.stringify(k), k);
      if (g.timer) return;
      const wait = g.last + intervalMs - Date.now();
      if (wait <= 0) {
        flush(g);
      } else {
        const target = g;
        g.timer = setTimeout(() => flush(target), wait);
      }
    },
    takePending(group: string): QueryKey[] {
      const g = groups.get(group);
      if (!g) return [];
      if (g.timer) clearTimeout(g.timer);
      groups.delete(group);
      return [...g.pending.values()];
    },
    cancelAll() {
      for (const g of groups.values()) if (g.timer) clearTimeout(g.timer);
      groups.clear();
    },
  };
}
