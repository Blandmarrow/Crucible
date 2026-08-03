/**
 * The keep/cut rating vocabulary — written once, imported by every surface.
 *
 * Six of them share these words (gallery badge, gallery filter chips, selection
 * toolbar modal, image detail control, export filters, stats panel), and a
 * dataset where one screen says "Keep" and another says "Best" is a dataset
 * nobody trusts. So the labels, the colours and the sentinel all live here.
 *
 * **Higher is better: 4 = Keep … 1 = Cut.** Every other numeric column in the
 * app runs that way, `SORT_OPTIONS` reads "Aesthetic ↓" for best-first, and a
 * star rating in any photo tool means more-is-better — an inverted scale would
 * make "Rating ↑" mean best-first and read wrong beside its neighbour. The cost
 * is that "press 1 for Keep" is unavailable; `0` clears instead, as in Lightroom.
 *
 * Decision language, not quality language: these say what to *do* with the
 * image, which is the judgement a human can actually make quickly. "Good" and
 * "bad" are what `aesthetic_score` is for.
 */
export const RATING_OPTIONS = [
  { value: 4, label: "Keep",         short: "K", color: "var(--good)" },
  { value: 3, label: "Probably",     short: "P", color: "var(--accent)" },
  { value: 2, label: "Probably not", short: "p", color: "var(--warn)" },
  { value: 1, label: "Cut",          short: "C", color: "var(--bad)" },
] as const;

export type RatingValue = (typeof RATING_OPTIONS)[number]["value"];

/** The filter's "not yet rated" entry.
 *
 *  `0` sits outside the stored 1–4 domain, so one `rating_filter` param can
 *  carry "these tiers **or** unrated" — which is the shape the chip row needs
 *  and which the license filter's `license_filter` + `license_missing` pair
 *  cannot express (`""` there is ambiguous: it is also a real license value).
 *  Never a stored `aesthetic_rating`; the backend schema caps that at 1–4. */
export const RATING_UNRATED = 0;

export const RATING_UNRATED_LABEL = "Unrated";

const BY_VALUE = new Map<number, (typeof RATING_OPTIONS)[number]>(
  RATING_OPTIONS.map((o) => [o.value, o]),
);

/** Label for a stored rating, or the unrated word for `null`/`0`. */
export function ratingLabel(value: number | null | undefined): string {
  if (value === null || value === undefined || value === RATING_UNRATED) return RATING_UNRATED_LABEL;
  return BY_VALUE.get(value)?.label ?? String(value);
}

/** Colour token for a stored rating; muted for unrated. */
export function ratingColor(value: number | null | undefined): string {
  if (value === null || value === undefined || value === RATING_UNRATED) return "var(--fg-mute)";
  return BY_VALUE.get(value)?.color ?? "var(--fg-mute)";
}

/** Short badge glyph — the numeral itself, which is also the key that sets it. */
export function ratingShort(value: number | null | undefined): string {
  if (value === null || value === undefined || value === RATING_UNRATED) return "—";
  return String(value);
}

/** The chip row's entries, best-first, with "Unrated" last: it is *no answer*,
 *  not a tier below Cut, which is the same reasoning that puts NULL last in
 *  both directions of the server-side sort. */
export const RATING_FILTER_ENTRIES = [
  ...RATING_OPTIONS.map((o) => ({ value: o.value as number, label: o.label, color: o.color as string })),
  { value: RATING_UNRATED, label: RATING_UNRATED_LABEL, color: "var(--fg-mute)" },
];

/** Encode a tier selection for the `rating_filter` query param. Empty → undefined
 *  (no filter), matching how the backend reads an absent param. */
export function encodeRatingFilter(values: number[]): string | undefined {
  return values.length ? JSON.stringify([...values].sort((a, b) => a - b)) : undefined;
}
