"""An in-place pixel rewrite clears an image's stored embeddings.

`clip_embedding`, `dino_embedding` and `dino_layer_embeddings` are measured from
the pixels. `scores_stale` qualifies the `*_score` columns only, and only on a
row that carries one, so before this a row with vectors and no score — embed for
style similarity, then crop — kept vectors of pixels that no longer existed,
and style similarity compared them as if they were current. Embeddings have no
stale bit, so `record_in_place` clears them (`utils.clear_embeddings`), and so
does a snapshot restore that writes a file's old bytes back, since the vectors
are not mirrored on `VersionImageState`.

The columns are `deferred`, and `record_in_place` sits between an irreversible
file write and the commit (PM-013), so the clear must be a write that never
loads them — the first test pins that against a real async session, because a
transient `Image()` has every attribute loaded and cannot show a lazy load.

No torch: the vectors are seeded bytes, which is enough since nothing here
reads one.
"""
from pathlib import Path

import pytest
from sqlalchemy import event, inspect, select

from backend.models.image import Image
from backend.services import version_service
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image, wait_for_job
from backend.utils import embedding_columns, record_in_place

EMBEDDINGS = ("clip_embedding", "dino_embedding", "dino_layer_embeddings")
VECTORS = {"clip_embedding": b"c" * 8, "dino_embedding": b"d" * 8, "dino_layer_embeddings": b"l" * 8}


async def _one_image(env, dataset_id: str, name: str = "a.png") -> dict:
    return await upload_image(env, dataset_id, name, png_bytes((10, 20, 30), (40, 20)))


async def _seed(env, image_id: str, **values) -> None:
    async with env.Session() as db:
        row = await db.get(Image, image_id)
        for k, v in values.items():
            setattr(row, k, v)
        await db.commit()


async def _read(env, image_id: str) -> dict:
    """A column select, never an attribute of a closed entity: the embeddings
    are deferred, so reading one off a returned row would lazy-load."""
    async with env.Session() as db:
        row = (await db.execute(select(
            Image.clip_embedding, Image.dino_embedding, Image.dino_layer_embeddings,
            Image.scores_stale, Image.processing_history,
        ).where(Image.id == image_id))).one()
    return row._asdict()


def _vectors(row: dict) -> dict:
    return {c: row[c] for c in EMBEDDINGS}


# ---------------------------------------------------------------------------
# The structure the clear rests on
# ---------------------------------------------------------------------------


def test_the_embedding_universe_is_the_three_deferred_vectors():
    """Derived by suffix, like `score_columns`; this pins what the suffix finds.
    Each must stay `deferred`: that is why the clear cannot read first, and why
    the next test has to prove the write does not load them either."""
    assert embedding_columns(Image) == frozenset(EMBEDDINGS)
    attrs = inspect(Image).attrs
    assert all(getattr(attrs[c], "deferred", False) for c in EMBEDDINGS)


