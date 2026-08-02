"""The aesthetic, watermark and NSFW scorers record nothing for what they could
not measure — the same contract `test_technical_scorer_failures.py` holds for the
technical scorer, which is where it was written down first.

All three used to write `0.0` on an exception out of the executor, and the
watermark branch logged nothing at all, so an entire run could fail silently and
leave a dataset that reports full coverage. A zero is a *measurement*: it said
the image was confidently safe (NSFW), confidently watermark-free, and — worst of
the three — scored 0.0 on a column whose range is 1–10, which sorted it to the
bottom of every "worst first" view as though a model had judged it.

The booleans stay False rather than None because `routers/quality.py` folds them
into a JSON flags dict where None is not expressible; an unmeasured image is
simply not flagged.

No model weights needed: each `*_sync` scorer is replaced by one that raises
before it would load anything. The scorer *modules* still `import torch` at the
top, though, which CI never has — hence `needs_torch` on the four that import one.
The fifth asserts on the ORM alone and is the only part of this contract CI
enforces; the rest run on any machine with the real venv.
"""

from backend.tests.conftest import needs_torch, run


@needs_torch
def test_an_aesthetic_failure_records_no_score(monkeypatch):
    import backend.ml.aesthetic_scorer as aes

    def boom(*a, **k):
        raise RuntimeError("CLIP exploded")

    monkeypatch.setattr(aes, "score_image_sync", boom)

    async def scenario():
        return await aes.score_images_batch(["/nope/a.png", "/nope/b.png"], {})

    scores = run(scenario())
    assert scores == [None, None], "0.0 is outside the column's 1-10 range"


@needs_torch
def test_a_v2_5_failure_records_no_score_either(monkeypatch):
    """The contract belongs to the *loop*, not to the model — which is the whole
    reason `score_images_batch` takes a `model` parameter instead of V2.5 getting
    a loop of its own.

    A second copy would be a second place for the None-on-failure contract, the
    SSE cadence and the `cancel_requested` check to drift, and this repo has
    already had to fix each of those once. Patched on the V2.5 module because the
    branch imports it at call time.
    """
    import backend.ml.aesthetic_scorer as aes
    import backend.ml.aesthetic_v2_5_scorer as v25

    def boom(*a, **k):
        raise RuntimeError("SigLIP exploded")

    monkeypatch.setattr(v25, "score_image_v2_5_sync", boom)

    async def scenario():
        return await aes.score_images_batch(["/nope/a.png", "/nope/b.png"], None, model="v2_5")

    scores = run(scenario())
    assert scores == [None, None], "0.0 is outside the column's 1-10 range"


@needs_torch
def test_a_watermark_failure_records_no_score_and_no_flag(monkeypatch):
    import backend.ml.aesthetic_scorer as aes

    def boom(*a, **k):
        raise RuntimeError("CLIP exploded")

    monkeypatch.setattr(aes, "score_watermark_sync", boom)
    monkeypatch.setattr(aes, "_precompute_watermark_text_features", lambda *a, **k: None)

    async def scenario():
        return await aes.score_images_watermark(["/nope/a.png"], {})

    results = run(scenario())
    assert results == [{"watermark_score": None, "has_watermark": False}]


@needs_torch
def test_a_watermark_failure_is_logged(monkeypatch, caplog):
    """This branch was silent — the one property that made the defect invisible
    in the field rather than merely wrong."""
    import logging

    import backend.ml.aesthetic_scorer as aes

    def boom(*a, **k):
        raise RuntimeError("CLIP exploded")

    monkeypatch.setattr(aes, "score_watermark_sync", boom)
    monkeypatch.setattr(aes, "_precompute_watermark_text_features", lambda *a, **k: None)

    async def scenario():
        return await aes.score_images_watermark(["/nope/a.png"], {})

    with caplog.at_level(logging.WARNING, logger="backend.ml.aesthetic_scorer"):
        run(scenario())
    assert any("/nope/a.png" in r.getMessage() for r in caplog.records)


@needs_torch
def test_an_nsfw_failure_records_no_score_and_is_not_flagged(monkeypatch):
    import backend.ml.nsfw_scorer as nsfw

    def boom(*a, **k):
        raise RuntimeError("classifier exploded")

    monkeypatch.setattr(nsfw, "score_image_sync", boom)

    async def scenario():
        return await nsfw.score_images_nsfw_batch(["/nope/a.png"], {}, threshold=0.5)

    results = run(scenario())
    assert results == [{"nsfw_score": None, "is_nsfw": False}]


def test_the_quality_router_writes_those_nulls_straight_through():
    """The three columns are nullable and the router assigns the scorer's value
    without a default, so NULL reaches the DB rather than becoming 0.0 again."""
    from backend.models.image import Image

    for col in ("aesthetic_score", "watermark_score", "nsfw_score"):
        assert Image.__table__.c[col].nullable, f"{col} cannot hold 'not measured'"
