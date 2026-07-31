"""`Image.scores_stale` — set by every in-place pixel rewrite, cleared by a
scoring run that refreshes what the row carries.

Ten code paths overwrite an image's file and deliberately leave the ten
`*_score` columns and `quality_flags` alone. That was always the right call —
nothing recomputes a score, and silently zeroing them would be worse — but
before this column nothing recorded the fact anywhere durable, so the damage
surfaced weeks later at export, where `exclude_flags` drops images on flags
computed against pixels that no longer exist.

The bit is set only when the row actually carries a score: an image that has
never been scored has no measurement to invalidate, and marking it warned about
"scores measured on pixels that no longer exist" on the commonest workflow there
is (upload → resize → export). `processing_history` is written either way —
that divergence is deliberate and is asserted here.

Three kinds of test here:

* **HTTP round-trips** over the four in-place sites reachable without a decoder
  (batch/single resize, batch/single crop in replace mode), asserted from a
  fresh session so a bit set on a live ORM object but never committed fails.
* **A structural AST guard** that no eleventh site can hand-roll the history
  append and skip the bit. `record_in_place` being the single writer is what the
  whole design rests on, and PM-010 asked for exactly this enforcement and never
  got it.
* **The clear predicate**, driven with monkeypatched scorers — the real ones need
  torch, which CI will never have.

No cv2 and no torch: nothing here imports past the pure-numpy `backend/ml/`
modules, and the scoring tests stub the scorers at the module boundary rather
than importing them.
"""
import ast
from pathlib import Path

from sqlalchemy import select

from backend.models.image import Image
from backend.tests.conftest import (
    API,
    api_env,
    png_bytes,
    run,
    upload_image,
    wait_for_job,
)

BACKEND = Path(__file__).resolve().parent.parent


async def _fresh(env, image_id: str) -> Image:
    """Re-read the row on a new session — the point is what was *committed*."""
    async with env.Session() as db:
        return (await db.execute(select(Image).where(Image.id == image_id))).scalar_one()


async def _one_image(env, dataset_id: str, name: str = "a.png") -> dict:
    return await upload_image(env, dataset_id, name, png_bytes((10, 20, 30), (40, 20)))


# ---------------------------------------------------------------------------
# The set sites
# ---------------------------------------------------------------------------


def test_a_new_image_is_not_stale(tmp_path):
    """The column's default has to be False, or every freshly uploaded image
    wears the badge and it means nothing."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await _one_image(env, ds["id"])
            assert (await _fresh(env, img["id"])).scores_stale is False

    run(scenario())


def test_batch_resize_sets_and_commits_the_bit_on_a_scored_image(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            imgs = [await _one_image(env, ds["id"], "a.png"),
                    await _one_image(env, ds["id"], "b.png")]
            for i in imgs:
                await _seed_scores(env, i["id"], aesthetic_score=0.4)

            r = await env.client.post(
                f"{API}/images/batch/resize",
                json={"image_ids": [i["id"] for i in imgs], "width": 20, "height": 10,
                      "maintain_ar": False},
            )
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

            for i in imgs:
                row = await _fresh(env, i["id"])
                assert row.scores_stale is True, i["filename"]
                # The two columns move together, always — same writer.
                assert [h["op"] for h in row.processing_history] == ["resize"]

    run(scenario())


def test_batch_crop_sets_and_commits_the_bit_on_a_scored_image(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await _one_image(env, ds["id"])  # 40x20, AR 2.0
            await _seed_scores(env, img["id"], aesthetic_score=0.4)

            r = await env.client.post(
                f"{API}/images/batch/crop",
                json={"image_ids": [img["id"]], "target_ar": 1.0},
            )
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

            row = await _fresh(env, img["id"])
            assert row.scores_stale is True
            assert [h["op"] for h in row.processing_history] == ["crop_aspect"]

    run(scenario())


def test_single_resize_sets_and_commits_the_bit_on_a_scored_image(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await _one_image(env, ds["id"])
            await _seed_scores(env, img["id"], aesthetic_score=0.4)

            r = await env.client.post(
                f"{API}/images/{img['id']}/resize",
                json={"width": 20, "height": 10, "maintain_ar": False},
            )
            assert r.status_code == 200, r.text
            assert (await _fresh(env, img["id"])).scores_stale is True

    run(scenario())


def test_single_replace_crop_sets_and_commits_the_bit_on_a_scored_image(tmp_path):
    """`replace: true` only. The non-replace crop writes a *new* row, which
    correctly starts False — its pixels have never been scored."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await _one_image(env, ds["id"])
            await _seed_scores(env, img["id"], aesthetic_score=0.4)

            r = await env.client.post(
                f"{API}/images/{img['id']}/crop",
                json={"x": 0, "y": 0, "width": 20, "height": 20, "replace": True},
            )
            assert r.status_code == 200, r.text
            assert (await _fresh(env, img["id"])).scores_stale is True

    run(scenario())


