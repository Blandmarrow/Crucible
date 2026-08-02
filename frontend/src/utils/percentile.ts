import type { StyleRunDescriptor } from "../api/quality";
import { styleModeLabel } from "../constants/styleModes";

/**
 * Placing one style-similarity score inside its dataset's own distribution.
 *
 * `Image.style_similarity_score` is a raw cosine whose scale depends entirely on
 * the mode that produced it — CLIP spans ~0.53–0.93 on the same images DINOv2
 * spans 0.05–0.70, and a per-layer run below layer 10 compresses everything into
 * 0.90–0.99. A fixed good/warn/bad threshold would therefore mean five different
 * things in five modes, which is the defect, not the fix. A percentile over the
 * dataset's own scores is mode-invariant by construction.
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

/** "Top 8%" / "Bottom 3%" / "Median" — a percentile read the way a person says it. */
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
  run: StyleRunDescriptor | null | undefined;
  stale?: boolean;
}): string {
  const { percentile, score, run, stale } = opts;
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
    if (run.scoped_image_count != null) {
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
