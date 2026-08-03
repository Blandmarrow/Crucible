"""`backend/ml/rating_metrics.py` — pure unit tests. No HTTP, no DB, no torch.

The module under test is deliberately dependency-free — numpy and nothing else —
so these run anywhere the collection path imports, torch or no torch. The one
scipy assertion is gated with `importorskip` inside a test body rather than
assumed: scipy is not declared in `backend/requirements-ci.txt` and reaches the
runner only as a transitive dependency of `imagehash`, which is a fact about a
pHash library's dependency list and not a guarantee this suite should rest on.

Tie handling is the thing worth testing hardest. The rating vector has four
distinct values over hundreds of rows, so every statistic here is dominated by
how ties are treated — average ranks in `spearman`, and 0.5 in `ordering_auc`,
which is the case a naive `>` count gets wrong.
"""
import pytest

from backend.ml.rating_metrics import (
    average_ranks,
    ordering_auc,
    self_agreement,
    spearman,
    spearman_ceiling,
)


# --- average_ranks ---------------------------------------------------------


def test_average_ranks_shares_tied_positions():
    assert list(average_ranks([1, 2, 2, 3])) == [1.0, 2.5, 2.5, 4.0]


def test_average_ranks_is_position_not_value():
    # Ranks are 1-based positions, so a gap in the values is not a gap in ranks.
    assert list(average_ranks([10, 20, 90])) == [1.0, 2.0, 3.0]


def test_average_ranks_handles_an_all_tied_vector():
    # The zero-variance case `spearman` must guard: every rank is the same.
    assert list(average_ranks([4, 4, 4, 4])) == [2.5, 2.5, 2.5, 2.5]


def test_average_ranks_of_an_empty_vector_is_empty():
    assert average_ranks([]).size == 0


# --- spearman --------------------------------------------------------------


def test_spearman_perfect_and_inverse():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)


def test_spearman_is_monotonic_not_linear():
    """Rank correlation, so an exponential-but-ordered scorer is still 1.0 —
    which is the property that makes it the right statistic for comparing an
    arbitrary score scale against a 1–4 rating."""
    assert spearman([1, 2, 3, 4], [1, 10, 1000, 100000]) == pytest.approx(1.0)


def test_spearman_hand_computed():
    # x ranks: [1,2,3,4]; y = [1,3,2,4] ranks: [1,3,2,4].
    # d = [0,-1,1,0] -> sum d^2 = 2; rho = 1 - 6*2/(4*15) = 0.8.
    assert spearman([1, 2, 3, 4], [1, 3, 2, 4]) == pytest.approx(0.8)


def test_spearman_returns_none_on_zero_variance():
    """A real early state — forty images all rated Cut — and NaN here would be a
    JSON serialisation error, not a visible wrong number."""
    assert spearman([1, 2, 3], [5, 5, 5]) is None
    assert spearman([5, 5, 5], [1, 2, 3]) is None


def test_spearman_returns_none_below_three_points():
    assert spearman([1, 2], [3, 4]) is None
    assert spearman([], []) is None


def test_spearman_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        spearman([1, 2, 3], [1, 2])


def test_spearman_matches_scipy_including_ties():
    """The cross-check the hand-rolled implementation exists to survive: the
    average-rank tie handling agrees with the reference. Skipped rather than
    required, because scipy is undeclared and only present transitively (see the
    module docstring) — it must never be the thing that fails a run."""
    stats = pytest.importorskip("scipy.stats")
    x = [0.9, 0.1, 0.5, 0.5, 0.2, 0.7, 0.7, 0.7, 0.3]
    y = [4, 1, 2, 3, 1, 4, 3, 4, 2]
    assert spearman(x, y) == pytest.approx(float(stats.spearmanr(x, y).statistic))


# --- spearman_ceiling ------------------------------------------------------


def test_spearman_ceiling_is_below_one_for_a_four_tier_target():
    tiers = [1] * 5 + [2] * 5 + [3] * 5 + [4] * 5
    scores = list(range(20))
    ceiling = spearman_ceiling(scores, tiers)
    assert ceiling is not None
    assert 0.9 < ceiling < 1.0   # bounded away from 1 by the tie structure alone


def test_spearman_ceiling_is_never_below_the_observed_rho():
    """The property that makes "ρ 0.31 of a possible 0.97" honest: no arrangement
    of scores can beat the ceiling."""
    tiers = [1, 1, 2, 2, 3, 3, 4, 4, 4, 1, 2, 3]
    scores = [0.2, 0.9, 0.4, 0.1, 0.8, 0.3, 0.95, 0.5, 0.6, 0.05, 0.7, 0.45]
    rho = spearman(scores, tiers)
    ceiling = spearman_ceiling(scores, tiers)
    assert rho is not None and ceiling is not None
    assert rho <= ceiling + 1e-12


def test_spearman_ceiling_is_one_when_the_target_has_no_ties():
    assert spearman_ceiling([9, 8, 7, 6], [1, 2, 3, 4]) == pytest.approx(1.0)


def test_spearman_ceiling_guards_like_spearman():
    assert spearman_ceiling([1, 2], [1, 2]) is None
    with pytest.raises(ValueError):
        spearman_ceiling([1, 2, 3], [1, 2])


# --- ordering_auc ----------------------------------------------------------