def test_a_non_replace_crop_leaves_the_parent_alone_and_starts_the_child_fresh(tmp_path):
    """The derivative is a new image with no scores at all, so nothing about it
    is stale — and the parent's pixels were never touched."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await _one_image(env, ds["id"])

            r = await env.client.post(
                f"{API}/images/{img['id']}/crop",
                json={"x": 0, "y": 0, "width": 20, "height": 20, "replace": False},
            )
            assert r.status_code == 200, r.text
            child_id = r.json()["id"]
            assert child_id != img["id"]

            assert (await _fresh(env, img["id"])).scores_stale is False
            assert (await _fresh(env, child_id)).scores_stale is False

    run(scenario())


def test_rebuilding_thumbnails_does_not_set_the_bit(tmp_path):
    """`bulk_thumbnails` re-cuts previews and bumps `updated_at`; the image's own
    pixels are untouched, so its scores are still measurements of what is there."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await _one_image(env, ds["id"])

            r = await env.client.post(
                f"{API}/images/bulk-thumbnails", json={"dataset_id": ds["id"]}
            )
            assert r.status_code == 200, r.text
            if "job_id" in r.json():
                await wait_for_job(env, r.json()["job_id"])

            assert (await _fresh(env, img["id"])).scores_stale is False

    run(scenario())


# ---------------------------------------------------------------------------
# The structural guard
# ---------------------------------------------------------------------------


def _hand_rolled_history_appends() -> list[str]:
    """Every `<x>.processing_history = <something> + <something>` outside utils.

    The list-concat form is the append idiom: `img.processing_history =
    (img.processing_history or []) + [{...}]`. Matching only the `BinOp(Add)`
    shape is what excludes the legitimate plain copies — `restore_snapshot`'s
    `img.processing_history = state.processing_history` and
    `dataset_service`'s ctor kwargs — which carry a history rather than extend
    one and must not touch `scores_stale`.
    """
    hits: list[str] = []
    for path in sorted([*(BACKEND / "routers").glob("*.py"),
                        *(BACKEND / "services").glob("*.py")]):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.BinOp) or not isinstance(node.value.op, ast.Add):
                continue
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == "processing_history":
                    hits.append(f"{path.relative_to(BACKEND)}:{node.lineno}")
    return hits


def test_no_router_or_service_hand_rolls_a_processing_history_append():
    """`backend/utils.py::record_in_place` is the single writer of
    `processing_history` **and** `scores_stale`, and this is what enforces it.

    The convention existed before — PM-010 named it — but nothing checked, which
    is how four routers ended up with their own copies of the append and how a
    fifth would have silently recorded the edit while leaving the scores looking
    trustworthy. This fails CI for the *eleventh* in-place site.
    """
    hits = _hand_rolled_history_appends()
    assert not hits, (
        "these sites append to processing_history by hand instead of calling "
        f"backend.utils.record_in_place: {hits}. The helper writes scores_stale "
        "too, so a hand-rolled append silently leaves stale scores unmarked."
    )