def test_record_in_place_clears_the_vectors_without_loading_them(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await _one_image(env, ds["id"])
            await _seed(env, img["id"], **VECTORS)

            engine = env.Session.kw["bind"].sync_engine
            sent: list[str] = []

            def hook(conn, cursor, statement, parameters, context, executemany):
                sent.append(" ".join(statement.split()))

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
                assert set(EMBEDDINGS) <= inspect(row).unloaded

                event.listen(engine, "before_cursor_execute", hook)
                try:
                    record_in_place(row, "resize", width=20, height=10)
                    assert sent == [], f"record_in_place emitted SQL: {sent}"
                    await db.commit()
                finally:
                    event.remove(engine, "before_cursor_execute", hook)

            updates = [s for s in sent if s.startswith("UPDATE images")]
            assert len(updates) == 1, sent
            assert all(c in updates[0] for c in EMBEDDINGS), updates[0]
            assert _vectors(await _read(env, img["id"])) == dict.fromkeys(EMBEDDINGS)

    run(scenario())


# ---------------------------------------------------------------------------
# Through the routes
# ---------------------------------------------------------------------------


def test_an_unscored_row_loses_its_vectors_but_is_not_marked_stale(tmp_path):
    """The gap itself: vectors and no score. The bit stays False — there is no
    score to qualify — but the vectors go."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await _one_image(env, ds["id"])
            await _seed(env, img["id"], **VECTORS)

            r = await env.client.post(
                f"{API}/images/{img['id']}/resize",
                json={"width": 20, "height": 10, "maintain_ar": False},
            )
            assert r.status_code == 200, r.text

            row = await _read(env, img["id"])
            assert _vectors(row) == dict.fromkeys(EMBEDDINGS)
            assert row["scores_stale"] is False
            assert [h["op"] for h in row["processing_history"]] == ["resize"]

    run(scenario())


def test_a_scored_row_loses_its_vectors_and_is_marked_stale(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await _one_image(env, ds["id"])
            await _seed(env, img["id"], aesthetic_score=0.4, **VECTORS)

            r = await env.client.post(
                f"{API}/images/batch/resize",
                json={"image_ids": [img["id"]], "width": 20, "height": 10,
                      "maintain_ar": False},
            )
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

            row = await _read(env, img["id"])
            assert _vectors(row) == dict.fromkeys(EMBEDDINGS)
            assert row["scores_stale"] is True

    run(scenario())


def test_a_replace_crop_clears_the_vectors(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await _one_image(env, ds["id"])
            await _seed(env, img["id"], **VECTORS)

            r = await env.client.post(
                f"{API}/images/{img['id']}/crop",
                json={"x": 0, "y": 0, "width": 20, "height": 20, "replace": True},
            )
            assert r.status_code == 200, r.text
            assert _vectors(await _read(env, img["id"])) == dict.fromkeys(EMBEDDINGS)

    run(scenario())


def test_a_copy_crop_leaves_the_parent_its_vectors(tmp_path):
    """The parent's pixels were never touched, so its vectors still describe
    them; the child is a new row with none."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await _one_image(env, ds["id"])
            await _seed(env, img["id"], **VECTORS)

            r = await env.client.post(
                f"{API}/images/{img['id']}/crop",
                json={"x": 0, "y": 0, "width": 20, "height": 20, "replace": False},
            )
            assert r.status_code == 200, r.text
            child_id = r.json()["id"]

            assert _vectors(await _read(env, img["id"])) == VECTORS
            assert _vectors(await _read(env, child_id)) == dict.fromkeys(EMBEDDINGS)

    run(scenario())


# ---------------------------------------------------------------------------
# Snapshot restore
# ---------------------------------------------------------------------------
#
# Driven through `version_service` directly, on the same kind of engine
# `test_versioning_restore.py` builds, because a restore needs a snapshot with
# recorded content (manual mode) and a file whose bytes changed under it.


@pytest.fixture
def _datasets_root(tmp_path, monkeypatch):
    from backend.config import settings as app_settings

    root = tmp_path / "datasets"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(app_settings, "datasets_dir", root)
    return root


async def _restore_env(tmp_path: Path):
    from backend.tests.test_versioning_restore import make_env

    return await make_env(tmp_path, {"1.png": b"AAAA"}, mode="manual")


async def _vectors_of(Session, image_id: str) -> dict:
    async with Session() as db:
        row = (await db.execute(select(
            Image.clip_embedding, Image.dino_embedding, Image.dino_layer_embeddings,
        ).where(Image.id == image_id))).one()
    return row._asdict()


def test_a_restore_that_writes_old_pixels_back_clears_the_vectors(tmp_path, _datasets_root):
    async def scenario():
        engine, Session, ds_dir, ds_id = await _restore_env(tmp_path)
        target = ds_dir / "images" / "1.png"
        async with Session() as db:
            snap = await version_service.create_snapshot(db, ds_id, "s1", "")
            img = (await db.execute(select(Image).where(Image.dataset_id == ds_id))).scalar_one()
            image_id = img.id
            # The pixels change after the snapshot, and the vectors are
            # re-measured on the new ones.
            target.write_bytes(b"BBBB")
            for k, v in VECTORS.items():
                setattr(img, k, v)
            await db.commit()

            result = await version_service.restore_snapshot(
                db, ds_id, snap.id, pre_restore_snapshot=False)
            assert result["files_restored"] == 1, result

        assert target.read_bytes() == b"AAAA"
        assert await _vectors_of(Session, image_id) == dict.fromkeys(EMBEDDINGS)
        await engine.dispose()

    run(scenario())


def test_a_restore_that_cannot_write_the_pixels_keeps_the_vectors(tmp_path, _datasets_root):
    """The snapshot's copy is gone, so the write never happens: the file still
    holds the pixels the vectors were measured on, and they stay."""
    async def scenario():
        engine, Session, ds_dir, ds_id = await _restore_env(tmp_path)
        target = ds_dir / "images" / "1.png"
        async with Session() as db:
            snap = await version_service.create_snapshot(db, ds_id, "s1", "")
            objects = [p for p in (ds_dir / ".versions" / "objects").rglob("*") if p.is_file()]
            assert objects, "manual mode should have stored the file's bytes"
            for obj in objects:
                obj.unlink()
            img = (await db.execute(select(Image).where(Image.dataset_id == ds_id))).scalar_one()
            image_id = img.id
            target.write_bytes(b"BBBB")
            for k, v in VECTORS.items():
                setattr(img, k, v)
            await db.commit()

            result = await version_service.restore_snapshot(
                db, ds_id, snap.id, pre_restore_snapshot=False)
            assert result["files_unavailable"] == 1, result

        assert target.read_bytes() == b"BBBB"
        assert await _vectors_of(Session, image_id) == VECTORS
        await engine.dispose()

    run(scenario())


def test_a_restore_that_leaves_the_pixels_keeps_the_vectors(tmp_path, _datasets_root):
    """Only the caption changed since the snapshot: the file already holds the
    snapshot's bytes, nothing is written, and the vectors still describe it."""
    async def scenario():
        engine, Session, ds_dir, ds_id = await _restore_env(tmp_path)
        async with Session() as db:
            snap = await version_service.create_snapshot(db, ds_id, "s1", "")
            img = (await db.execute(select(Image).where(Image.dataset_id == ds_id))).scalar_one()
            image_id = img.id
            img.caption_text = "edited"
            for k, v in VECTORS.items():
                setattr(img, k, v)
            await db.commit()

            await version_service.restore_snapshot(
                db, ds_id, snap.id, pre_restore_snapshot=False)

        assert await _vectors_of(Session, image_id) == VECTORS
        await engine.dispose()

    run(scenario())
