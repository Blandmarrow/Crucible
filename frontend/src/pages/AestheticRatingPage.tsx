import { useQuery } from "@tanstack/react-query";

import { ratingApi } from "../api/rating";
import type { ScorerAgreement, ScorerModelAgreement, SelfAgreement } from "../api/rating";
import { aestheticModelLabel } from "../constants/aestheticModels";
import { RATING_OPTIONS, ratingColor, ratingLabel } from "../constants/rating";

/**
 * Aesthetic Rating — what the keep/cut ratings say about themselves.
 *
 * Two questions, and neither needs a model: **how often do you give the same
 * image the same answer twice** (your own ceiling — a learned head at 84% when
 * you self-agree 87% is *at ceiling*, and more labeling buys nothing), and
 * **does an existing scorer already track your taste** (if LAION already
 * correlates near that ceiling, a learned head is answered before it is built).
 *
 * The page exists now rather than alongside a head so a labeling queue has a
 * home and needs no re-homing later.
 */

/** Below this many comparable pairs the page shows counts and no percentage.
 *  A ceiling from three pairs is noise wearing a number, and not shipping those
 *  is the entire point of measuring first. */
const MIN_CEILING_PAIRS = 10;

const pct = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`;

const num = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined ? "—" : v.toFixed(digits);

export default function AestheticRatingPage() {
  // No dataset in either key: both endpoints pool across every dataset, because
  // a head trained from pooled labels cannot live under one.
  const { data: summary, isLoading: summaryLoading, isError: summaryError } = useQuery({
    queryKey: ["rating-summary"],
    queryFn: ratingApi.summary,
  });
  const { data: agreement, isError: agreementError } = useQuery({
    queryKey: ["rating-scorer-agreement"],
    queryFn: ratingApi.scorerAgreement,
  });

  if (summaryLoading) {
    return <div style={{ padding: 40, color: "var(--fg-mute)" }}>Loading…</div>;
  }
  if (summaryError || !summary) {
    return (
      <div style={{ padding: 40, color: "var(--bad)", fontSize: 13 }}>
        Failed to load the rating summary.
      </div>
    );
  }

  const sa = summary.self_agreement;
  const ratedPct = summary.total ? Math.round((summary.rated / summary.total) * 100) : 0;
  const haveCeiling = sa.pairs >= MIN_CEILING_PAIRS;

  return (
    <div style={{ flex: 1, overflowY: "auto", padding: "24px 28px" }}>
      <div className="page-h" style={{ marginBottom: 20 }}>
        <div>
          <h1>Aesthetic Rating</h1>
          <p>
            Keep/cut ratings pooled across <strong>every</strong> dataset — taste is
            yours, not a dataset's, so everything here counts the whole library.
          </p>
        </div>
      </div>

      {/* ── Tiles ── */}
      <div
        style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10, marginBottom: 22 }}
      >
        {/* Test ids rather than heading text: three of the four tile labels
            contain the substring "Rated". */}
        <div className="stat-card" data-testid="tile-rated">
          <div className="sk">Rated</div>
          <div className="sv">
            {summary.rated.toLocaleString()}
            <small>{ratedPct}% of {summary.total.toLocaleString()}</small>
          </div>
          {summary.rating_stale > 0 && (
            <div className="sdelta" style={{ color: "var(--warn)" }}>
              {summary.rating_stale.toLocaleString()} rated before an edit
            </div>
          )}
        </div>
        <div className="stat-card" data-testid="tile-unrated">
          <div className="sk">Unrated</div>
          <div className="sv">{summary.unrated.toLocaleString()}</div>
        </div>
        <div className="stat-card" data-testid="tile-rerated">
          <div className="sk">Re-rated images</div>
          <div className="sv">{summary.events.images_with_repeats.toLocaleString()}</div>
          <div className="sdelta" style={{ color: "var(--fg-dim)" }}>
            of {summary.events.images_with_events.toLocaleString()} ever rated
          </div>
        </div>
        <div className="stat-card" data-testid="tile-self-agreement">
          <div className="sk">Self-agreement</div>
          <div className="sv" data-testid="self-agreement-value">
            {haveCeiling ? pct(sa.rate) : "—"}
          </div>
          <div className="sdelta" style={{ color: "var(--fg-dim)" }}>
            {sa.pairs.toLocaleString()} comparable {sa.pairs === 1 ? "pair" : "pairs"}
          </div>
        </div>
      </div>

      <RatingDistribution byRating={summary.by_rating} unrated={summary.unrated} />

      <SelfAgreementPanel sa={sa} haveCeiling={haveCeiling} />

      {agreementError ? (
        <p style={{ color: "var(--bad)", fontSize: 12 }}>
          Failed to load scorer agreement.
        </p>
      ) : (
        <ScorerAgreementPanel data={agreement} />
      )}
    </div>
  );
}

/* ── Rating distribution ──────────────────────────────────────────────────────
 * A small local bar row rather than StatsPage's `CssHist`: that component is not
 * exported, and a cross-page import would fold StatsPage's chunk into this one —
 * exactly what `pages/lazyPages.ts` exists to prevent. */
function RatingDistribution({
  byRating,
  unrated,
}: {
  byRating: Record<string, number>;
  unrated: number;
}) {
  const bars = RATING_OPTIONS.map((o) => ({
    label: o.label,
    color: o.color as string,
    count: byRating[String(o.value)] ?? 0,
  }));
  const max = Math.max(...bars.map((b) => b.count), 1);
  const rated = bars.reduce((a, b) => a + b.count, 0);

  return (
    <section className="card" style={{ padding: 18, marginBottom: 18 }}>
      <h3 style={{ fontSize: 13, fontWeight: 600, margin: "0 0 2px" }}>Rating distribution</h3>
      <p style={{ color: "var(--fg-mute)", fontSize: 11.5, margin: "0 0 14px" }}>
        {rated.toLocaleString()} rated, {unrated.toLocaleString()} unrated.
      </p>
      {rated === 0 ? (
        <p style={{ color: "var(--fg-dim)", fontSize: 12, margin: 0 }}>
          Nothing rated yet. Rate images from the gallery with the number keys 1–4.
        </p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {bars.map((b) => (
            <div key={b.label} style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ width: 96, fontSize: 12, color: b.color, flexShrink: 0 }}>
                {b.label}
              </span>
              <div
                style={{
                  flex: 1, height: 10, background: "var(--surface-2)",
                  borderRadius: 3, overflow: "hidden",
                }}
              >
                <div
                  style={{
                    width: `${(b.count / max) * 100}%`, height: "100%",
                    background: b.color, borderRadius: 3,
                  }}
                />
              </div>
              <span
                style={{
                  width: 68, textAlign: "right", fontSize: 12,
                  fontFeatureSettings: '"tnum"', color: "var(--fg-dim)", flexShrink: 0,
                }}
              >
                {b.count.toLocaleString()}
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

/* ── Self-agreement ───────────────────────────────────────────────────────── */
function SelfAgreementPanel({ sa, haveCeiling }: { sa: SelfAgreement; haveCeiling: boolean }) {
  return (
    <section className="card" style={{ padding: 18, marginBottom: 18 }}>
      <h3 style={{ fontSize: 13, fontWeight: 600, margin: "0 0 2px" }}>
        Your own ceiling
      </h3>
      <p style={{ color: "var(--fg-mute)", fontSize: 11.5, margin: "0 0 14px" }}>
        How often you give the same image the same answer twice. It is the bar any
        automatic scorer should be measured against — matching it means the scorer
        is as consistent as you are, not that it is failing.
      </p>

      {haveCeiling ? (
        <>
          <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 4 }}>
            <span style={{ fontSize: 34, fontWeight: 600, letterSpacing: "-.02em" }}>
              {pct(sa.rate)}
            </span>
            <span style={{ color: "var(--fg-dim)", fontSize: 12.5 }}>
              {sa.agreements.toLocaleString()} of {sa.pairs.toLocaleString()} re-rating
              pairs agreed
            </span>
          </div>
          <dl
            style={{
              display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12,
              margin: "14px 0 0", fontSize: 12,
            }}
          >
            <Figure
              term="Deliberate re-rates"
              value={sa.singleton_pairs >= MIN_CEILING_PAIRS ? pct(sa.singleton_rate) : "—"}
              detail={`${sa.singleton_pairs.toLocaleString()} of ${sa.pairs.toLocaleString()} pairs, both one-image writes`}
            />
            <Figure
              term="From bulk sweeps"
              value={sa.bulk_pairs.toLocaleString()}
              detail="pairs where at least one side was a multi-image write"
            />
            <Figure
              term="Within one tier"
              value={pct(sa.rate_within_1)}
              detail={`${sa.adjacent.toLocaleString()} off by one, ${sa.distant.toLocaleString()} off by two or more`}
            />
          </dl>
        </>
      ) : (
        <div style={{ fontSize: 12.5, color: "var(--fg-dim)" }}>
          <strong style={{ color: "var(--fg)" }}>Not enough re-ratings yet.</strong>{" "}
          {sa.pairs.toLocaleString()} comparable{" "}
          {sa.pairs === 1 ? "pair" : "pairs"} so far; {MIN_CEILING_PAIRS} are needed
          before a percentage means anything. Rate an image you have already rated and
          it counts here.
        </div>
      )}

      {/* Always rendered, above and below the floor alike: the number is a
          diagnostic, and this line is what stops it being read as a measurement. */}
      <p
        style={{
          color: "var(--fg-mute)", fontSize: 11.5, margin: "14px 0 0",
          paddingTop: 12, borderTop: "1px solid var(--line)",
        }}
      >
        These are re-ratings <em>you chose to make</em>, with the previous answer
        visible — not a blind re-show. So the figure is biased down by choosing to
        revisit what you disagree with, and up by seeing your old answer first and by
        bulk sweeps that rate images nobody looked at. Treat it as a rough floor on
        your consistency, not a measurement of it.
      </p>
    </section>
  );
}

function Figure({ term, value, detail }: { term: string; value: string; detail: string }) {
  return (
    <div>
      <dt style={{ color: "var(--fg-mute)", fontSize: 11.5 }}>{term}</dt>
      <dd style={{ margin: "3px 0 0", fontSize: 18, fontWeight: 600, letterSpacing: "-.01em" }}>
        {value}
      </dd>
      <div style={{ color: "var(--fg-dim)", fontSize: 11, marginTop: 2 }}>{detail}</div>
    </div>
  );
}

/* ── Scorer agreement ─────────────────────────────────────────────────────── */
function ScorerAgreementPanel({ data }: { data: ScorerAgreement | undefined }) {
  if (!data) return null;

  return (
    <section style={{ marginBottom: 18 }}>
      <h3 style={{ fontSize: 13, fontWeight: 600, margin: "0 0 2px" }}>
        Does an existing scorer already know your taste?
      </h3>
      <p style={{ color: "var(--fg-mute)", fontSize: 11.5, margin: "0 0 14px" }}>
        Each aesthetic model is shown apart: their score scales are not comparable,
        so pooling them would measure the mix rather than either one.
      </p>

      {data.scored_and_rated === 0 ? (
        <div className="card" style={{ padding: 18, fontSize: 12.5, color: "var(--fg-dim)" }}>
          No image is both rated and scored yet.{" "}
          {data.rated > 0
            ? `${data.rated_unscored.toLocaleString()} rated ${data.rated_unscored === 1 ? "image has" : "images have"} no aesthetic score — run Score Images on them and this fills in.`
            : "Rate some images and run Score Images on them, and this fills in."}
        </div>
      ) : (
        <>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {data.models.map((m) => <ModelCard key={m.model} m={m} />)}
          </div>
          {data.rated_unscored > 0 && (
            <p style={{ color: "var(--fg-dim)", fontSize: 11.5, marginTop: 10 }}>
              {data.rated_unscored.toLocaleString()} rated{" "}
              {data.rated_unscored === 1 ? "image is" : "images are"} unscored and sit
              outside every figure above.
            </p>
          )}
        </>
      )}
    </section>
  );
}

function ModelCard({ m }: { m: ScorerModelAgreement }) {
  return (
    <div className="card" style={{ padding: 18 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 12 }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>{aestheticModelLabel(m.model)}</span>
        <span style={{ color: "var(--fg-dim)", fontSize: 11.5 }}>
          {m.n.toLocaleString()} rated and scored
        </span>
      </div>

      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 14 }}>
        <span style={{ fontSize: 26, fontWeight: 600, letterSpacing: "-.02em" }}>
          ρ {num(m.spearman)}
        </span>
        {/* Never the bare ρ: with four rating tiers the tie structure caps it
            below 1.0, so 0.31 alone reads as failure forever. */}
        <span style={{ color: "var(--fg-dim)", fontSize: 12.5 }}>
          of a possible {num(m.spearman_ceiling)}
        </span>
      </div>

      <div style={{ marginBottom: 14 }}>
        <div style={{ color: "var(--fg-mute)", fontSize: 11.5, marginBottom: 6 }}>
          Mean score by rating — flat means the scorer knows nothing about your taste.
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {RATING_OPTIONS.map((o) => {
            const v = m.mean_by_rating[String(o.value)];
            return (
              <div
                key={o.value}
                style={{
                  flex: 1, border: "1px solid var(--line)", borderRadius: "var(--r-sm)",
                  padding: "6px 8px",
                }}
              >
                <div style={{ fontSize: 11, color: ratingColor(o.value) }}>
                  {ratingLabel(o.value)}
                </div>
                <div
                  style={{ fontSize: 15, fontWeight: 600, fontFeatureSettings: '"tnum"' }}
                >
                  {num(v)}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div style={{ color: "var(--fg-mute)", fontSize: 11.5, marginBottom: 6 }}>
        Per-boundary accuracy — how often it puts the better image above the worse
        one. 0.5 is a coin flip.
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {m.boundaries.map((b) => <BoundaryRow key={b.boundary} b={b} />)}
      </div>
    </div>
  );
}

function BoundaryRow({ b }: { b: ScorerModelAgreement["boundaries"][number] }) {
  const [lo, hi] = b.boundary.split("v").map(Number);
  const auc = b.auc;
  // Centred on 0.5, never drawn from zero: a bar from 0 makes a coin flip look
  // like half a success rather than the no-information point it is.
  const magnitude = auc === null ? 0 : Math.abs(auc - 0.5) * 100;
  const positive = auc !== null && auc >= 0.5;

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 11.5 }}>
      <span style={{ width: 140, flexShrink: 0, color: "var(--fg-dim)" }}>
        {ratingLabel(lo)} vs {ratingLabel(hi)}
      </span>
      <div
        style={{
          flex: 1, height: 10, background: "var(--surface-2)", borderRadius: 3,
          position: "relative", overflow: "hidden",
        }}
        title={`${b.n_lo} vs ${b.n_hi} images`}
      >
        <div
          style={{
            position: "absolute", top: 0, bottom: 0, left: "50%", width: 1,
            background: "var(--line)",
          }}
        />
        <div
          style={{
            position: "absolute", top: 0, bottom: 0,
            left: positive ? "50%" : `${50 - magnitude}%`,
            width: `${magnitude}%`,
            background: positive ? "var(--good)" : "var(--bad)",
          }}
        />
      </div>
      <span
        style={{
          width: 44, textAlign: "right", flexShrink: 0,
          fontFeatureSettings: '"tnum"', color: "var(--fg-dim)",
        }}
      >
        {auc === null ? "—" : auc.toFixed(2)}
      </span>
    </div>
  );
}
