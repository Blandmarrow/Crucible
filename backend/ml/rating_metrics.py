"""Pure-numpy statistics over the rating corpus — no DB, no torch, no images.

Everything here takes plain lists and returns plain Python, which is what makes
it unit-testable in CI with no session and no model. `backend/ml/similarity_scorer.py`
is the template.

**Why not scipy.** `backend/requirements-ci.txt` declares `numpy>=1.26` and no
scipy; scipy arrives anyway, but only as a *transitive* dependency of
`imagehash>=4.3` (verified: `pip show imagehash` → `Requires: numpy, pillow,
PyWavelets, scipy`). Depending on it here would make this module's correctness
hostage to a pHash library's dependency list — imagehash dropping scipy, or
declaring it an extra, would break rank correlation with no visible connection
between the two. So Spearman is hand-rolled. `test_rating_metrics.py`
cross-checks it against `scipy.stats.spearmanr` behind an `importorskip`, which
runs wherever scipy happens to be present and skips cleanly where it is not.

**Why the name is not `*_scorer.py`.** `test_ml_image_opens.py` guards a suffix
set (`_scorer`/`_captioner`/`_predictor`/`_tagger`) that every member must either
open images through `image_utils.open_rgb` or carry a triage entry explaining why
not. This module opens nothing and loads nothing, so either entry would be a lie.

**Every guard returns `None`, never NaN.** These values are serialised to JSON,
which cannot carry NaN — a missing zero-variance guard becomes a serialisation
error rather than a wrong number. Zero variance is a real early state: forty
images all rated Cut.
"""
import numpy as np

# A rating is 1–4; the tiers are ordered worst-to-best, matching the column.
RATING_TIERS = (1, 2, 3, 4)


def average_ranks(x) -> np.ndarray:
    """Ranks of `x`, ties sharing the mean of the positions they span.

    **Mandatory, not a refinement.** The rating vector has exactly four distinct
    values, so on a corpus of hundreds every rank is the mean of hundreds of
    positions. Ordinal ranking would instead impose an arbitrary within-tier order
    fixed by row order, and the resulting "correlation" would partly be measuring
    `images.id` ordering — silently, and in a number nobody would think to doubt.
    It is also what `scipy.stats.spearmanr` does, which is what makes the
    cross-check in the tests meaningful.

    `[1, 2, 2, 3]` -> `[1.0, 2.5, 2.5, 4.0]` (1-based, as ranks are).
    """
    a = np.asarray(x, dtype=np.float64)
    n = a.size
    if n == 0:
        return np.empty(0, dtype=np.float64)
    order = np.argsort(a, kind="mergesort")   # stable, so ties keep input order
    sorted_a = a[order]
    ranks_sorted = np.empty(n, dtype=np.float64)

    start = 0
    for i in range(1, n + 1):
        if i == n or sorted_a[i] != sorted_a[start]:
            # Positions start..i-1 are one tie group; 1-based mean rank.
            ranks_sorted[start:i] = (start + i + 1) / 2.0
            start = i

    ranks = np.empty(n, dtype=np.float64)
    ranks[order] = ranks_sorted
    return ranks


def spearman(x, y) -> float | None:
    """Spearman's rank correlation. `None` when it is undefined.

    Undefined means fewer than three paired observations (two points are always
    perfectly correlated, which says nothing), or zero variance in the ranks of
    either side — every image scored the same, or every image rated the same.
    """
    a = np.asarray(x, dtype=np.float64)
    b = np.asarray(y, dtype=np.float64)
    if a.size != b.size:
        raise ValueError("x and y must be the same length")
    if a.size < 3:
        return None

    ra, rb = average_ranks(a), average_ranks(b)
    sa, sb = float(ra.std()), float(rb.std())
    if sa == 0.0 or sb == 0.0:
        return None

    cov = float(((ra - ra.mean()) * (rb - rb.mean())).mean())
    rho = cov / (sa * sb)
    # Clamp: exactly ±1 can come back as ±1.0000000000000002 from the division.
    return float(max(-1.0, min(1.0, rho)))


def spearman_ceiling(scores, tiers) -> float | None:
    """The largest ρ any scorer could achieve against these tiers.

    With a four-level `y`, ρ is bounded away from 1.0 no matter how good the
    scorer is: the tie structure of the rating vector caps it. Reporting "0.31"
    alone reads as failure forever; reporting "0.31 of a possible 0.97" is the
    same move this whole measurement exists to make.

    Computed by correlating the tiers against a perfectly ordered scorer — one
    whose scores sort exactly as the tiers do. `scores` is accepted (and only its
    length used) so the caller cannot accidentally compute a ceiling for a
    different-sized population than the ρ it accompanies.
    """
    t = np.asarray(tiers, dtype=np.float64)
    if np.asarray(scores).size != t.size:
        raise ValueError("scores and tiers must be the same length")
    if t.size < 3:
        return None
    # A perfect scorer's values, by construction: the tiers themselves, broken
    # into a strict order within each tier. Sorting the tiers and pairing them
    # with 0..n-1 is exactly that.
    perfect = np.argsort(np.argsort(t, kind="mergesort"), kind="mergesort")
    return spearman(perfect, t)


