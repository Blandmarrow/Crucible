"""`GET /rating/summary` and `GET /rating/scorer-agreement`.

The metrics themselves are unit-tested in `test_rating_metrics.py`; what is here
is the endpoints' own contract, and it is mostly about *not failing on day one*.
Both routes back a page that must render against an empty corpus, a corpus with
no re-ratings, and a corpus where everything is rated the same — so each returns
200 with zeros and nulls rather than a 404 or a NaN that fails serialisation.

The one invariant test worth its weight is `sum(m.n) == scored_and_rated`. It
holds by construction rather than by luck: a scored row whose `aesthetic_model`
was never written gets its **own bucket** keyed `None`, because skipping it would
make the per-model breakdown silently stop accounting for part of the corpus.
Migration `a5e1b7c3d9f0` backfilled the marker for every row that existed then;
nothing enforces it for rows written since.
"""
from backend.models import Image
from backend.tests.conftest import API, api_env, run


def _mk(dataset_id: str, i: int, **kw) -> Image:
    return Image(
        dataset_id=dataset_id,
        filename=f"img_{i:03d}.png",
        original_filename=f"img_{i:03d}.png",
        file_path=f"/tmp/{dataset_id}/img_{i:03d}.png",
        **kw,
    )


async def _add(env, rows):
    async with env.Session() as db:
        db.add_all(rows)
        await db.commit()


async def _rate(env, dataset_id: str, ids: list[str], rating: int | None):
    return await env.client.post(
        f"{API}/images/bulk-rating",
        json={"dataset_id": dataset_id, "image_ids": ids, "rating": rating},
    )


# --- summary ---------------------------------------------------------------


