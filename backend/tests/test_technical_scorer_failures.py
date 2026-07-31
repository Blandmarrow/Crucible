"""What the technical scorer records for a file it could not measure: nothing.

Both failure branches — `cv2.imread` returning None, and an exception out of the
executor — used to write 0.0 into all six score columns. A zero is a
*measurement*: it says the image is pitch black, perfectly uniform, fully
desaturated and (via `is_blurry=True`) out of focus, about a file no decoder ever
read. The columns are nullable precisely so "not measured" has its own value.

The three booleans stay False in both branches. `routers/quality.py` folds them
into a JSON flags dict via `t.get(..., False)`, so None is not expressible there
— and claiming `is_blurry` from a measurement never taken is the same defect in
a different column.
"""

from backend.ml.technical_scorer import score_images_technical, score_technical_sync
from backend.tests.conftest import needs_cv2, png_bytes, run

SCORE_KEYS = (
    "blur_score", "noise_score", "uniformity_score",
    "color_score", "saturation_score", "luminance_score",
)
FLAG_KEYS = ("is_blurry", "is_noisy", "is_uniform")


def _assert_unmeasured(scores: dict) -> None:
    for key in SCORE_KEYS:
        assert scores[key] is None, f"{key} was measured as {scores[key]!r}"
    for key in FLAG_KEYS:
        assert scores[key] is False, f"{key} was claimed from no measurement"


@needs_cv2
def test_an_unreadable_file_records_no_scores(tmp_path):
    """`cv2.imread` returns None for a path that is not a decodable image; the
    old branch answered with `is_blurry=True, is_uniform=True` and six zeros."""
    p = tmp_path / "not-an-image.png"
    p.write_bytes(b"this is not a PNG")

    _assert_unmeasured(score_technical_sync(str(p)))


@needs_cv2
def test_a_missing_file_records_no_scores(tmp_path):
    _assert_unmeasured(score_technical_sync(str(tmp_path / "gone.png")))


@needs_cv2
def test_a_readable_file_still_records_every_score(tmp_path):
    """The guard against over-reach: a file that *does* decode is unaffected."""
    p = tmp_path / "ok.png"
    p.write_bytes(png_bytes())

    scores = score_technical_sync(str(p))
    for key in SCORE_KEYS:
        assert isinstance(scores[key], float), f"{key} was {scores[key]!r}"


def test_an_exception_out_of_the_executor_records_no_scores(tmp_path, monkeypatch):
    """The batch driver's own failure branch, which had the same six zeros.

    No cv2 needed: the scorer is replaced by one that raises before it would
    import anything.
    """
    import backend.ml.technical_scorer as ts

    def boom(*a, **k):
        raise RuntimeError("decoder exploded")

    monkeypatch.setattr(ts, "score_technical_sync", boom)

    async def scenario():
        return await score_images_technical(["id-1"], [str(tmp_path / "a.png")])

    results = run(scenario())
    assert len(results) == 1
    _assert_unmeasured(results[0])


def test_the_failure_contract_covers_every_field_the_scorer_writes():
    """One contract, two sites. They drifted before — the unreadable branch
    claimed `is_blurry=True` while the exception branch said False. A score added
    to `score_technical_sync` without a matching key here fails this."""
    import backend.ml.technical_scorer as ts

    assert set(ts._unmeasured()) == set(SCORE_KEYS) | set(FLAG_KEYS)