def ordering_auc(scores_lo, scores_hi) -> float | None:
    """P(a randomly chosen `hi` scores above a randomly chosen `lo`), ties at 0.5.

    The Mann-Whitney U statistic, which is what "how often does this scorer get
    this one boundary right" means when both sides are unbalanced. Computed from
    `average_ranks` in O(n log n) — it never materialises the |lo|×|hi| pair
    matrix, which on a real corpus would be tens of millions of comparisons per
    boundary.

    Ties earning 0.5 is the honest treatment and the case a naive `>` count gets
    wrong: two images the scorer cannot separate are neither a success nor a
    failure. `None` when either side is empty.
    """
    lo = np.asarray(scores_lo, dtype=np.float64)
    hi = np.asarray(scores_hi, dtype=np.float64)
    if lo.size == 0 or hi.size == 0:
        return None
    combined = np.concatenate([lo, hi])
    ranks = average_ranks(combined)
    rank_sum_hi = float(ranks[lo.size:].sum())
    u = rank_sum_hi - hi.size * (hi.size + 1) / 2.0
    return float(u / (lo.size * hi.size))


def self_agreement(events) -> dict:
    """How often the same image got the same rating twice.

    `events` is an iterable of `(image_id, rating, batch_size)` triples **already
    ordered by `(image_id, id)`** — the event table's index shape. Ordering by id
    rather than timestamp is load-bearing: a bulk write stamps one `created_at`
    across its whole batch, so timestamps tie constantly.

    **Consecutive pairs, not first-versus-last.** First-versus-last hides
    oscillation: 1 → 4 → 1 scores as perfect agreement while describing someone
    who cannot decide. First-versus-last is reported alongside as a labelled
    secondary figure — it is free and answers a different question ("where did you
    end up relative to where you started").

    **Returns raw counts, never a bare rate**, because the day-one figure is a
    diagnostic and not a measurement, and three biases pull it in known
    directions:

    - *selection* — you re-rate an image because you disagree with what is stored,
      so the sample is enriched for disagreement: biases the number **down**;
    - *anchoring* — a second look sees the previous answer first: **up**;
    - *bulk sweep* — select-all then press 1 writes events for images nobody
      looked at, and two such sweeps agree perfectly: **up**, and the largest
      threat. `singleton_*` exists to isolate it, counting only pairs where
      **both** sides were one-image writes.

    A client that wants a percentage has to reach past the counts to get it, which
    is the point. A blind re-show — system-selected, previous answer hidden — is
    what turns this into a real ceiling, and that is not this phase.
    """
    by_image: dict[str, list[tuple[int | None, int | None]]] = {}
    for image_id, rating, batch_size in events:
        by_image.setdefault(image_id, []).append((rating, batch_size))

    repeats = {k: v for k, v in by_image.items() if len(v) > 1}

    pairs = agreements = 0
    singleton_pairs = singleton_agreements = 0
    cleared_pairs = 0
    adjacent = distant = 0
    first_last_pairs = first_last_agreements = 0

    for seq in repeats.values():
        for (r_a, b_a), (r_b, b_b) in zip(seq, seq[1:]):
            # A clear is a withdrawal, not a second judgement: it cannot agree or
            # disagree with anything, so the pair is excluded rather than counted
            # as a mismatch.
            if r_a is None or r_b is None:
                cleared_pairs += 1
                continue
            pairs += 1
            same = r_a == r_b
            agreements += same
            delta = abs(r_a - r_b)
            if delta == 1:
                adjacent += 1
            elif delta >= 2:
                distant += 1
            if b_a == 1 and b_b == 1:
                singleton_pairs += 1
                singleton_agreements += same

        first, last = seq[0][0], seq[-1][0]
        if first is not None and last is not None:
            first_last_pairs += 1
            first_last_agreements += first == last

    def _rate(hits: int, total: int) -> float | None:
        return float(hits) / total if total else None

    return {
        "images_with_repeats": len(repeats),
        "pairs": pairs,
        "agreements": agreements,
        "rate": _rate(agreements, pairs),
        "singleton_pairs": singleton_pairs,
        "singleton_agreements": singleton_agreements,
        "singleton_rate": _rate(singleton_agreements, singleton_pairs),
        # Stated rather than left to be inferred from the two numbers above, so a
        # client can show the gap without arithmetic it might get wrong.
        "bulk_pairs": pairs - singleton_pairs,
        "cleared_pairs": cleared_pairs,
        # A boundary wobble is not a Keep↔Cut flip, and conflating the two makes
        # the headline rate look far worse than the disagreement it describes.
        "adjacent": adjacent,
        "distant": distant,
        "rate_within_1": _rate(agreements + adjacent, pairs),
        "first_last_pairs": first_last_pairs,
        "first_last_agreements": first_last_agreements,
        "first_last_rate": _rate(first_last_agreements, first_last_pairs),
    }
