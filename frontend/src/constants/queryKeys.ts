import type { QueryClient } from "@tanstack/react-query";
import type { ImageFilterParams } from "../api/images";

/**
 * "How many images match these filters" — one cache entry, shared by the gallery's
 * pagination total and the detail view's live total.
 *
 * Two properties make the sharing work, and both are load-bearing:
 *
 * - It stays **nested under the `["images", datasetId]` prefix** every image writer
 *   already invalidates (TanStack matches by prefix), including TopBar's per-row
 *   live invalidation while a `comfy_generate` run fills the dataset. That is what
 *   makes the detail view's total climb without watching a single job. A sibling
 *   key like `["images-count", …]` would go stale behind the user's back.
 * - The filters go in as **one plain object**, not a hand-spread list of values.
 *   `hashKey` sorts a plain object's keys and drops `undefined` props, so the
 *   gallery's live `ImageFilterParams` memo and the JSON-round-tripped copy in
 *   `utils/galleryNav.ts` (where the `undefined` keys are simply gone) hash
 *   identically — the same property the boundary prefetches already rely on.
 *
 * No collision with the list key: that one's third element is the page *number*,
 * and the only `setQueryData` on it addresses the full list key.
 */
export function imagesCountKey(datasetId: string | undefined, filters: ImageFilterParams) {
  return ["images", datasetId, "count", filters];
}

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
  // Reset, not invalidate: an invalidated entry is still *served* while it refetches,
  // and this one is a navigation target — the detail view's → would step onto the id
  // it holds before the refetch lands. `resetQueries` blanks `data` through a dispatch
  // the observers see, then refetches the active one. `["gallery-nav", undefined]`
  // matches nothing, the same no-op as the loop above.
  qc.resetQueries({ queryKey: ["gallery-nav", datasetId] });
}

/**
 * Every query whose data depends on which labels are attached to which images.
 *
 * There are four writers — the Settings vocabulary panel, the detail-page panel,
 * the detail-view hotkey and the gallery's bulk toolbar — which is exactly the
 * drift this file exists to prevent. A label write moves the vocabulary itself
 * (`["labels"]`, including every `usage_count`), the grid rows that carry the
 * dots (`["images", datasetId]`), the open detail pane (`["image"]`), the chip
 * badges (`["label-counts", datasetId]`) and the Export preview, whose count
 * narrows with the label filter.
 *
 * Every key is invalidated at its **bare prefix**, with no dataset segment —
 * matching `invalidateProvenanceScope` above and for the same reason. The
 * vocabulary is app-wide, `useSelectionStore` spans datasets, and the bulk assign
 * this helper mostly serves explicitly labels a selection that can straddle
 * several: scoping to one pane's `datasetId` left the other datasets' grids and
 * chip badges showing counts for assignments that had already happened.
 */
export function invalidateLabelScope(qc: QueryClient) {
  qc.invalidateQueries({ queryKey: ["labels"] });
  qc.invalidateQueries({ queryKey: ["image"] });
  qc.invalidateQueries({ queryKey: ["export-preview"] });
  qc.invalidateQueries({ queryKey: ["images"] });
  qc.invalidateQueries({ queryKey: ["label-counts"] });
}