def test_the_guard_can_see_a_hand_rolled_append():
    """A structural test that cannot fail is worse than no test. This pins the
    matcher against the exact source shape it is looking for."""
    tree = ast.parse(
        "img.processing_history = (img.processing_history or []) + [{'op': 'x'}]"
    )
    node = tree.body[0]
    assert isinstance(node, ast.Assign)
    assert isinstance(node.value, ast.BinOp) and isinstance(node.value.op, ast.Add)
    assert node.targets[0].attr == "processing_history"


def test_record_in_place_writes_both_columns_and_cannot_raise():
    """The helper's whole contract in one place: it sets the history entry and the
    timestamp unconditionally, sets the bit only when there is a score to
    invalidate, and is pure attribute access and assignment — which is what lets
    every caller put it between an irreversible `os.replace` and the commit that
    describes it, rather than in the post-commit epilogue (PM-013).

    Driven against a transient `Image()` rather than a stub, because the helper
    now reads the class's columns: no session is needed, since every attribute on
    an unflushed instance is an already-materialised `None`.
    """
    from backend.utils import record_in_place

    row = Image()
    # A loaded row carries the column's `False`; on a transient instance the
    # default only lands at flush, so set it here to model the real thing.
    row.scores_stale = False
    record_in_place(row, "lut", lut="warm.cube", intensity=0.5)
    # Unscored: the history entry is written, the bit is not. The two columns
    # deliberately diverge — history is the durable "these pixels were rewritten"
    # record and pass 2's skip guard; the bit qualifies a measurement.
    assert row.scores_stale is False
    assert len(row.processing_history) == 1
    entry = row.processing_history[0]
    assert entry["op"] == "lut"
    assert entry["lut"] == "warm.cube"
    assert entry["intensity"] == 0.5
    assert "at" in entry
    assert row.updated_at is not None

    # One score is enough, and the history keeps extending either way.
    # Reassignment, never .append() — SQLAlchemy compares JSON columns by
    # equality, so a mutated-in-place list looks unchanged and the UPDATE is
    # skipped (CLAUDE.md § Key invariants).
    first = row.processing_history
    row.blur_score = 50.0
    record_in_place(row, "resize", width=10, height=10)
    assert row.scores_stale is True
    assert row.processing_history is not first
    assert [e["op"] for e in row.processing_history] == ["lut", "resize"]


def test_an_in_place_edit_on_an_unscored_image_records_the_history_but_not_the_bit(tmp_path):
    """The headline of this fix, over a real endpoint.

    `scores_stale` says a *measurement* no longer describes the pixels. An image
    that has never been scored carries no measurement, so there is nothing to
    invalidate — marking it put an amber badge, a detail chip and an export
    warning reading "edited in place after being scored" on rows with no scores
    and no flags. `processing_history` still records the rewrite unconditionally:
    it is the durable record and re-extraction's skip guard, and that is the one
    place the two columns are meant to disagree.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await _one_image(env, ds["id"])

            r = await env.client.post(
                f"{API}/images/{img['id']}/resize",
                json={"width": 20, "height": 10, "maintain_ar": False},
            )
            assert r.status_code == 200, r.text

            row = await _fresh(env, img["id"])
            assert row.scores_stale is False
            assert [h["op"] for h in row.processing_history] == ["resize"]

    run(scenario())


def test_the_commonest_workflow_never_warns_at_export(tmp_path):
    """Upload → batch resize → export, with no scoring anywhere: the export
    preview must report nothing stale. This is the whole user-visible bug in one
    assertion."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            imgs = [await _one_image(env, ds["id"], "a.png"),
                    await _one_image(env, ds["id"], "b.png")]

            r = await env.client.post(
                f"{API}/images/batch/resize",
                json={"image_ids": [i["id"] for i in imgs], "width": 20, "height": 10,
                      "maintain_ar": False},
            )
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

            p = (await env.client.get(f"{API}/export/preview/{ds['id']}")).json()
            assert p["stale_scores_count"] == 0
            assert p["stale_scores_will_export"] == 0

    run(scenario())


