import { useState } from "react";

import { imagesApi } from "../../api/images";
import type { StyleDistribution } from "../../api/quality";
import { styleModeLabel } from "../../constants/styleModes";
import { formatTimeAgo } from "../../utils/duration";
import { isPartialScopeRun, percentileOf, percentileLabel, styleMatchTitle } from "../../utils/percentile";

/** How many reference thumbnails render before the "+N more" chip takes over. */
const REFERENCE_TILES_SHOWN = 8;

interface Props {
  /** The image's raw cosine, straight from `Image.style_similarity_score`. */
  score: number | null | undefined;
  /** Whether the pixels have been rewritten since the score was measured. */
  stale: boolean;
  /** The dataset's distribution and run descriptor; undefined while loading. */
  distribution: StyleDistribution | undefined;
  datasetId: string | null | undefined;
  /** The image being viewed — ringed in the reference strip if it was itself a
   *  reference, since a reference scores ~1.0 against its own centroid and would
   *  otherwise look like a suspiciously perfect match. */
  imageId: string;
  /** Opens another image in this pane. */
  onOpenImage: (imageId: string) => void;
}

/**
 * "What does this style score mean, and what produced it?" — the block that makes
 * `style_similarity_score` readable on the detail page.
 *
 * It replaces a bare `Style match 62%` row in the flat scores grid. That grid is a
 * two-column list of one-line facts and this needs three rows, a meter and a
 * thumbnail strip, so it mounts below the grid rather than inside it.
 *
 * The raw cosine stays on screen throughout: this work makes the number readable,
 * it does not hide it.
 */
export default function StyleMatchPanel({ score, stale, distribution, datasetId, imageId, onOpenImage }: Props) {
  // References can be deleted after a run — `reference_image_ids` is deliberately
  // not kept in sync — so a 404 on a thumbnail is the *expected* state, not a
  // fault. Held in state rather than hiding the tile with an imperative
  // `style.display` write: React owns that button's `style` prop, and a
  // declarative drop cannot be undone by a later re-render.
  const [missingRefs, setMissingRefs] = useState<Set<string>>(new Set());

  if (score == null) return null;

  const run = distribution?.run ?? null;
  const pct = percentileOf(score, distribution?.quantiles);
  const title = styleMatchTitle({ percentile: pct, score, distribution, stale });

  const shownRefs = (run?.reference_image_ids ?? []).slice(0, REFERENCE_TILES_SHOWN);
  // What the strip actually renders: a reference deleted since the run 404s its
  // thumbnail and drops out (see `missingRefs`).
  const visibleRefs = shownRefs.filter((refId) => !missingRefs.has(refId));
  // Counted against the tiles on screen, so a dropped tile moves into the chip
  // rather than vanishing from both. Covers overflow past the strip *and* the
  // server-side cap on how many ids the descriptor stores, since `reference_count`
  // is the true number of references either way.
  const moreRefs = (run?.reference_count ?? 0) - visibleRefs.length;
  const totalRefs = (run?.reference_count ?? 0) + (run?.external_reference_count ?? 0);

  return (
    <div
      data-testid="style-match-panel"
      style={{ marginTop: 10, borderTop: "1px solid var(--line)", paddingTop: 8 }}
    >
      <div style={{ fontSize: 11, fontWeight: 600, color: "var(--fg-mute)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
        Style match
      </div>

      {/* Meter + percentile + raw cosine */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8 }} title={title}>
        {pct !== null && (
          <>
            <div style={{ flex: 1, height: 6, borderRadius: 3, background: "var(--surface-3)", overflow: "hidden" }}>
              <div style={{
                height: "100%",
                width: `${Math.max(2, pct)}%`,
                background: stale ? "var(--fg-mute)" : "var(--accent)",
                opacity: stale ? 0.5 : 1,
                borderRadius: 3,
                transition: "width .2s",
              }} />
            </div>
            <span style={{ fontSize: 11, color: "var(--fg)", whiteSpace: "nowrap" }}>{percentileLabel(pct)}</span>
          </>
        )}
        <span style={{ fontSize: 11, color: "var(--fg-dim)", fontFamily: "Geist Mono, monospace", marginLeft: pct === null ? 0 : "auto" }}>
          {score.toFixed(4)}
        </span>
      </div>

      {pct === null && distribution && (
        <p style={{ fontSize: 11, color: "var(--fg-dim)", margin: "6px 0 0", lineHeight: 1.4 }}>
          {distribution.scored <= 1
            ? "Only this image is scored, so there is nothing to rank it against — the raw cosine is all this number can mean."
            : "Every scored image in this dataset has the same score, so a ranking would be meaningless."}
        </p>
      )}

      {/* What produced it */}
      <div style={{ fontSize: 11, color: "var(--fg-mute)", marginTop: 6 }}>
        {run ? (
          <>
            {styleModeLabel(run.embedding_type)}
            {run.dino_layer != null && ` · layer ${run.dino_layer}`}
            {` · ${totalRefs} reference${totalRefs === 1 ? "" : "s"}`}
            {run.updated_at && ` · scored ${formatTimeAgo(run.updated_at)}`}
          </>
        ) : (
          <>Scored, but the run details were not recorded — these scores predate run tracking, or were copied from another dataset.</>
        )}
      </div>

      {run?.scoped_image_count != null && isPartialScopeRun(distribution) && (
        <p style={{ fontSize: 11, color: "var(--warn)", margin: "6px 0 0", lineHeight: 1.4 }}>
          That run covered only {run.scoped_image_count} selected image
          {run.scoped_image_count === 1 ? "" : "s"}. The rest of this dataset still carries scores from an
          earlier run, so the percentile above mixes two runs and may not be comparable.
        </p>
      )}

      {/* Reference thumbnails. A tile whose thumbnail 404s drops out (see
          `missingRefs` above), and the whole strip disappears when they all do.
          `+N more` counts everything `reference_count` knows about that is not on
          screen, so the run's true reference total is always tiles + chip. */}
      {visibleRefs.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 8, alignItems: "center" }}>
          {visibleRefs.map((refId) => (
            <button
              key={refId}
              className="icon-btn"
              style={{
                width: 28, height: 28, padding: 0, borderRadius: 4, overflow: "hidden",
                border: refId === imageId ? "2px solid var(--accent)" : "1px solid var(--line-2)",
                background: "var(--surface-2)", flexShrink: 0,
              }}
              title={refId === imageId
                ? "This image was one of the references — a reference scores near 1.0 against its own centroid, so its high match is expected."
                : "Open this reference image"}
              onClick={(e) => { e.stopPropagation(); onOpenImage(refId); }}
              disabled={!datasetId}
            >
              <img
                src={imagesApi.thumbnailUrl(refId)}
                alt=""
                loading="lazy"
                style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
                onError={() => setMissingRefs((prev) => (prev.has(refId) ? prev : new Set(prev).add(refId)))}
              />
            </button>
          ))}
          {moreRefs > 0 && (
            <span style={{ fontSize: 10.5, color: "var(--fg-dim)" }}>+{moreRefs} more</span>
          )}
        </div>
      )}
    </div>
  );
}
