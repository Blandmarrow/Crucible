import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { settingsApi, type Thresholds } from "../api/settings";
import { ratingLabel } from "../constants/rating";

/** One rebindable action: a rating tier, or `null` for "clear the rating". */
export interface RatingKeyAction {
  field: keyof Thresholds &
    ("rating_key_keep" | "rating_key_probably" | "rating_key_probably_not" | "rating_key_cut" | "rating_key_clear");
  rating: number | null;
  label: string;
}

/** Best-first, then clear — the order Settings lists them and the hint reads.
 *  One per entry in `RATING_OPTIONS`, plus clear; a tier added there needs a
 *  column on `threshold_settings` and an entry here, or nobody can press it. */
export const RATING_KEY_ACTIONS: RatingKeyAction[] = [
  { field: "rating_key_keep", rating: 4, label: ratingLabel(4) },
  { field: "rating_key_probably", rating: 3, label: ratingLabel(3) },
  { field: "rating_key_probably_not", rating: 2, label: ratingLabel(2) },
  { field: "rating_key_cut", rating: 1, label: ratingLabel(1) },
  { field: "rating_key_clear", rating: null, label: "Clear rating" },
];

/**
 * The keep/cut rating keys, read from the shared `["settings","thresholds"]`
 * query that Settings writes. `byKey` maps a pressed key (lower-cased) to the
 * rating it sets, `null` meaning clear; an unbound action is simply absent.
 *
 * Empty until the settings have loaded, never a fallback to the default
 * digits: once someone has rebound a key, a default guessed at mount would
 * write the wrong rating for the press that lands first.
 */
export function useRatingKeys() {
  const { data } = useQuery({
    queryKey: ["settings", "thresholds"],
    queryFn: settingsApi.getThresholds,
    staleTime: 60_000,
  });
  return useMemo(() => {
    const byKey = new Map<string, number | null>();
    const keyFor = new Map<number | null, string>();
    for (const a of RATING_KEY_ACTIONS) {
      const key = data?.[a.field];
      if (!key) continue;
      byKey.set(key, a.rating);
      keyFor.set(a.rating, key);
    }
    // "4 Keep · 3 Probably · … · 0 clears" — whatever is bound, in that order.
    const hint = RATING_KEY_ACTIONS.filter((a) => keyFor.has(a.rating))
      .map((a) => `${keyFor.get(a.rating)} ${a.rating === null ? "clears" : a.label}`)
      .join(" · ");
    return { byKey, keyFor, hint, loaded: !!data };
  }, [data]);
}