def test_ordering_auc_disjoint_populations():
    assert ordering_auc([0.1, 0.2, 0.3], [0.7, 0.8]) == pytest.approx(1.0)
    assert ordering_auc([0.7, 0.8], [0.1, 0.2, 0.3]) == pytest.approx(0.0)


def test_ordering_auc_of_identical_populations_is_a_coin_flip():
    """The tie case. A naive `count(hi > lo) / n` would read 0.0 here and call a
    scorer that cannot separate the two tiers *maximally wrong*."""
    assert ordering_auc([0.5, 0.5], [0.5, 0.5]) == pytest.approx(0.5)


def test_ordering_auc_splits_a_partial_tie_evenly():
    # hi=[2] beats lo=[1] and ties lo=[2]: (1 + 0.5) / 2 = 0.75.
    assert ordering_auc([1, 2], [2]) == pytest.approx(0.75)


def test_ordering_auc_returns_none_on_an_empty_side():
    assert ordering_auc([], [1, 2]) is None
    assert ordering_auc([1, 2], []) is None


# --- self_agreement --------------------------------------------------------


def _ev(image_id, rating, batch_size=1):
    return (image_id, rating, batch_size)


def test_self_agreement_counts_consecutive_pairs_not_first_and_last():
    """1 → 4 → 1 is someone who cannot decide. First-versus-last would score it
    as perfect agreement, which is why the headline is consecutive — and why
    first-versus-last is reported separately rather than not at all."""
    out = self_agreement([_ev("a", 1), _ev("a", 4), _ev("a", 1)])
    assert out["pairs"] == 2
    assert out["agreements"] == 0
    assert out["rate"] == pytest.approx(0.0)
    assert out["first_last_pairs"] == 1
    assert out["first_last_agreements"] == 1
    # The same history read in rating order rather than write order — what a
    # router sorting by `rating` would hand in. Every witness moves the other
    # way, which is what makes three events the minimum length that can tell
    # them apart. Pinned over HTTP too, where the ordering actually lives:
    # `test_a_three_event_history_is_read_in_write_order_not_sorted_by_rating`.
    sorted_out = self_agreement([_ev("a", 1), _ev("a", 1), _ev("a", 4)])
    assert sorted_out["agreements"] == 1 and sorted_out["first_last_agreements"] == 0


def test_self_agreement_ignores_images_with_one_event():
    out = self_agreement([_ev("a", 4), _ev("b", 3), _ev("c", 1)])
    assert out["images_with_repeats"] == 0
    assert out["pairs"] == 0
    assert out["rate"] is None


def test_self_agreement_of_an_empty_log_is_all_none_and_zero():
    out = self_agreement([])
    assert out["pairs"] == 0
    assert out["rate"] is None and out["singleton_rate"] is None
    assert out["rate_within_1"] is None


def test_a_cleared_rating_is_excluded_rather_than_counted_as_a_disagreement():
    """Clearing is a withdrawal, not a second opinion."""
    out = self_agreement([_ev("a", 4), _ev("a", None), _ev("a", 4)])
    assert out["cleared_pairs"] == 2
    assert out["pairs"] == 0
    assert out["rate"] is None


def test_singleton_pairs_require_both_sides_to_be_one_image_writes():
    """The bulk-sweep bias, isolated. A pair where either side came from a
    select-all sweep is not evidence about how consistently you judge."""
    out = self_agreement([
        _ev("a", 1, batch_size=1), _ev("a", 1, batch_size=1),      # both singleton
        _ev("b", 1, batch_size=900), _ev("b", 1, batch_size=1),    # one sweep side
        _ev("c", 3, batch_size=900), _ev("c", 3, batch_size=900),  # both sweeps
    ])
    assert out["pairs"] == 3
    assert out["agreements"] == 3
    assert out["singleton_pairs"] == 1
    assert out["singleton_agreements"] == 1
    assert out["bulk_pairs"] == 2


def test_an_unknown_batch_size_is_not_a_singleton():
    """Backfilled events carry NULL, which means "nothing is known about the
    write" — it must not be silently promoted to a deliberate one-image look."""
    out = self_agreement([_ev("a", 2, batch_size=None), _ev("a", 2, batch_size=None)])
    assert out["pairs"] == 1
    assert out["singleton_pairs"] == 0


def test_adjacent_and_distant_split_the_disagreements():
    out = self_agreement([
        _ev("a", 3), _ev("a", 4),   # |d| = 1 — a boundary wobble
        _ev("b", 1), _ev("b", 4),   # |d| = 3 — a Keep/Cut flip
        _ev("c", 2), _ev("c", 2),   # agreement
    ])
    assert out["pairs"] == 3
    assert out["agreements"] == 1
    assert out["adjacent"] == 1
    assert out["distant"] == 1
    assert out["rate"] == pytest.approx(1 / 3)
    assert out["rate_within_1"] == pytest.approx(2 / 3)


def test_self_agreement_returns_counts_alongside_every_rate():
    """A client cannot render a percentage from this without also holding the n
    it came from, which is the reason for the shape."""
    out = self_agreement([_ev("a", 4), _ev("a", 4)])
    for rate_key, num, den in (
        ("rate", "agreements", "pairs"),
        ("singleton_rate", "singleton_agreements", "singleton_pairs"),
        ("first_last_rate", "first_last_agreements", "first_last_pairs"),
    ):
        assert rate_key in out and num in out and den in out