def test_summary_on_an_empty_corpus_is_200_with_zeros_and_nulls(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.get(f"{API}/rating/summary")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["total"] == 0 and body["rated"] == 0 and body["unrated"] == 0
            assert body["rating_stale"] == 0
            assert body["by_rating"] == {"1": 0, "2": 0, "3": 0, "4": 0}
            assert body["events"] == {
                "total": 0, "images_with_events": 0, "images_with_repeats": 0
            }
            assert body["self_agreement"]["pairs"] == 0
            assert body["self_agreement"]["rate"] is None

    run(scenario())


def test_summary_counts_the_corpus_and_the_tier_distribution(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await _add(env, [
                _mk(ds["id"], 0, aesthetic_rating=4),
                _mk(ds["id"], 1, aesthetic_rating=4),
                _mk(ds["id"], 2, aesthetic_rating=1),
                _mk(ds["id"], 3),                       # unrated
                _mk(ds["id"], 4, aesthetic_rating=2, rating_stale=True),
            ])

            body = (await env.client.get(f"{API}/rating/summary")).json()
            assert body["total"] == 5
            assert body["rated"] == 4
            assert body["unrated"] == 1
            assert body["rating_stale"] == 1
            # Every tier key is present even at zero, so the page's four bars need
            # no defaulting.
            assert body["by_rating"] == {"1": 1, "2": 1, "3": 0, "4": 2}

    run(scenario())


def test_summary_pools_across_datasets(tmp_path):
    """A head trained from pooled labels cannot live under one dataset, which is
    why neither route takes a `dataset_id`."""
    async def scenario():
        async with api_env(tmp_path) as env:
            a = await env.create_dataset("a")
            b = await env.create_dataset("b")
            await _add(env, [
                _mk(a["id"], 0, aesthetic_rating=4),
                _mk(b["id"], 1, aesthetic_rating=1),
            ])
            body = (await env.client.get(f"{API}/rating/summary")).json()
            assert body["rated"] == 2

    run(scenario())


def test_summary_reports_no_agreement_rate_until_something_is_re_rated(tmp_path):
    """The day-one state, and the one the page must render honestly: events exist,
    but no image has two of them, so there is nothing to agree with."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            rows = [_mk(ds["id"], i) for i in range(3)]
            await _add(env, rows)
            ids = [r.id for r in rows]

            await _rate(env, ds["id"], ids, 4)

            body = (await env.client.get(f"{API}/rating/summary")).json()
            assert body["events"]["total"] == 3
            assert body["events"]["images_with_events"] == 3
            assert body["events"]["images_with_repeats"] == 0
            assert body["self_agreement"]["pairs"] == 0
            assert body["self_agreement"]["rate"] is None

    run(scenario())


def test_summary_computes_agreement_over_real_re_ratings(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            rows = [_mk(ds["id"], i) for i in range(2)]
            await _add(env, rows)
            a, b = rows[0].id, rows[1].id

            await _rate(env, ds["id"], [a], 4)
            await _rate(env, ds["id"], [a], 4)   # agreed with itself
            await _rate(env, ds["id"], [b], 1)
            await _rate(env, ds["id"], [b], 3)   # changed its mind by 2

            sa = (await env.client.get(f"{API}/rating/summary")).json()["self_agreement"]
            assert sa["images_with_repeats"] == 2
            assert sa["pairs"] == 2
            assert sa["agreements"] == 1
            assert sa["rate"] == 0.5
            # Both writes touched one image each, so the honest subset is the
            # whole population here.
            assert sa["singleton_pairs"] == 2 and sa["bulk_pairs"] == 0
            assert sa["distant"] == 1 and sa["adjacent"] == 0

    run(scenario())


def test_summary_separates_bulk_pairs_from_singleton_pairs(tmp_path):
    """The bulk-sweep bias made visible: a select-all sweep followed by a second
    one agrees perfectly and says nothing about the human's consistency."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            rows = [_mk(ds["id"], i) for i in range(4)]
            await _add(env, rows)
            ids = [r.id for r in rows]

            await _rate(env, ds["id"], ids, 1)   # sweep
            await _rate(env, ds["id"], ids, 1)   # sweep again

            sa = (await env.client.get(f"{API}/rating/summary")).json()["self_agreement"]
            assert sa["pairs"] == 4 and sa["agreements"] == 4
            assert sa["singleton_pairs"] == 0
            assert sa["bulk_pairs"] == 4

    run(scenario())


def test_a_three_event_history_is_read_in_write_order_not_sorted_by_rating(tmp_path):
    """The `ORDER BY … ImageRatingEvent.id` in `rating_summary`, pinned.

    Ordering by `rating` instead turns `1 → 4 → 1` into `1 → 1 → 4`. **Two
    events cannot catch it** — every figure `self_agreement` reports is symmetric
    in a single pair — so three is the minimum, and the witnesses move in
    opposite directions:

    | history as read     | pairs | agreements | distant | first_last_agreements |
    |---------------------|-------|------------|---------|-----------------------|
    | 1 → 4 → 1 (by id)   | 2     | 0          | 2       | 1                     |
    | 1 → 1 → 4 (by rating)| 2    | 1          | 1       | 0                     |

    All three are asserted together: any one alone could be matched by accident.
    Every write is a one-image list, so `batch_size` is constant and cannot
    introduce a nondeterministic tie-break.

    HTTP level, because the ordering being pinned is the router's — a unit test
    of `self_agreement` supplies the order itself and so cannot see it. The
    `id`-rather-than-`created_at` half of the rule is **not testable at any
    layer**: one write produces at most one event per image, so within an image's
    history timestamps never tie, and the ties the rule guards against are
    cross-image ones that grouping removes before ordering matters.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            rows = [_mk(ds["id"], 0)]
            await _add(env, rows)
            one = [rows[0].id]

            await _rate(env, ds["id"], one, 1)
            await _rate(env, ds["id"], one, 4)
            await _rate(env, ds["id"], one, 1)

            sa = (await env.client.get(f"{API}/rating/summary")).json()["self_agreement"]
            assert sa["pairs"] == 2
            assert sa["agreements"] == 0            # 1 → 1 → 4 would report 1
            assert sa["distant"] == 2               # ...and 1
            assert sa["first_last_agreements"] == 1  # ...and 0

    run(scenario())


# --- scorer-agreement ------------------------------------------------------


def test_scorer_agreement_on_an_empty_corpus_is_200_with_no_models(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.get(f"{API}/rating/scorer-agreement")
            assert r.status_code == 200, r.text
            assert r.json() == {
                "rated": 0, "scored_and_rated": 0, "rated_unscored": 0, "models": []
            }

    run(scenario())


def test_scorer_agreement_groups_by_model_and_accounts_for_every_scored_row(tmp_path):
    """`sum(m.n) == scored_and_rated` — the invariant bucketing (rather than
    skipping) an unknown marker preserves; the unmarked case has its own test."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            rows = []
            for i in range(8):
                rows.append(_mk(
                    ds["id"], i,
                    aesthetic_rating=(i % 4) + 1,
                    aesthetic_score=float(i),
                    aesthetic_model="laion" if i < 5 else "v2_5",
                ))
            await _add(env, rows)

            body = (await env.client.get(f"{API}/rating/scorer-agreement")).json()
            assert body["scored_and_rated"] == 8
            assert sum(m["n"] for m in body["models"]) == body["scored_and_rated"]
            # Ordered by n desc, and a list rather than a dict so a future
            # `head:{uuid}` producer needs no schema change.
            assert [m["model"] for m in body["models"]] == ["laion", "v2_5"]

            laion = body["models"][0]
            assert set(laion["mean_by_rating"]) == {"1", "2", "3", "4"}
            # Every mean arrives with the count behind it, and they account for
            # the model's whole population.
            assert sum(laion["n_by_rating"].values()) == laion["n"]
            assert [b["boundary"] for b in laion["boundaries"]] == ["1v2", "2v3", "3v4"]

    run(scenario())


def test_a_scored_row_with_no_model_marker_gets_its_own_bucket_not_a_500(tmp_path):
    """Nothing *enforces* `aesthetic_score IS NOT NULL ⟺ aesthetic_model IS NOT
    NULL` — the migration established it; a writer that skips the marker breaks
    it again, and `bench_scaling.py` did exactly that. A `None` marker fed into a
    `model: str` field raised a `ValidationError` **inside the handler**, so the
    page whose stated job is rendering whatever day one looks like answered 500.

    Bucketed rather than skipped, because skipping would silently break
    `sum(m.n) == scored_and_rated` — asserted here as well as above.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await _add(env, [
                _mk(ds["id"], 0, aesthetic_rating=4, aesthetic_score=0.9,
                    aesthetic_model="laion"),
                _mk(ds["id"], 1, aesthetic_rating=2, aesthetic_score=0.2,
                    aesthetic_model="laion"),
                # Scored, rated, unmarked.
                _mk(ds["id"], 2, aesthetic_rating=1, aesthetic_score=0.4),
            ])

            r = await env.client.get(f"{API}/rating/scorer-agreement")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["scored_and_rated"] == 3
            assert sum(m["n"] for m in body["models"]) == body["scored_and_rated"]
            unmarked = [m for m in body["models"] if m["model"] is None]
            assert len(unmarked) == 1 and unmarked[0]["n"] == 1

    run(scenario())


def test_a_boundary_with_one_image_a_side_still_reports_its_auc_and_its_counts(tmp_path):
    """The division of labour between the module and the page, pinned.

    `ordering_auc`'s `None` means *undefined* — a side with nobody in it. "One a
    side" is defined and reported, counts and all; it is the **page** that
    refuses to print a figure below its own `MIN_BOUNDARY_PER_SIDE`, because
    folding the two into one token would throw the counts away with it. This is
    what stops a later sweep moving the floor down here.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await _add(env, [
                # 1v2 has one image a side; 3 and 4 stay empty.
                _mk(ds["id"], 0, aesthetic_rating=1, aesthetic_score=0.1,
                    aesthetic_model="laion"),
                _mk(ds["id"], 1, aesthetic_rating=2, aesthetic_score=0.9,
                    aesthetic_model="laion"),
            ])

            model = (await env.client.get(
                f"{API}/rating/scorer-agreement"
            )).json()["models"][0]
            by_boundary = {b["boundary"]: b for b in model["boundaries"]}

            assert by_boundary["1v2"]["auc"] == 1.0
            assert by_boundary["1v2"]["n_lo"] == 1 and by_boundary["1v2"]["n_hi"] == 1
            # An empty side is the undefined case, and *that* is the module's None.
            assert by_boundary["3v4"]["auc"] is None
            assert by_boundary["3v4"]["n_lo"] == 0 and by_boundary["3v4"]["n_hi"] == 0

    run(scenario())


def test_a_rated_but_unscored_image_is_excluded_from_rho_but_counted(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await _add(env, [
                _mk(ds["id"], 0, aesthetic_rating=4, aesthetic_score=0.9, aesthetic_model="laion"),
                _mk(ds["id"], 1, aesthetic_rating=2, aesthetic_score=0.2, aesthetic_model="laion"),
                _mk(ds["id"], 2, aesthetic_rating=1),                  # rated, unscored
                _mk(ds["id"], 3, aesthetic_score=0.5, aesthetic_model="laion"),  # scored, unrated
            ])

            body = (await env.client.get(f"{API}/rating/scorer-agreement")).json()
            assert body["rated"] == 3
            assert body["scored_and_rated"] == 2
            assert body["rated_unscored"] == 1
            assert body["models"][0]["n"] == 2

    run(scenario())


def test_a_single_tier_corpus_returns_null_rho_not_nan_or_a_500(tmp_path):
    """Forty images all rated Cut is a real early state. Zero variance makes ρ
    undefined, and NaN would fail JSON serialisation rather than show as a gap."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await _add(env, [
                _mk(ds["id"], i, aesthetic_rating=1, aesthetic_score=float(i),
                    aesthetic_model="laion")
                for i in range(5)
            ])

            r = await env.client.get(f"{API}/rating/scorer-agreement")
            assert r.status_code == 200, r.text
            model = r.json()["models"][0]
            assert model["n"] == 5
            assert model["spearman"] is None
            assert model["spearman_ceiling"] is None
            # Tiers with nobody in them report None rather than 0.0, which would
            # read as "the scorer rates them at zero".
            assert model["mean_by_rating"]["1"] is not None
            assert model["mean_by_rating"]["4"] is None
            # Every boundary is empty on one side, so no AUC is claimed.
            assert all(b["auc"] is None for b in model["boundaries"])

    run(scenario())


def test_a_perfectly_ordered_scorer_reports_rho_at_its_ceiling(tmp_path):
    """The pair of numbers the panel exists to show together: with a four-tier
    target the ceiling is below 1.0, so a scorer that gets the ordering exactly
    right still does not read 1.00."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            rows = []
            for i in range(12):
                tier = (i // 3) + 1
                rows.append(_mk(
                    ds["id"], i,
                    aesthetic_rating=tier, aesthetic_score=float(i),
                    aesthetic_model="laion",
                ))
            await _add(env, rows)

            model = (await env.client.get(
                f"{API}/rating/scorer-agreement"
            )).json()["models"][0]
            assert model["spearman"] == model["spearman_ceiling"]
            assert model["spearman_ceiling"] < 1.0
            assert all(b["auc"] == 1.0 for b in model["boundaries"])
            means = model["mean_by_rating"]
            assert means["1"] < means["2"] < means["3"] < means["4"]

    run(scenario())
