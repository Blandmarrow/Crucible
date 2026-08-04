import type { StyleDistribution, StyleRunDescriptor } from "../api/quality";
import { styleModeLabel } from "../constants/styleModes";

/**
 * Placing one style-similarity score inside its dataset's own distribution.
 *
 * `Image.style_similarity_score` is a raw cosine whose scale depends entirely on
 * what produced it — CLIP spans ~0.53–0.93 on the same images DINOv2 spans
 * 0.05–0.70, a low-layer run compresses everything into 0.90–0.99, and the same
 * mode at a different blend weight is a different scale again. A fixed
 * good/warn/bad threshold would therefore mean several different things at once,
 * which is the defect, not the fix. A percentile over the dataset's own scores is
 * invariant to all of it by construction — which is also why the meter's *length*
 * carries the meaning and its colour never does.
 *
 * There is **no frontend unit-test runner in this repo**, so every degenerate
 * input is handled defensively here and the shapes that produce them are
 * generated from the backend side by `backend/tests/test_style_distribution_http.py`
 * (one scored image → 21 identical breakpoints; three scores → repeated values).
 */

/**
 * Where `score` falls in `quantiles`, as 0–100, or null when the question has no
 * answer.
 *
 * `quantiles` is the endpoint's array: ascending, evenly spaced in *percentile*
 * (every `quantile_step`), with q0 and q100 exactly the dataset's min and max.
 *
 * Null — meaning "render nothing" — is returned for:
 *  - a null score (the image was never scored), and
 *  - fewer than two breakpoints (the payload has not loaded), and
 *  - an all-identical distribution, which includes the single-scored-image case.
 *    A meter there would claim a ranking that does not exist; the caller falls
 *    back to showing the raw cosine alone.
 *
 * A zero-length bar is deliberately *not* the answer to any of these: it reads as
 * "0th percentile", not as "not measured".
 *
 * Repeated breakpoints are normal whenever fewer images are scored than there are
 * breakpoints. The answer for a score inside a flat run is that run's **low
 * edge**, which is what walking forward to the first breakpoint above the score
 * gives. Scores outside the range clamp to 0 and 100.
 */
export function percentileOf(score: number | null | undefined, quantiles: number[] | undefined): number | null {
  if (score == null || !Number.isFinite(score)) return null;
  if (!quantiles || quantiles.length < 2) return null;

  const lo = quantiles[0];
  const hi = quantiles[quantiles.length - 1];
  if (!(hi > lo)) return null; // every score identical — no ranking to report

  const step = 100 / (quantiles.length - 1);
  if (score <= lo) return 0;
  if (score >= hi) return 100;

  for (let i = 1; i < quantiles.length; i++) {
    if (score < quantiles[i]) {
      const span = quantiles[i] - quantiles[i - 1];
      // A flat run has span 0 and no interior to interpolate: the score sits at
      // the run's low edge, which is the previous breakpoint's percentile.
      const frac = span > 0 ? (score - quantiles[i - 1]) / span : 0;
      return Math.max(0, Math.min(100, (i - 1 + frac) * step));
    }
  }
  return 100;
}

/**
 * The breakpoint at percentile `p` — the payload's own answer to "what score sits
 * at the median / the top 10% line?".
 *
 * The index is derived from `quantile_step` rather than hardcoded, because
 * `STYLE_QUANTILE_STEP` is a backend constant a caller cannot see: at step 5 the
 * median is `quantiles[10]`, at step 10 it is `quantiles[5]`, and a literal index
 * would silently relabel the max as the median if that constant ever moved. Falls
 * back to the spacing the array itself implies when the field is absent or zero,
 * and clamps, so an out-of-range `p` yields an edge breakpoint rather than
 * `undefined`.
 *
 * Returns undefined only when there are no breakpoints at all (nothing scored).
 */
export function quantileAt(distribution: StyleDistribution | undefined, p: number): number | undefined {
  const quantiles = distribution?.quantiles;
  if (!quantiles || quantiles.length === 0) return undefined;
  const step = distribution?.quantile_step || 100 / Math.max(1, quantiles.length - 1);
  const i = Math.round(p / step);
  return quantiles[Math.max(0, Math.min(quantiles.length - 1, i))];
}

/**
 * Whether the run behind this distribution left older scores in place — the only
 * case in which a percentile really does mix two runs.
 *
 * `scoped_image_count != null` alone is too coarse: gallery *Select all matching
 * filters* sends every image id, so a run that covered the whole dataset is
 * recorded as scoped and would carry a caveat about nothing. `scored_count >=
 * scored` says the run wrote at least as many scores as the dataset currently
 * carries, so no score from an earlier run survives to mix with.
 *
 * Deliberately a test over the *current* payload rather than a boolean frozen at
 * run time: images come and go afterwards, and re-evaluating on every read is what
 * lets the verdict correct itself when they do.
 */
export function isPartialScopeRun(distribution: StyleDistribution | undefined): boolean {
  const run = distribution?.run;
  if (!run || run.scoped_image_count == null) return false;
  return run.scored_count < (distribution?.scored ?? 0);
}

/** "Top 8%" / "Bottom 3%" — a percentile read the way a person says it. */
export function percentileLabel(p: number): string {
  const top = Math.max(1, Math.round(100 - p));
  const bottom = Math.max(1, Math.round(p));
  if (p >= 50) return `Top ${top}%`;
  return `Bottom ${bottom}%`;
}

/** The tooltip shown on both the gallery meter and the detail block.
 *
 *  One builder, because the two surfaces carry the same caveats — which mode
 *  produced the number, whether the run covered the whole dataset, whether the
 *  pixels have since been rewritten — and a second copy would drift on exactly
 *  those. */
export function styleMatchTitle(opts: {
  percentile: number | null;
  score: number | null | undefined;
  /** The whole payload, not just its `run`, because the scoped caveat is a test
   *  over both (see `isPartialScopeRun`). Both call sites already hold it. */
  distribution: StyleDistribution | undefined;
  stale?: boolean;
}): string {
  const { percentile, score, distribution, stale } = opts;
  const run: StyleRunDescriptor | null | undefined = distribution?.run;
  const parts: string[] = [];

  if (percentile != null) {
    parts.push(`Style match — ${percentileLabel(percentile)} of this dataset's scored images`);
  } else {
    parts.push("Style match");
  }
  if (score != null && Number.isFinite(score)) {
    parts.push(`Raw cosine ${score.toFixed(4)}`);
  }

  if (run) {
    const mode = styleModeLabel(run.embedding_type);
    const layer = run.dino_layer != null ? `, layer ${run.dino_layer}` : "";
    const refs = run.reference_count + run.external_reference_count;
    parts.push(`Scored with ${mode}${layer} against ${refs} reference${refs === 1 ? "" : "s"}`);
    if (isPartialScopeRun(distribution)) {
      parts.push(
        `That run covered only ${run.scoped_image_count} selected image${run.scoped_image_count === 1 ? "" : "s"} — ` +
        "the rest of the dataset still carries scores from an earlier run, so these numbers may not be comparable.",
      );
    }
  } else {
    parts.push("The run that produced this score was not recorded, so the mode and references are unknown.");
  }

  if (stale) {
    parts.push("This image's pixels were rewritten after it was scored");
  }
  // Sentences are written without their full stop (some clauses carry one
  // internally), so trim and re-punctuate rather than risking "..".
  return parts.map((s) => s.replace(/\.$/, "")).join(". ") + ".";
}
