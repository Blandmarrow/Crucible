import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";

import { labelsApi, type Label } from "../api/labels";
import { invalidateLabelScope } from "../constants/queryKeys";
import { apiErrorDetail } from "../utils/apiError";

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
 *
 * `isLoaded` is what separates "still fetching" from "the vocabulary is genuinely
 * empty", which `labels` alone collapses into `[]`. Every consumer that drops
 * unknown ids from a restored filter has to know the difference: reconciling
 * against a not-yet-arrived vocabulary clears every filter, and refusing to
 * reconcile until one label exists leaves a stale id filtering the grid to zero
 * after the last label is deleted.
 */
export function useLabels() {
  const { data, isLoading, isSuccess: isLoaded } = useQuery({
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

  return { labels, byId, byHotkey, isLoading, isLoaded };
}

/**
 * Add/remove labels on one image — the detail view's hotkeys and its labels
 * panel both write through this, and both must share it.
 *
 * Each toggle is a whole `{add}` or `{remove}` decided client-side, so two in
 * flight at once can land on the server in either order: a fast double-tap
 * sent remove + add on separate connections, the server applied add first, and
 * the label ended up removed while the screen said attached. The shared `scope`
 * makes TanStack queue every assign for this image behind the previous one, so
 * they reach the server in the order they were made.
 *
 * Settling refetches only once the *last* queued assign is done. Refetching
 * after each one would fetch a state the next queued assign is about to change,
 * and that response would overwrite the caller's optimistic flip of the next
 * one. That is also the rollback for a failed assign: the final refetch puts
 * back whatever the server actually holds.
 */
export function useAssignImageLabels(imageId: string | undefined) {
  const qc = useQueryClient();
  const mutationKey = ["label-assign", imageId];
  return useMutation({
    mutationKey,
    scope: { id: `label-assign:${imageId}` },
    mutationFn: (body: { add?: string[]; remove?: string[] }) =>
      labelsApi.assign({ image_ids: [imageId!], ...body }),
    onError: (err) => toast.error(apiErrorDetail(err, "Could not update labels")),
    onSettled: () => {
      // Counts this one too: it is still pending while its own onSettled runs.
      if (qc.isMutating({ mutationKey }) <= 1) invalidateLabelScope(qc);
    },
  });
}
