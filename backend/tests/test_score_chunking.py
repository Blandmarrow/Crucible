"""The quality-scoring job commits per chunk within each check (M9 stage 3).

Before this, the job held every result in memory and wrote it all in one commit
at the end, so a crash, a Restart or a shutdown past `JobQueue.stop()`'s grace
saved nothing. Now each check scores `SCORE_CHUNK` images, writes them, commits
and checks for a cancel, so whatever stops the job leaves the finished chunks in
the DB — and `only_unscored` re-runs pick up the rest, the rows being the
checkpoint.

`SCORE_CHUNK` is shrunk to 2 here rather than creating thousands of rows. The
scorers are stubbed at the module boundary, as in `test_scores_stale.py`: the
real ones need torch, which CI does not have.
"""
import sys
import types
from datetime import datetime
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

_TECH = {
    "blur_score": 120.0, "noise_score": 2.0, "uniformity_score": 0.2,
    "color_score": 30.0, "saturation_score": 0.4, "luminance_score": 0.5,
    "is_blurry": False, "is_noisy": False, "is_uniform": False,
}


def _install(monkeypatch, *, chunk=2, aesthetic_scores=None, on_technical_call=None):
    """Stub the scorers and shrink the chunk. Returns `calls`: per check, the file
    names each scorer call received, in order.

    `on_technical_call(n, paths)` runs at the start of the technical scorer's
    n-th call (from 1) and may return a shorter result list, request a cancel,
    or raise — the three ways a job stops partway.
    """
    import backend.routers.quality as quality
    from backend.ml.model_manager import ModelEntry, model_manager

    monkeypatch.setattr(quality, "SCORE_CHUNK", chunk)
    calls: dict[str, list[list[str]]] = {"aesthetic": [], "technical": [], "embeddings": []}

    async def _tick(progress, n):
        if progress is not None:
            for i in range(n):
                await progress(i + 1)

    aesthetic_mod = types.ModuleType("backend.ml.aesthetic_scorer")

    async def score_images_batch(paths, handle, job_id=None, model="laion", progress=None):
        calls["aesthetic"].append([Path(p).name for p in paths])
        await _tick(progress, len(paths))
        if aesthetic_scores is None:
            return [0.75] * len(paths)
        return [aesthetic_scores[Path(p).name] for p in paths]

    async def _unused(*a, **kw):  # pragma: no cover - unticked in these tests
        return []

    async def extract_clip_embeddings_batch(paths, handle, job_id=None, progress=None):
        calls["embeddings"].append([Path(p).name for p in paths])
        await _tick(progress, len(paths))
        return [b"new-clip"] * len(paths)

    aesthetic_mod.score_images_batch = score_images_batch
    aesthetic_mod.score_images_watermark = _unused
    aesthetic_mod.extract_clip_embeddings_batch = extract_clip_embeddings_batch

    technical_mod = types.ModuleType("backend.ml.technical_scorer")

    async def score_images_technical(ids, paths, job_id=None, progress=None, **kw):
        calls["technical"].append([Path(p).name for p in paths])
        out = [dict(_TECH) for _ in paths]
        if on_technical_call is not None:
            short = on_technical_call(len(calls["technical"]), paths)
            if short is not None:
                out = out[:short]
        await _tick(progress, len(out))
        return out

    def find_duplicates_sync(phashes, duplicate_threshold=8):
        return []

    technical_mod.score_images_technical = score_images_technical
    technical_mod.find_duplicates_sync = find_duplicates_sync

    monkeypatch.setitem(sys.modules, "backend.ml.aesthetic_scorer", aesthetic_mod)
    monkeypatch.setitem(sys.modules, "backend.ml.technical_scorer", technical_mod)

    async def _load(*a, **kw):
        return ModelEntry(object(), None, vram_mb=0)

    monkeypatch.setattr(model_manager, "load_aesthetic", _load)
    return calls


async def _images(env, ds_id: str, n: int) -> dict[str, str]:
    """Upload `n` images named i0.png…; returns {file name: image id}."""
    out = {}
    for i in range(n):
        img = await upload_image(env, ds_id, f"i{i}.png", png_bytes(color=(i * 30, 5, 5)))
        out[f"i{i}.png"] = img["id"]
    return out


