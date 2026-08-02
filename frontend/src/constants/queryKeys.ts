import type { QueryClient } from "@tanstack/react-query";

/**
 * Every query whose data depends on effective source/license provenance.
 *
 * Provenance resolves at read time (image value over dataset default), so a
 * single write can change what several unrelated views show — and split view
 * means those views are often mounted at the same time. Editing a dataset's
 * default license changes the effective license of every non-overriding image,
 * which changes the gallery badges, the Stats license breakdown *and* the Export
 * page's preview counts.
 *
 * Any new provenance writer calls this instead of listing keys inline; a writer
 * that lists its own keys is how the Export preview came to sit on stale license
 * counts after a dataset rename.
 */
const PROVENANCE_SCOPE = [
  ["datasets"],
  ["dataset"],
  ["images"],
  ["image"],
  ["dataset-stats"],
  ["export-preview"],
  ["bucket-images"],
  // The free-text licenses the pickers offer (hooks/useCustomLicenses) are the
  // set of values in use, so a provenance write can add one — and the point of
  // typing a custom license is to filter/apply it immediately afterwards.
  ["licenses-in-use"],
] as const;

export function invalidateProvenanceScope(qc: QueryClient) {
  for (const key of PROVENANCE_SCOPE) {
    qc.invalidateQueries({ queryKey: key });
  }
}

/**
 * Every dataset-scoped query whose data is derived from the image rows.
 *
 * Adding *or* removing rows moves all of them together: the dataset card and
 * header counts, the subfolder list, and the four Stats queries are all counts
 * or aggregates over the same rows the gallery lists. Split view means several
 * are mounted at once, and the global `staleTime` is 30 s — so a writer that
 * invalidates only `["images"]` leaves visibly wrong numbers on screen until
 * something unrelated triggers a refetch.
 *
 * The copies of this list had already drifted apart before it was extracted
 * (`ImageDetailPage`'s delete was missing `["dataset", id]`). Following the
 * `invalidateDetectionQueries` precedent, the helper carries only the shared
 * set: a caller with extras (`videos`, `licenses-in-use`, `image`) invalidates
 * them alongside the call rather than passing an options bag.
 *
 * `datasetId` is allowed to be undefined because every call site interpolates a
 * possibly-undefined pane id — `["images", undefined]` matches nothing, which
 * is the existing behaviour.
 *
 * `style-distribution` belongs here for the same reason as the Stats aggregates:
 * a percentile is an aggregate over exactly these rows, so deleting or importing
 * images moves every card's meter. It costs nothing when nobody is looking, since
 * the query is `enabled`-gated on the gallery meter preference and an invalidation
 * of a key with no observer never refetches.
 */
const DATASET_CONTENT_SCOPE = [
  "images",
  "dataset",
  "subfolders",
  "dataset-stats",
  "tag-stats",
  "score-values",
  "tag-cooccurrence",
  "style-distribution",
] as const;

export function invalidateDatasetContentScope(qc: QueryClient, datasetId: string | undefined) {
  qc.invalidateQueries({ queryKey: ["datasets"] });
  for (const k of DATASET_CONTENT_SCOPE) {
    qc.invalidateQueries({ queryKey: [k, datasetId] });
  }
}
