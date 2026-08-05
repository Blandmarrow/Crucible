import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { labelsApi, type Label } from "../api/labels";

/**
 * The global label vocabulary, plus the two lookups every consumer wants.
 *
 * The vocabulary is app-wide rather than per-dataset (so a copy or move across
 * datasets needs no name remapping, and "fx" means one thing everywhere), which
 * is why the query key is a bare `["labels"]` with no dataset in it. It changes
 * only when someone edits it in Settings, hence the long `staleTime`; writers
 * call `invalidateLabelScope` rather than relying on it expiring.
 *
 * `byHotkey` is keyed on the lowercase single character the backend stores, so
 * the detail-view handler can look up `e.key.toLowerCase()` directly.
 */
export function useLabels() {
  const { data, isLoading } = useQuery({
    queryKey: ["labels"],
    queryFn: labelsApi.list,
    staleTime: 5 * 60_000,
  });

  const labels = useMemo(() => data ?? [], [data]);

  const byId = useMemo(() => {
    const map = new Map<string, Label>();
    for (const l of labels) map.set(l.id, l);
    return map;
  }, [labels]);

  const byHotkey = useMemo(() => {
    const map = new Map<string, Label>();
    for (const l of labels) if (l.hotkey) map.set(l.hotkey, l);
    return map;
  }, [labels]);

  return { labels, byId, byHotkey, isLoading };
}
