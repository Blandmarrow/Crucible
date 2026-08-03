"""Aggregate reads over the keep/cut rating corpus, pooled across every dataset.

**The division of labour, stated so nobody tidies it away: `images.py` owns
writes to image rows; `rating.py` owns aggregate reads over the rating corpus.**
`POST /images/bulk-rating` does not move here — it shares `_apply_bulk_filters`
and the `ensure_not_busy` loop with five sibling bulk endpoints, and splitting it
off would fork that scope logic.

**Not an extension of `quality.py`.** That router owns the *measurement*
subsystem: model-loading jobs, `_JOB_SCORE_COLUMNS`, duplicates, style
similarity. A rating is deliberately not a score —
`test_the_score_universe_is_the_ten_suffixed_columns` pins that — and every
`quality.py` GET is `/{dataset_id}`-scoped, while both routes here pool across
datasets by design, because a head trained from pooled labels cannot live under
one dataset. `quality.py` is also 990 lines with a split seam already recorded in
`docs/dev/pending-splits.md`.

Both routes are parameterless GETs following `GET /quality/aesthetic-coverage/{id}`'s
shape. **An empty corpus returns 200 with zeros and nulls, never a 404** — the
caller is a page that must render on day one, not a navigation.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.ml.rating_metrics import (
    RATING_TIERS,
    ordering_auc,
    self_agreement,
    spearman,
    spearman_ceiling,
)
from backend.models import Image, ImageRatingEvent
from backend.schemas.rating import (
    BoundaryAgreementOut,
    RatingEventsOut,
    RatingSummaryOut,
    ScorerAgreementOut,
    ScorerModelAgreementOut,
    SelfAgreementOut,
)
from backend.utils import chunked

router = APIRouter(prefix="/rating", tags=["rating"])


@router.get("/summary", response_model=RatingSummaryOut)
async def rating_summary(db: AsyncSession = Depends(get_db)):
    """Corpus counts plus the self-agreement ceiling.

    The ceiling is the number that decides whether a learned head is worth
    building: a head at 84% when you agree with yourself 87% of the time is *at
    ceiling*, and more labeling buys nothing. Without it, 84% reads as "needs
    work" forever.

    **The day-one figure is a diagnostic, not a measurement.** Every backfilled
    rating got exactly one event, so the ≥2-events population starts empty and
    the page says "not enough re-ratings yet". Nothing can change that — the
    information does not exist. The three biases that qualify the number even once
    it is populated (selection, anchoring, bulk sweep) are named in
    `rating_metrics.self_agreement`, which is why this returns counts and lets the
    page decide whether it has earned a percentage.

    The working set is bounded by how often the user has changed their mind: one
    `GROUP BY image_id HAVING COUNT(*) > 1` over the covering index finds the
    repeat images, and only those images' events are pulled.
    """
    total, rated, stale = (await db.execute(
        select(
            func.count(Image.id),
            func.count(Image.aesthetic_rating),
            # `sum(case(...))` rather than a FILTER clause, matching
            # `versioning.py` and `dataset_service.py`: the same shape everywhere,
            # and no dependence on the runtime SQLite's FILTER support.
            func.coalesce(func.sum(case((Image.rating_stale.is_(True), 1), else_=0)), 0),
        )
    )).one()

    tier_rows = (await db.execute(
        select(Image.aesthetic_rating, func.count(Image.id))
        .where(Image.aesthetic_rating.isnot(None))
        .group_by(Image.aesthetic_rating)
    )).all()
    by_rating = {str(t): 0 for t in RATING_TIERS}
    for tier, count in tier_rows:
        by_rating[str(tier)] = count

    events_total, images_with_events = (await db.execute(
        select(
            func.count(ImageRatingEvent.id),
            func.count(func.distinct(ImageRatingEvent.image_id)),
        )
    )).one()

    repeat_ids = [r[0] for r in (await db.execute(
        select(ImageRatingEvent.image_id)
        .group_by(ImageRatingEvent.image_id)
        .having(func.count(ImageRatingEvent.id) > 1)
    )).all()]

    triples: list[tuple[str, int | None, int | None]] = []
    for batch in chunked(repeat_ids):
        rows = (await db.execute(
            select(
                ImageRatingEvent.image_id,
                ImageRatingEvent.rating,
                ImageRatingEvent.batch_size,
            )
            .where(ImageRatingEvent.image_id.in_(list(batch)))
            # (image_id, id), matching the index and the ordering
            # `self_agreement` documents it requires. Never `created_at`: a bulk
            # write stamps one timestamp across its whole batch.
            .order_by(ImageRatingEvent.image_id, ImageRatingEvent.id)
        )).all()
        triples.extend((r[0], r[1], r[2]) for r in rows)

    agreement = self_agreement(triples)

    return RatingSummaryOut(
        total=total,
        rated=rated,
        unrated=total - rated,
        rating_stale=stale,
        by_rating=by_rating,
        events=RatingEventsOut(
            total=events_total,
            images_with_events=images_with_events,
            images_with_repeats=len(repeat_ids),
        ),
        self_agreement=SelfAgreementOut(**agreement),
    )


@router.get("/scorer-agreement", response_model=ScorerAgreementOut)
async def scorer_agreement(db: AsyncSession = Depends(get_db)):
    """Does an existing aesthetic scorer already track the user's taste?

    If LAION or V2.5 already correlates with the ratings near the self-agreement
    ceiling, a learned head is answered before it is built.

    **Grouped by `aesthetic_model`**, because LAION's sac+logos+ava1 scores and
    V2.5's SigLIP scores are not comparable — pooling them would measure the mix.
    Migration `a5e1b7c3d9f0`'s backfill established `aesthetic_score IS NOT NULL
    ⟺ aesthetic_model IS NOT NULL`, but **nothing enforces it**, so a `None`
    marker gets its **own bucket** rather than being skipped: bucketing keeps
    `sum(m.n) == scored_and_rated` true, and a test asserts both. (Precedent:
    `dataset_service.py`'s and `export_service.py`'s unknown-marker buckets.)

    Selects **columns, not entities** (`quality.py`'s rule at its paginated
    scorer): four scalars per row, and an ORM entity here would drag every
    deferred blob behind it. Unpaginated for the same reason —
    `_score_all_layers_paginated` (`routers/quality.py`) is the escape hatch if a
    corpus ever makes four scalar columns too much to hold.
    """
    rated = (await db.execute(
        select(func.count(Image.id)).where(Image.aesthetic_rating.isnot(None))
    )).scalar_one()

    rows = (await db.execute(
        select(Image.aesthetic_model, Image.aesthetic_score, Image.aesthetic_rating)
        .where(Image.aesthetic_rating.isnot(None), Image.aesthetic_score.isnot(None))
    )).all()

    # `None` is a valid dict key: a scored row whose marker was never written
    # groups together rather than crashing the response model.
    by_model: dict[str | None, list[tuple[float, int]]] = {}
    for marker, score, tier in rows:
        by_model.setdefault(marker, []).append((float(score), int(tier)))

    models: list[ScorerModelAgreementOut] = []
    for marker, pairs in by_model.items():
        scores = [p[0] for p in pairs]
        tiers = [p[1] for p in pairs]

        by_tier: dict[int, list[float]] = {t: [] for t in RATING_TIERS}
        for s, t in pairs:
            by_tier.setdefault(t, []).append(s)

        boundaries = [
            BoundaryAgreementOut(
                boundary=f"{lo}v{hi}",
                n_lo=len(by_tier[lo]),
                n_hi=len(by_tier[hi]),
                auc=ordering_auc(by_tier[lo], by_tier[hi]),
            )
            for lo, hi in zip(RATING_TIERS, RATING_TIERS[1:])
        ]

        models.append(ScorerModelAgreementOut(
            model=marker,
            n=len(pairs),
            spearman=spearman(scores, tiers),
            spearman_ceiling=spearman_ceiling(scores, tiers),
            mean_by_rating={
                str(t): (sum(v) / len(v) if v else None) for t, v in sorted(by_tier.items())
            },
            n_by_rating={str(t): len(v) for t, v in sorted(by_tier.items())},
            boundaries=boundaries,
        ))

    models.sort(key=lambda m: m.n, reverse=True)

    return ScorerAgreementOut(
        rated=rated,
        scored_and_rated=len(rows),
        rated_unscored=rated - len(rows),
        models=models,
    )
