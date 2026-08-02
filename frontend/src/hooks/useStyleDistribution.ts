import { useQuery } from "@tanstack/react-query";

import { qualityApi, type StyleDistribution } from "../api/quality";

/**
 * One dataset's style-score distribution and run descriptor.
 *
 * Every scored gallery card calls this — TanStack dedupes the whole page down to
 * one request per dataset, and the card already subscribes to a Zustand store per
 * instance, so per-card subscription is the existing shape rather than a new cost.
 *
 * `enabled` takes the caller's own gate (the gallery meter preference) so a user
 * with the meter switched off never fires the request at all, rather than fetching
 * a payload nothing renders.
 *
 * The key `["style-distribution", datasetId]` is invalidated by every screen that
 * finishes a style run — the Score page's Style similarity panel and the gallery
 * `SelectionToolbar`'s.
 */
export function useStyleDistribution(
  datasetId: string | undefined | null,
  enabled = true,
): StyleDistribution | undefined {
  const { data } = useQuery({
    queryKey: ["style-distribution", datasetId],
    queryFn: () => qualityApi.styleDistribution(datasetId!),
    enabled: enabled && !!datasetId,
    staleTime: 60_000,
  });
  return data;
}
