import type { QueryClient } from "@tanstack/react-query";

/**
 * Invalidate every dataset-scoped detection query in one call.
 *
 * The single sanctioned way to refresh detection-derived caches after a run,
 * manual edit, merge, refine, bulk-delete, or geometry-changing crop — mirror of
 * the backend "import the shared helper, never copy the logic" convention. Covers:
 *  - ["detection-labels", datasetId] — label chips (gallery filter, crop form, export)
 *  - ["detection-models", datasetId] — model chips (bulk-delete form)
 *  - ["detection-stats", datasetId]  — Statistics detection/mask panel; the live
 *    key carries a trailing subfolder segment, so this prefix-matches it (TanStack
 *    invalidateQueries matches by key prefix).
 *
 * Callers that also need per-image caches (e.g. ["image", imageId]) or the
 * bulk-count key invalidate those separately alongside this helper.
 */
export function invalidateDetectionQueries(qc: QueryClient, datasetId: string | undefined): void {
  qc.invalidateQueries({ queryKey: ["detection-labels", datasetId] });
  qc.invalidateQueries({ queryKey: ["detection-models", datasetId] });
  qc.invalidateQueries({ queryKey: ["detection-stats", datasetId] });
}