def test_one_score_is_enough_and_style_similarity_counts(tmp_path):
    """The *set* universe is all ten `*_score` columns, `style_similarity_score`
    included — it is a measurement of those pixels like any other. (It is out of
    the *clear* universe, which is a different question; see
    `test_the_clear_universe_is_the_nine_scores_the_job_writes`.)"""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await _one_image(env, ds["id"])
            await _seed_scores(env, img["id"], style_similarity_score=0.9)

            r = await env.client.post(
                f"{API}/images/{img['id']}/resize",
                json={"width": 20, "height": 10, "maintain_ar": False},
            )
            assert r.status_code == 200, r.text
            assert (await _fresh(env, img["id"])).scores_stale is True

    run(scenario())


def test_quality_flags_alone_do_not_mark_a_row_stale(tmp_path):
    """Flags derive from scores, so a row with flags and no score is not a state
    the scoring job produces — and the clear predicate ignores flags for the same
    reason. The two ends stay symmetric."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await _one_image(env, ds["id"])
            async with env.Session() as db:
                row = await db.get(Image, img["id"])
                row.quality_flags = {"is_blurry": True}
                await db.commit()

            r = await env.client.post(
                f"{API}/images/{img['id']}/resize",
                json={"width": 20, "height": 10, "maintain_ar": False},
            )
            assert r.status_code == 200, r.text
            assert (await _fresh(env, img["id"])).scores_stale is False

    run(scenario())


def test_the_score_universe_is_the_ten_suffixed_columns():
    """`utils.score_columns` is suffix-derived, so this pins what the suffix
    currently catches — and, deliberately, what it must not: `dino_layer_scores`
    is plural (a JSON blob of embeddings, not a score) and `scores_stale` is the
    bit itself."""
    from backend.utils import score_columns

    assert score_columns(Image) == frozenset({
        "aesthetic_score", "blur_score", "noise_score", "uniformity_score",
        "watermark_score", "color_score", "saturation_score", "luminance_score",
        "style_similarity_score", "nsfw_score",
    })
    assert "dino_layer_scores" not in score_columns(Image)
    assert "scores_stale" not in score_columns(Image)


def test_the_clear_universe_is_the_nine_scores_the_job_writes():
    """`_JOB_SCORE_COLUMNS` is derived from the same source as the set site, minus
    the one column the job cannot refresh.

    An eleventh `*_score` column fails this test on purpose: derivation makes the
    default a *loud* failure (a permanently un-clearable bit) rather than the
    silent one a hand-written list gives (the badge vanishing while a score is
    stale), and this assertion turns even the loud one into a decision made at
    review time.
    """
    from backend.routers.quality import _JOB_SCORE_COLUMNS, _TECHNICAL_SCORE_COLUMNS
    from backend.utils import score_columns

    assert _JOB_SCORE_COLUMNS == score_columns(Image) - {"style_similarity_score"}
    assert _JOB_SCORE_COLUMNS == frozenset({
        "aesthetic_score", "blur_score", "noise_score", "uniformity_score",
        "watermark_score", "color_score", "saturation_score", "luminance_score",
        "nsfw_score",
    })
    # The technical block's literal names what one block writes; it still has to
    # be a subset of what the job as a whole can refresh.
    assert _TECHNICAL_SCORE_COLUMNS <= _JOB_SCORE_COLUMNS


def test_no_score_column_is_deferred():
    """`record_in_place` reads every score column by `getattr` between an
    irreversible file write and the commit that describes it. A `deferred`
    column would make that a lazy load — IO on an async session, i.e. a
    `MissingGreenlet` raised exactly where PM-013 says nothing may raise."""
    from sqlalchemy import inspect

    from backend.utils import score_columns

    attrs = inspect(Image).attrs
    deferred = [c for c in score_columns(Image) if getattr(attrs[c], "deferred", False)]
    assert not deferred, f"score columns must stay eagerly loaded: {deferred}"


# ---------------------------------------------------------------------------
# The clear predicate
# ---------------------------------------------------------------------------


class _StubScores:
    """Stand-ins for the three scorers `run_quality_scoring` imports at call time.

    Patched onto the modules rather than imported from them: the real ones pull
    in torch, which CI does not have and never will.
    """


def _patch_scorers(monkeypatch, *, technical=True, aesthetic=True, watermark=True, truncate_at=None):
    """Install fake scorers on the modules the job imports inside `_run`.

    `truncate_at` returns a results list shorter than the id list, which is what
    a cancelled batch produces — the rows past the cut must stay stale.
    """
    import sys
    import types

    def _cut(items):
        return items if truncate_at is None else items[:truncate_at]

    aesthetic_mod = types.ModuleType("backend.ml.aesthetic_scorer")

    async def score_images_batch(paths, model, job_id=None):
        return _cut([0.75] * len(paths)) if aesthetic else []

    async def score_images_watermark(paths, model, job_id=None, watermark_threshold=0.5):
        return _cut([{"watermark_score": 0.1, "has_watermark": False}] * len(paths)) if watermark else []

    async def extract_clip_embeddings_batch(paths, model, job_id=None):
        return []

    aesthetic_mod.score_images_batch = score_images_batch
    aesthetic_mod.score_images_watermark = score_images_watermark
    aesthetic_mod.extract_clip_embeddings_batch = extract_clip_embeddings_batch

    technical_mod = types.ModuleType("backend.ml.technical_scorer")

    async def score_images_technical(ids, paths, job_id=None, **kw):
        if not technical:
            return []
        return _cut([{
            "blur_score": 120.0, "noise_score": 2.0, "uniformity_score": 0.2,
            "color_score": 30.0, "saturation_score": 0.4, "luminance_score": 0.5,
            "is_blurry": False, "is_noisy": False, "is_uniform": False,
        } for _ in paths])

    technical_mod.score_images_technical = score_images_technical

    # `run_technical` also triggers the duplicate scan, which imports this from
    # the same module. The real one is pure numpy, but the stub module has
    # replaced it wholesale, so it has to be stubbed too.
    def find_duplicates_sync(phashes, duplicate_threshold=8):
        return []

    technical_mod.find_duplicates_sync = find_duplicates_sync

    monkeypatch.setitem(sys.modules, "backend.ml.aesthetic_scorer", aesthetic_mod)
    monkeypatch.setitem(sys.modules, "backend.ml.technical_scorer", technical_mod)

    # The job asks the model manager for a loaded model before every scorer call;
    # the stubs above ignore what it hands back.
    from backend.ml.model_manager import model_manager

    class _Entry:
        model = object()

    async def _load(*a, **kw):
        return _Entry()

    monkeypatch.setattr(model_manager, "load_aesthetic", _load)


async def _mark_stale(env, image_id: str) -> None:
    async with env.Session() as db:
        row = await db.get(Image, image_id)
        row.scores_stale = True
        await db.commit()


async def _seed_scores(env, image_id: str, **scores) -> None:
    async with env.Session() as db:
        row = await db.get(Image, image_id)
        for k, v in scores.items():
            setattr(row, k, v)
        await db.commit()


def test_a_run_that_refreshes_every_score_the_row_carries_clears_the_bit(tmp_path, monkeypatch):
    """The common case: re-scoring with the same checks that produced the
    original numbers."""
    _patch_scorers(monkeypatch)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await _one_image(env, ds["id"])
            await _seed_scores(env, img["id"], aesthetic_score=0.4, blur_score=50.0)
            await _mark_stale(env, img["id"])

            r = await env.client.post(f"{API}/quality/score", json={
                "dataset_id": ds["id"], "run_aesthetic": True, "run_technical": True,
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job

            row = await _fresh(env, img["id"])
            assert row.scores_stale is False
            assert row.aesthetic_score == 0.75

    run(scenario())


def test_a_watermark_only_run_does_not_claim_the_others_are_fresh(tmp_path, monkeypatch):
    """Set-covering over what was *written*: a run that refreshed one column of
    several must leave the bit standing, or the badge disappears while the blur
    score is still a measurement of deleted pixels."""
    _patch_scorers(monkeypatch, technical=False, aesthetic=False)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await _one_image(env, ds["id"])
            await _seed_scores(env, img["id"], aesthetic_score=0.4, blur_score=50.0)
            await _mark_stale(env, img["id"])

            r = await env.client.post(f"{API}/quality/score", json={
                "dataset_id": ds["id"], "run_aesthetic": False, "run_technical": False,
                "run_watermark": True,
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job

            row = await _fresh(env, img["id"])
            assert row.scores_stale is True
            assert row.watermark_score == 0.1

    run(scenario())


def test_a_row_the_run_never_reached_stays_stale(tmp_path, monkeypatch):
    """A cancelled batch truncates the results list, and the guarded writes stop
    at its end. Those rows must not be swept clear by a predicate that reasoned
    from `body.run_*` instead of from what was written."""
    _patch_scorers(monkeypatch, truncate_at=1)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await _one_image(env, ds["id"], "a.png")
            b = await _one_image(env, ds["id"], "b.png")
            for i in (a, b):
                await _seed_scores(env, i["id"], aesthetic_score=0.4, blur_score=50.0)
                await _mark_stale(env, i["id"])

            r = await env.client.post(f"{API}/quality/score", json={
                "dataset_id": ds["id"], "run_aesthetic": True, "run_technical": True,
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job

            rows = {i["filename"]: await _fresh(env, i["id"]) for i in (a, b)}
            cleared = [fn for fn, row in rows.items() if row.scores_stale is False]
            stale = [fn for fn, row in rows.items() if row.scores_stale is True]
            assert len(cleared) == 1 and len(stale) == 1, rows
            # The one that was reached is also the one that got the new score.
            assert rows[cleared[0]].aesthetic_score == 0.75
            assert rows[stale[0]].aesthetic_score == 0.4

    run(scenario())


def test_a_score_the_job_cannot_refresh_does_not_block_the_clear(tmp_path, monkeypatch):
    """`style_similarity_score` is deliberately outside `_JOB_SCORE_COLUMNS`.

    Its writer is a Core bulk `update(Image)` with no per-row load, so it can
    never evaluate the clear predicate — and if the column counted toward the
    predicate, the bit would be permanently un-clearable on any dataset that has
    ever run style similarity. See `docs/dev/scoring.md`.
    """
    _patch_scorers(monkeypatch)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await _one_image(env, ds["id"])
            await _seed_scores(
                env, img["id"],
                aesthetic_score=0.4, blur_score=50.0, style_similarity_score=0.9,
            )
            await _mark_stale(env, img["id"])

            r = await env.client.post(f"{API}/quality/score", json={
                "dataset_id": ds["id"], "run_aesthetic": True, "run_technical": True,
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job

            row = await _fresh(env, img["id"])
            assert row.scores_stale is False
            assert row.style_similarity_score == 0.9

    run(scenario())


def test_an_nsfw_score_the_run_skipped_keeps_the_bit(tmp_path, monkeypatch):
    """`nsfw_score` *is* in the job's universe, so a row carrying one that this
    run did not refresh is not fully covered."""
    _patch_scorers(monkeypatch)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await _one_image(env, ds["id"])
            await _seed_scores(
                env, img["id"], aesthetic_score=0.4, blur_score=50.0, nsfw_score=0.2
            )
            await _mark_stale(env, img["id"])

            r = await env.client.post(f"{API}/quality/score", json={
                "dataset_id": ds["id"], "run_aesthetic": True, "run_technical": True,
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job

            assert (await _fresh(env, img["id"])).scores_stale is True

    run(scenario())


def test_a_run_that_measured_nothing_leaves_the_bit_alone(tmp_path, monkeypatch):
    """A pass with only `run_embeddings` (or only `run_dino`) ticked writes no
    score column at all, so `refreshed` is empty — and for a row whose *job*-score
    columns are all NULL, `stale_left` would be empty too and the bit would clear
    having taken no measurement. The clear is gated on `refreshed` as well.

    `run_aesthetic` and `run_technical` default to **True** in `ScoreRequest`, so
    both must be passed false explicitly.
    """
    _patch_scorers(monkeypatch)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await _one_image(env, ds["id"])
            # Only a score the job cannot refresh, so nothing lands in the
            # job-score universe to keep the bit standing on its own.
            await _seed_scores(env, img["id"], style_similarity_score=0.9)
            await _mark_stale(env, img["id"])

            r = await env.client.post(f"{API}/quality/score", json={
                "dataset_id": ds["id"], "run_aesthetic": False, "run_technical": False,
                "run_embeddings": True,
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job

            assert (await _fresh(env, img["id"])).scores_stale is True

    run(scenario())


def test_an_embeddings_only_run_does_not_clear_a_bit_a_real_edit_set(tmp_path, monkeypatch):
    """The same guard, reached the way a user reaches it: a real in-place resize
    sets the bit, then an embeddings-only pass must leave it standing."""
    _patch_scorers(monkeypatch)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await _one_image(env, ds["id"])
            await _seed_scores(env, img["id"], style_similarity_score=0.9)

            r = await env.client.post(
                f"{API}/images/{img['id']}/resize",
                json={"width": 20, "height": 10, "maintain_ar": False},
            )
            assert r.status_code == 200, r.text
            assert (await _fresh(env, img["id"])).scores_stale is True

            r = await env.client.post(f"{API}/quality/score", json={
                "dataset_id": ds["id"], "run_aesthetic": False, "run_technical": False,
                "run_embeddings": True,
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job

            assert (await _fresh(env, img["id"])).scores_stale is True

    run(scenario())


# ---------------------------------------------------------------------------
# Surfaces
# ---------------------------------------------------------------------------


def test_the_bit_is_on_both_image_payloads(tmp_path):
    """The gallery card and the detail page both render a badge from it."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await _one_image(env, ds["id"])
            await _mark_stale(env, img["id"])

            listed = (await env.client.get(f"{API}/images/", params={"dataset_id": ds["id"]})).json()
            rows = listed["images"] if isinstance(listed, dict) else listed
            assert rows[0]["scores_stale"] is True

            detail = (await env.client.get(f"{API}/images/{img['id']}")).json()
            assert detail["scores_stale"] is True

    run(scenario())


