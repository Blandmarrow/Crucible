"""Response shapes for `GET /rating/*`.

Both endpoints answer the same question from two sides — *is a learned aesthetic
head worth building* — and both are shaped so a client cannot render the headline
number without the caveat that qualifies it. Every rate is nullable and arrives
beside the counts it came from; below a floor the page shows the counts and no
percentage at all, because a ceiling computed from three pairs is noise wearing a
number.
"""
from pydantic import BaseModel


class SelfAgreementOut(BaseModel):
    """How often the same image got the same rating twice.

    Read `backend/ml/rating_metrics.py::self_agreement` for what each field
    counts. The short version: `pairs` are **consecutive** events per image, not
    first-versus-last (which scores 1 → 4 → 1 as perfect agreement);
    `singleton_*` restricts to pairs where **both** writes touched one image,
    which is the only subset the bulk-sweep bias cannot inflate; `cleared_pairs`
    are excluded from `pairs` entirely, because withdrawing a judgement is not a
    second opinion.
    """

    images_with_repeats: int
    pairs: int
    agreements: int
    rate: float | None
    singleton_pairs: int
    singleton_agreements: int
    singleton_rate: float | None
    bulk_pairs: int
    cleared_pairs: int
    # |Δ| = 1 versus |Δ| ≥ 2. A boundary wobble is not a Keep↔Cut flip.
    adjacent: int
    distant: int
    rate_within_1: float | None
    first_last_pairs: int
    first_last_agreements: int
    first_last_rate: float | None


class RatingEventsOut(BaseModel):
    total: int
    images_with_events: int
    images_with_repeats: int


class RatingSummaryOut(BaseModel):
    total: int
    rated: int
    unrated: int
    # Rated images whose pixels were rewritten since the rating was given, so the
    # judgement is about pixels that no longer exist.
    rating_stale: int
    # Keyed "1".."4" — JSON object keys are strings.
    by_rating: dict[str, int]
    events: RatingEventsOut
    self_agreement: SelfAgreementOut


class BoundaryAgreementOut(BaseModel):
    """One adjacent-tier boundary, e.g. Probably-not vs Probably.

    `auc` is P(a randomly drawn image from the upper tier scores above one from
    the lower), ties counted as 0.5. 0.5 is a coin flip, which is why the page
    draws its bar centred there rather than from zero.
    """

    boundary: str
    n_lo: int
    n_hi: int
    auc: float | None


class ScorerModelAgreementOut(BaseModel):
    # Nullable because nothing *enforces* `aesthetic_score IS NOT NULL ⟺
    # aesthetic_model IS NOT NULL` — the migration's backfill established it, and
    # any writer that sets a score without the marker breaks it again. A `None`
    # here means "scored by a writer that skipped the marker", **not** a model
    # named "unknown": the bucket is real and its `n` is counted, so
    # `sum(m.n) == scored_and_rated` holds. Skipping the row instead would break
    # that identity silently.
    model: str | None
    n: int
    spearman: float | None
    # The largest ρ *any* scorer could reach against this tier distribution. With
    # a four-level target the tie structure caps ρ below 1.0, so a bare 0.31 reads
    # as failure forever; "0.31 of a possible 0.97" is the honest form.
    spearman_ceiling: float | None
    # The most legible thing on the page: four flat tier means mean the scorer
    # knows nothing about your taste, and anyone can read four numbers.
    mean_by_rating: dict[str, float | None]
    # The count behind each of those means, on the wire for the same reason
    # `n_lo`/`n_hi` are: a mean over one image is indistinguishable from a mean
    # over two hundred, and it is not derivable from `boundaries` except by an
    # index trick that breaks the moment a boundary changes.
    n_by_rating: dict[str, int]
    boundaries: list[BoundaryAgreementOut]


class ScorerAgreementOut(BaseModel):
    rated: int
    scored_and_rated: int
    rated_unscored: int
    # A **list ordered by `n` desc**, not a dict keyed by marker, so a future
    # `head:{uuid}` producer lands in it without a schema change — matching what
    # `aestheticModelLabel` already says about unknown markers.
    models: list[ScorerModelAgreementOut]
