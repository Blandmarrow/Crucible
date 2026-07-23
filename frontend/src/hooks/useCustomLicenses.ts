import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { datasetsApi } from "../api/datasets";
import { LICENSE_OPTIONS, OTHER_PREFIX } from "../constants/licenses";

const KNOWN_IDS = new Set(LICENSE_OPTIONS.map((l) => l.id));

/**
 * The free-text (`other:`) licenses actually recorded in one dataset, most-used
 * first — the options a license picker must add to the compiled-in vocabulary.
 *
 * The vocabulary ships in the bundle, but a free-text license only exists in the
 * data, so a dropdown can offer one only by asking. Every dataset-scoped license
 * control reads this: the gallery filter, the export filter, the image detail
 * editor, the bulk Set source/license modal and the dataset Edit modal.
 *
 * The endpoint returns *every* distinct effective license including vocabulary
 * ids and "" (no license recorded); both are filtered out here because each
 * caller already renders those itself.
 *
 * The query key is in `PROVENANCE_SCOPE` (constants/queryKeys.ts), so typing a
 * new free-text license makes it selectable everywhere without a reload.
 */
export function useCustomLicenses(datasetId: string | undefined | null): string[] {
  const { data } = useQuery({
    queryKey: ["licenses-in-use", datasetId],
    queryFn: () => datasetsApi.licensesInUse(datasetId!),
    enabled: !!datasetId,
    staleTime: 60_000,
  });

  return useMemo(
    () =>
      (data ?? [])
        .map((row) => row.license)
        .filter((lic) => lic.toLowerCase().startsWith(OTHER_PREFIX) && !KNOWN_IDS.has(lic)),
    [data],
  );
}