def test_the_export_preview_counts_stale_scores_over_the_whole_dataset(tmp_path):
    """`stale_scores_count` is whole-dataset scope and `stale_scores_will_export`
    responds to every filter — the same split as the unlicensed pair, and for the
    same reason. The client cannot derive the second: it would have to re-apply
    the aesthetic, caption, flag, style-similarity and five license filters.

    Modelled on `test_unlicensed_will_export_accounts_for_every_filter`.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("mix")
            plain = await _one_image(env, ds["id"], "plain.png")
            captioned = await _one_image(env, ds["id"], "cap.png")
            r = await env.client.put(
                f"{API}/captions/image/{captioned['id']}", json={"caption_text": "a cat"}
            )
            assert r.status_code == 200, r.text
            for i in (plain, captioned):
                await _mark_stale(env, i["id"])

            async def preview(**params):
                r = await env.client.get(f"{API}/export/preview/{ds['id']}", params=params)
                assert r.status_code == 200, r.text
                return r.json()

            p = await preview()
            assert p["stale_scores_count"] == 2
            assert p["stale_scores_will_export"] == 2

            # A non-license filter drops one of them; the will-export figure
            # follows and the dataset-scope count does not.
            p = await preview(captioned_only=True)
            assert p["stale_scores_count"] == 2
            assert p["stale_scores_will_export"] == 1
            assert p["will_export"] == 1

    run(scenario())


def test_a_dataset_with_nothing_stale_reports_zero(tmp_path):
    """The warning block is rendered on `!!stale_scores_count`, so the key has to
    be present and falsy rather than absent."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("clean")
            await _one_image(env, ds["id"])

            p = (await env.client.get(f"{API}/export/preview/{ds['id']}")).json()
            assert p["stale_scores_count"] == 0
            assert p["stale_scores_will_export"] == 0

    run(scenario())