async def _set(env, image_id: str, **cols) -> None:
    async with env.Session() as db:
        row = await db.get(Image, image_id)
        for k, v in cols.items():
            setattr(row, k, v)
        await db.commit()


async def _rows(env, ids) -> dict[str, Image]:
    """Fresh reads: the point is what was *committed*."""
    async with env.Session() as db:
        rows = (await db.execute(select(Image).where(Image.id.in_(list(ids))))).scalars().all()
        return {r.id: r for r in rows}


async def _score(env, **body):
    r = await env.client.post(f"{API}/quality/score", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_each_check_scores_in_chunks_and_every_row_lands(tmp_path, monkeypatch):
    calls = _install(monkeypatch)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            imgs = await _images(env, ds["id"], 5)
            old = datetime(2000, 1, 1)
            for img_id in imgs.values():
                await _set(env, img_id, updated_at=old)

            out = await _score(env, dataset_id=ds["id"], run_aesthetic=True, run_technical=True)
            assert out["total"] == 5
            job = await wait_for_job(env, out["job_id"], timeout=60)
            assert job["status"] == "completed", job

            rows = await _rows(env, imgs.values())
            for r in rows.values():
                assert r.aesthetic_score == 0.75 and r.aesthetic_model == "laion"
                assert r.blur_score == 120.0
                assert r.quality_flags["is_blurry"] is False
                # The bulk write still fires `onupdate`, so the stats cache moves.
                assert r.updated_at > old

    run(scenario())
    # Five rows in chunks of two, per check: the job never holds more than a chunk.
    assert [len(c) for c in calls["aesthetic"]] == [2, 2, 1]
    assert [len(c) for c in calls["technical"]] == [2, 2, 1]


def test_a_cancel_keeps_the_chunks_already_scored(tmp_path, monkeypatch):
    from backend.workers.job_queue import job_queue

    def cancel_on_second(n, paths):
        if n == 2:
            # What a real scorer does: it sees the request at the top of its next
            # image and returns what it has.
            job_queue.request_cancel(job_queue._current_job_id)
            return 1
        return None

    _install(monkeypatch, on_technical_call=cancel_on_second)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            imgs = await _images(env, ds["id"], 5)
            for img_id in imgs.values():
                await _set(env, img_id, blur_score=50.0, scores_stale=True)

            out = await _score(env, dataset_id=ds["id"], run_aesthetic=False, run_technical=True)
            job = await wait_for_job(env, out["job_id"], timeout=60)
            assert job["status"] == "cancelled", job

            rows = (await _rows(env, imgs.values())).values()
            scored = [r for r in rows if r.blur_score == 120.0]
            untouched = [r for r in rows if r.blur_score == 50.0]
            # Chunk one (2 rows) plus the part of chunk two before the cancel.
            assert len(scored) == 3 and len(untouched) == 2
            assert all(r.scores_stale is False for r in scored)
            assert all(r.scores_stale is True for r in untouched)

    run(scenario())


def test_a_crash_keeps_the_chunks_already_scored(tmp_path, monkeypatch):
    """The case the old single commit lost entirely: the job dies between chunks."""
    def crash_on_second(n, paths):
        if n == 2:
            raise RuntimeError("simulated crash")
        return None

    _install(monkeypatch, on_technical_call=crash_on_second)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            imgs = await _images(env, ds["id"], 5)

            out = await _score(env, dataset_id=ds["id"], run_aesthetic=False, run_technical=True)
            job = await wait_for_job(env, out["job_id"], timeout=60)
            assert job["status"] == "failed", job

            rows = (await _rows(env, imgs.values())).values()
            assert sum(r.blur_score == 120.0 for r in rows) == 2
            assert sum(r.blur_score is None for r in rows) == 3

    run(scenario())


def test_only_unscored_visits_rows_missing_the_check_or_stale(tmp_path, monkeypatch):
    calls = _install(monkeypatch, chunk=10)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            imgs = await _images(env, ds["id"], 4)
            full = dict(aesthetic_score=4.0, aesthetic_model="laion",
                        **{k: 1.0 for k in ("blur_score", "noise_score", "uniformity_score",
                                            "color_score", "saturation_score", "luminance_score")})
            await _set(env, imgs["i0.png"], **full)                            # done
            await _set(env, imgs["i1.png"], aesthetic_score=4.0, aesthetic_model="laion")
            # i2: never scored
            await _set(env, imgs["i3.png"], **full, scores_stale=True)        # stale

            out = await _score(env, dataset_id=ds["id"], run_aesthetic=True,
                               run_technical=True, only_unscored=True)
            assert out["total"] == 3  # i0 is in no check's filter
            job = await wait_for_job(env, out["job_id"], timeout=60)
            assert job["status"] == "completed", job

            rows = await _rows(env, imgs.values())
            assert rows[imgs["i0.png"]].aesthetic_score == 4.0
            assert rows[imgs["i1.png"]].aesthetic_score == 4.0
            assert rows[imgs["i1.png"]].blur_score == 120.0
            # Every check visited the stale row, so it is cleared.
            assert rows[imgs["i3.png"]].scores_stale is False
            assert rows[imgs["i3.png"]].aesthetic_score == 0.75

    run(scenario())
    assert sorted(sum(calls["aesthetic"], [])) == ["i2.png", "i3.png"]
    assert sorted(sum(calls["technical"], [])) == ["i1.png", "i2.png", "i3.png"]


def test_only_unscored_still_reembeds_a_stale_row_an_earlier_check_cleared(tmp_path, monkeypatch):
    """The stale half of the filter is a snapshot taken when the job starts.

    Here the technical check clears the row's bit (aesthetic and technical cover
    every score it carries). A live `scores_stale` test would then drop the row
    from the embeddings check, since its `clip_embedding` is present, and the
    embedding of the pre-edit pixels would stay with nothing left to flag it."""
    calls = _install(monkeypatch, chunk=10)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            imgs = await _images(env, ds["id"], 1)
            x = imgs["i0.png"]
            await _set(env, x, aesthetic_score=4.0, aesthetic_model="laion",
                       **{k: 1.0 for k in ("blur_score", "noise_score", "uniformity_score",
                                           "color_score", "saturation_score", "luminance_score")},
                       clip_embedding=b"old-clip", scores_stale=True)

            out = await _score(env, dataset_id=ds["id"], run_aesthetic=True, run_technical=True,
                               run_embeddings=True, only_unscored=True)
            assert (await wait_for_job(env, out["job_id"], timeout=60))["status"] == "completed"

            async with env.Session() as db:
                # A column select: `clip_embedding` is deferred on the entity.
                stale, clip = (await db.execute(
                    select(Image.scores_stale, Image.clip_embedding).where(Image.id == x)
                )).one()
            assert stale is False
            assert clip == b"new-clip"

    run(scenario())
    assert calls["embeddings"] == [["i0.png"]]


def test_a_row_edited_mid_job_is_not_cleared(tmp_path, monkeypatch):
    """Only rows stale when the job started can be cleared by it. A row that
    turns stale mid-job was edited while the job ran, so its aesthetic score —
    already written by the earlier check — may describe pixels that are gone."""
    from backend import database

    def edit_during_technical(n, paths):
        async def mark():
            async with database.AsyncSessionLocal() as s:
                for row in (await s.execute(select(Image))).scalars():
                    row.scores_stale = True
                await s.commit()
        return mark

    state = {}

    def on_technical(n, paths):
        state["mark"] = edit_during_technical(n, paths)
        return None

    calls = _install(monkeypatch, chunk=10, on_technical_call=on_technical)

    # Run the "edit" inside the technical scorer, between the aesthetic check's
    # write and the technical check's.
    import backend.ml  # noqa: F401 - the stub module is looked up on sys.modules
    technical = sys.modules["backend.ml.technical_scorer"]
    stub = technical.score_images_technical

    async def with_edit(ids, paths, job_id=None, progress=None, **kw):
        out = await stub(ids, paths, job_id=job_id, progress=progress, **kw)
        await state["mark"]()
        return out

    technical.score_images_technical = with_edit

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            imgs = await _images(env, ds["id"], 2)

            out = await _score(env, dataset_id=ds["id"], run_aesthetic=True, run_technical=True)
            assert (await wait_for_job(env, out["job_id"], timeout=60))["status"] == "completed"

            assert all(r.scores_stale for r in (await _rows(env, imgs.values())).values())

    run(scenario())
    assert calls["technical"]


def test_only_unscored_with_nothing_left_starts_no_job(tmp_path, monkeypatch):
    _install(monkeypatch)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            imgs = await _images(env, ds["id"], 2)
            for img_id in imgs.values():
                await _set(env, img_id, aesthetic_score=4.0, aesthetic_model="laion")

            out = await _score(env, dataset_id=ds["id"], run_aesthetic=True,
                               run_technical=False, only_unscored=True)
            assert out == {"job_id": None, "message": "Nothing left to score"}

    run(scenario())


def test_only_unscored_and_only_mismatched_are_refused_together(tmp_path, monkeypatch):
    _install(monkeypatch)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            r = await env.client.post(f"{API}/quality/score", json={
                "dataset_id": ds["id"], "only_unscored": True, "only_mismatched": True,
            })
            assert r.status_code == 422, r.text

    run(scenario())


def test_the_stale_bit_clears_across_checks_and_chunks(tmp_path, monkeypatch):
    """The clear predicate counts the columns of every check that already
    visited all its rows, so a row is cleared in the last check that touches it
    even though no single chunk wrote all of its scores."""
    _install(monkeypatch)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            imgs = await _images(env, ds["id"], 3)
            for img_id in imgs.values():
                await _set(env, img_id, aesthetic_score=4.0, aesthetic_model="laion",
                           blur_score=50.0, scores_stale=True)

            # One check alone does not cover the row: the bit stays.
            out = await _score(env, dataset_id=ds["id"], run_aesthetic=True, run_technical=False)
            assert (await wait_for_job(env, out["job_id"], timeout=60))["status"] == "completed"
            assert all(r.scores_stale for r in (await _rows(env, imgs.values())).values())

            out = await _score(env, dataset_id=ds["id"], run_aesthetic=True, run_technical=True)
            assert (await wait_for_job(env, out["job_id"], timeout=60))["status"] == "completed"
            assert not any(r.scores_stale for r in (await _rows(env, imgs.values())).values())

    run(scenario())


def test_the_marker_rides_with_its_score_in_every_chunk(tmp_path, monkeypatch):
    """score non-NULL <=> marker non-NULL, per row, across chunk boundaries."""
    _install(monkeypatch, aesthetic_scores={"i0.png": 5.0, "i1.png": None, "i2.png": 6.0})

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            imgs = await _images(env, ds["id"], 3)
            # A marker left from an earlier model must not survive a NULL score.
            await _set(env, imgs["i1.png"], aesthetic_score=3.0, aesthetic_model="v2_5")

            out = await _score(env, dataset_id=ds["id"], run_aesthetic=True, run_technical=False)
            assert (await wait_for_job(env, out["job_id"], timeout=60))["status"] == "completed"

            rows = await _rows(env, imgs.values())
            assert (rows[imgs["i0.png"]].aesthetic_score, rows[imgs["i0.png"]].aesthetic_model) == (5.0, "laion")
            assert (rows[imgs["i1.png"]].aesthetic_score, rows[imgs["i1.png"]].aesthetic_model) == (None, None)
            assert (rows[imgs["i2.png"]].aesthetic_score, rows[imgs["i2.png"]].aesthetic_model) == (6.0, "laion")

    run(scenario())


def test_progress_is_one_monotone_percent_with_per_check_counts(tmp_path, monkeypatch):
    _install(monkeypatch)
    from backend.workers.progress import broadcaster

    events: list[dict] = []
    real_emit = broadcaster.emit

    async def spy(job_id, event):
        if event.get("job_type") == "quality_score" and event.get("status") == "running":
            events.append(dict(event))
        return await real_emit(job_id, event)

    monkeypatch.setattr(broadcaster, "emit", spy)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await _images(env, ds["id"], 5)
            out = await _score(env, dataset_id=ds["id"], run_aesthetic=True, run_technical=True)
            assert (await wait_for_job(env, out["job_id"], timeout=60))["status"] == "completed"

    run(scenario())
    scored = [e for e in events if "step" in e.get("message", "")]
    assert scored, events
    percents = [e["percent"] for e in scored]
    assert percents == sorted(percents)  # never jumps back at a chunk or check
    assert percents[-1] == 100.0
    assert all(e["total"] == 5 and 1 <= e["done"] <= 5 for e in scored)
    # The bar does not restart per check; the counts do, and say which step.
    assert scored[0]["message"].startswith("Aesthetic scoring 1/5 · step 1 of 2")
    assert scored[-1]["message"] == "Technical scoring 5/5 · step 2 of 2"
