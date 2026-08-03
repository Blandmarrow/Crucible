import client from "./client";

/** How often the same image got the same rating twice.
 *
 *  **Counts, never a bare rate** — every `*_rate` is nullable and arrives beside
 *  the `pairs`/`agreements` it came from, because the day-one figure is a
 *  diagnostic and not a measurement. Three biases qualify it, each pushing a
 *  known way: *selection* (you re-rate what you disagree with) pushes it **down**;
 *  *anchoring* (the second look sees the previous answer) pushes it **up**;
 *  *bulk sweep* (select-all then press 1 writes events for images nobody looked
 *  at) pushes it **up** hardest, and `singleton_*` is what isolates it. */
export interface SelfAgreement {
  /** Images with two or more events — the whole universe of this statistic. */
  images_with_repeats: number;
  /** **Consecutive** event pairs, not first-versus-last: 1 → 4 → 1 is someone
   *  who cannot decide, and first-versus-last scores it as perfect agreement. */
  pairs: number;
  agreements: number;
  rate: number | null;
  /** Pairs where **both** writes touched exactly one image. The honest subset:
   *  a sweep cannot inflate it. */
  singleton_pairs: number;
  singleton_agreements: number;
  singleton_rate: number | null;
  /** `pairs − singleton_pairs`, stated rather than left to be inferred. */
  bulk_pairs: number;
  /** Pairs where either side cleared the rating. **Excluded** from `pairs`:
   *  withdrawing a judgement is not a second opinion. */
  cleared_pairs: number;
  /** |Δ| = 1 — a boundary wobble, not a Keep↔Cut flip. */
  adjacent: number;
  /** |Δ| ≥ 2. */
  distant: number;
  rate_within_1: number | null;
  first_last_pairs: number;
  first_last_agreements: number;
  first_last_rate: number | null;
}

export interface RatingSummary {
  total: number;
  rated: number;
  unrated: number;
  /** Rated images whose pixels were rewritten since — the judgement is about
   *  pixels that no longer exist. */
  rating_stale: number;
  /** Keyed "1".."4"; every tier present even at zero. */
  by_rating: Record<string, number>;
  events: { total: number; images_with_events: number; images_with_repeats: number };
  self_agreement: SelfAgreement;
}

export interface BoundaryAgreement {
  /** "1v2" | "2v3" | "3v4". */
  boundary: string;
  n_lo: number;
  n_hi: number;
  /** P(an image from the upper tier scores above one from the lower), ties at
   *  0.5. **0.5 is a coin flip**, which is why its bar is drawn centred there
   *  rather than from zero. Null when either tier is empty. */
  auc: number | null;
}

export interface ScorerModelAgreement {
  /** The `aesthetic_model` marker: "laion" | "v2_5" | a future `head:{uuid}`. */
  model: string;
  n: number;
  spearman: number | null;
  /** The largest ρ **any** scorer could reach against this tier distribution —
   *  with a four-level target the tie structure caps it below 1.0. Shown beside
   *  `spearman` because a bare 0.31 reads as failure forever, while "0.31 of a
   *  possible 0.97" is the truth. */
  spearman_ceiling: number | null;
  /** Keyed "1".."4"; null for a tier with nobody in it (not 0, which would read
   *  as "the scorer rates them at zero"). Four flat means is the most legible
   *  possible statement that a scorer knows nothing about your taste. */
  mean_by_rating: Record<string, number | null>;
  boundaries: BoundaryAgreement[];
}

export interface ScorerAgreement {
  rated: number;
  scored_and_rated: number;
  rated_unscored: number;
  /** Ordered by `n` desc. A **list**, not a dict, so a future `head:{uuid}`
   *  producer lands in it without a shape change. */
  models: ScorerModelAgreement[];
}

/** Both endpoints pool across every dataset and take no `dataset_id`: a head
 *  trained from pooled labels cannot live under one dataset. */
export const ratingApi = {
  summary: () =>
    client.get<RatingSummary>("/rating/summary").then((r) => r.data),
  scorerAgreement: () =>
    client.get<ScorerAgreement>("/rating/scorer-agreement").then((r) => r.data),
};
