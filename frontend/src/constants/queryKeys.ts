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
] as const;

export function invalidateProvenanceScope(qc: QueryClient) {
  for (const key of PROVENANCE_SCOPE) {
    qc.invalidateQueries({ queryKey: key });
  }
}
