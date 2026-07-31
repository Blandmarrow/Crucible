"""Tests for versioning maintenance: atomic object-store writes, the dataset-busy
guard, object-store pruning, and the version-delete DB cascade.

Same harness pattern as test_versioning_restore.py: real service code against a
scratch SQLite DB + dataset folder under tmp_path, driven via asyncio.run().
"""
import asyncio
import hashlib
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.database import Base
import backend.models  # noqa: F401 — register all models on Base
from backend.models.dataset import Dataset
from backend.models.image import Image
from backend.models.threshold_settings import ThresholdSettings
from backend.models.versioning import DatasetVersion, VersionImageState
from backend.services import version_service
from backend.services.dataset_busy import busy, ensure_not_busy


def run(coro):
    return asyncio.run(coro)


async def make_env(tmp_path: Path, names_contents: dict[str, bytes], mode: str = "auto"):
    """Engine + session factory + one dataset with the given image files."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")

    # Mirror backend/database.py: FK enforcement is per-connection, so the
    # ondelete="CASCADE" behaviour under test matches production.
    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    ds_dir = tmp_path / "ds"
    (ds_dir / "images").mkdir(parents=True)
    (ds_dir / "thumbnails").mkdir(parents=True)

    async with Session() as db:
        ds = Dataset(name="t", folder_path=str(ds_dir))
        db.add(ds)
        db.add(ThresholdSettings(id=1, versioning_mode=mode))
        await db.flush()
        for fn, content in names_contents.items():
            fp = ds_dir / "images" / fn
            fp.write_bytes(content)
            db.add(Image(
                dataset_id=ds.id, filename=fn, original_filename=fn, subfolder="",
                file_path=str(fp),
                thumbnail_path=str(ds_dir / "thumbnails" / (fp.stem + ".webp")),
            ))
        await db.commit()
        dataset_id = ds.id
    return engine, Session, ds_dir, dataset_id


# ---------------------------------------------------------------------------
# _store_object
# ---------------------------------------------------------------------------

def test_store_object_content_matches_returned_hash(tmp_path):
    ds_dir = tmp_path / "ds"
    ds_dir.mkdir()
    src = tmp_path / "src.png"
    src.write_bytes(b"PIXELDATA")

    h = version_service._store_object(str(ds_dir), str(src))

    assert h == hashlib.sha256(b"PIXELDATA").hexdigest()
    obj = version_service._object_store_path(str(ds_dir), h)
    assert obj.read_bytes() == b"PIXELDATA"
    # no temp leftovers
    assert not list((ds_dir / ".versions" / "objects").glob(".tmp-*"))

    # idempotent re-store
    h2 = version_service._store_object(str(ds_dir), str(src))
    assert h2 == h
    assert obj.read_bytes() == b"PIXELDATA"
    assert not list((ds_dir / ".versions" / "objects").glob(".tmp-*"))


def test_backup_records_hash_of_actual_bytes_not_stale_precomputed(tmp_path):
    """TOCTOU regression: a caller-supplied hash that no longer matches the file
    (and has no stored object) must not poison the store — the backfilled hash
    must be the hash of the bytes actually stored."""
    async def scenario():
        engine, Session, ds_dir, ds_id = await make_env(tmp_path, {"1.png": b"REAL"})
        async with Session() as db:
            await version_service.create_snapshot(db, ds_id, "s1", "")  # NULL-hash rows
            img = (await db.execute(select(Image).where(
                Image.dataset_id == ds_id))).scalar_one()

            stale = hashlib.sha256(b"WHAT THE CALLER SAW EARLIER").hexdigest()
            recorded = await version_service._backup_and_record_hash(
                img.id, img.file_path, str(ds_dir), db, precomputed_sha256=stale
            )
            await db.commit()

            real = hashlib.sha256(b"REAL").hexdigest()
            assert recorded == real
            assert version_service._object_store_path(str(ds_dir), real).read_bytes() == b"REAL"
            assert not version_service._object_store_path(str(ds_dir), stale).exists()

            hashes = (await db.execute(select(VersionImageState.file_hash).where(
                VersionImageState.image_id == img.id))).scalars().all()
            assert hashes == [real]
        await engine.dispose()

    run(scenario())


def test_backup_skips_io_when_precomputed_object_exists(tmp_path):
    async def scenario():
        engine, Session, ds_dir, ds_id = await make_env(tmp_path, {"1.png": b"REAL"})
        async with Session() as db:
            await version_service.create_snapshot(db, ds_id, "s1", "")
            img = (await db.execute(select(Image).where(
                Image.dataset_id == ds_id))).scalar_one()

            # object already safely stored under the precomputed hash
            pre = version_service._store_object(str(ds_dir), img.file_path)
            # file changes afterwards — the stored content must stay authoritative
            Path(img.file_path).write_bytes(b"CHANGED")

            recorded = await version_service._backup_and_record_hash(
                img.id, img.file_path, str(ds_dir), db, precomputed_sha256=pre
            )
            assert recorded == pre
            assert version_service._object_store_path(
                str(ds_dir), pre).read_bytes() == b"REAL"
        await engine.dispose()

    run(scenario())


def test_backup_refuses_out_of_tree_path(tmp_path):
    """A row whose file_path escaped the dataset must not reach the object store.

    A stored object is retrievable through a snapshot restore, so copying an
    out-of-tree file in is an arbitrary-file read primitive. Skip-and-log, not
    raise — the caller's overwrite/deletion still completes.
    """
    async def scenario():
        engine, Session, ds_dir, ds_id = await make_env(tmp_path, {"1.png": b"REAL"})
        outside = tmp_path / "secret.txt"
        outside.write_bytes(b"SECRET")
        async with Session() as db:
            await version_service.create_snapshot(db, ds_id, "s1", "")
            img = (await db.execute(select(Image).where(
                Image.dataset_id == ds_id))).scalar_one()

            recorded = await version_service._backup_and_record_hash(
                img.id, str(outside), str(ds_dir), db
            )
            await db.commit()

            assert recorded is None
            secret_hash = hashlib.sha256(b"SECRET").hexdigest()
            assert not version_service._object_store_path(str(ds_dir), secret_hash).exists()
            hashes = (await db.execute(select(VersionImageState.file_hash).where(
                VersionImageState.image_id == img.id))).scalars().all()
            assert hashes == [None]
        await engine.dispose()

    run(scenario())


def test_backup_refuses_symlink_escaping_the_dataset(tmp_path):
    """The one live vector: rescan registers a symlink under its in-tree path
    (`file_path=str(f)`) and `open()` follows it out of tree. The guard resolves,
    so an in-tree-looking path whose target is outside is still refused."""
    async def scenario():
        engine, Session, ds_dir, ds_id = await make_env(tmp_path, {"1.png": b"REAL"})
        outside = tmp_path / "secret.txt"
        outside.write_bytes(b"SECRET")
        link = ds_dir / "images" / "link.png"
        link.symlink_to(outside)

        async with Session() as db:
            await version_service.create_snapshot(db, ds_id, "s1", "")
            img = (await db.execute(select(Image).where(
                Image.dataset_id == ds_id))).scalar_one()

            # the path is inside the dataset as a string; only .resolve() reveals it
            assert str(link).startswith(str(ds_dir))
            assert link.read_bytes() == b"SECRET"

            recorded = await version_service._backup_and_record_hash(
                img.id, str(link), str(ds_dir), db
            )
            await db.commit()

            assert recorded is None
            secret_hash = hashlib.sha256(b"SECRET").hexdigest()
            assert not version_service._object_store_path(str(ds_dir), secret_hash).exists()
        await engine.dispose()

    run(scenario())


# ---------------------------------------------------------------------------
# Prune
# ---------------------------------------------------------------------------

def test_prune_deletes_unreferenced_and_tmp_keeps_referenced(tmp_path):
    async def scenario():
        engine, Session, ds_dir, ds_id = await make_env(
            tmp_path, {"1.png": b"AAAA", "2.png": b"BBBB"}, mode="manual")
        async with Session() as db:
            await version_service.create_snapshot(db, ds_id, "s1", "")

            objects_dir = ds_dir / ".versions" / "objects"
            junk_hash = hashlib.sha256(b"JUNK").hexdigest()
            junk = version_service._object_store_path(str(ds_dir), junk_hash)
            junk.parent.mkdir(parents=True, exist_ok=True)
            junk.write_bytes(b"JUNK")
            tmp_leftover = objects_dir / ".tmp-deadbeef"
            tmp_leftover.write_bytes(b"partial write")

            summary = await version_service.prune_object_store(
                db, ds_id, min_age_seconds=0)

            assert summary["objects_deleted"] == 2  # junk + tmp leftover
            assert summary["objects_kept"] == 2
            assert summary["bytes_freed"] == len(b"JUNK") + len(b"partial write")
            assert not junk.exists()
            assert not tmp_leftover.exists()
            for content in (b"AAAA", b"BBBB"):
                h = hashlib.sha256(content).hexdigest()
                assert version_service._object_store_path(str(ds_dir), h).exists()
        await engine.dispose()

    run(scenario())


def test_prune_skips_recent_files(tmp_path):
    async def scenario():
        engine, Session, ds_dir, ds_id = await make_env(tmp_path, {}, mode="manual")
        async with Session() as db:
            fresh = version_service._object_store_path(
                str(ds_dir), hashlib.sha256(b"FRESH").hexdigest())
            fresh.parent.mkdir(parents=True, exist_ok=True)
            fresh.write_bytes(b"FRESH")

            summary = await version_service.prune_object_store(
                db, ds_id, min_age_seconds=3600)
            assert summary["objects_deleted"] == 0
            assert fresh.exists()
        await engine.dispose()

    run(scenario())


# ---------------------------------------------------------------------------
# Busy guard
# ---------------------------------------------------------------------------

def test_busy_guard_raises_409_inside_and_passes_outside():
    ensure_not_busy("ds1")  # not busy — no raise
    with busy("ds1", "restore"):
        with pytest.raises(HTTPException) as exc:
            ensure_not_busy("ds1")
        assert exc.value.status_code == 409
        assert "restore" in exc.value.detail
        ensure_not_busy("other-ds")  # other datasets unaffected
    ensure_not_busy("ds1")  # flag cleared on exit


def test_busy_flag_cleared_on_exception():
    with pytest.raises(RuntimeError):
        with busy("ds1", "restore"):
            raise RuntimeError("job blew up")
    ensure_not_busy("ds1")  # must not raise


# ---------------------------------------------------------------------------
# Version delete cascade (passive_deletes)
# ---------------------------------------------------------------------------

def test_version_delete_cascades_state_rows_in_db(tmp_path):
    async def scenario():
        engine, Session, ds_dir, ds_id = await make_env(
            tmp_path, {"1.png": b"AAAA", "2.png": b"BBBB"})
        async with Session() as db:
            snap = await version_service.create_snapshot(db, ds_id, "s1", "")
            count = (await db.execute(select(VersionImageState).where(
                VersionImageState.version_id == snap.id))).scalars().all()
            assert len(count) == 2

        # fresh session: image_states is unloaded, so passive_deletes means the
        # ORM deletes only the version row and the DB cascade removes the states
        async with Session() as db:
            ver = await db.get(DatasetVersion, snap.id)
            await db.delete(ver)
            await db.commit()

            remaining = (await db.execute(text(
                "SELECT COUNT(*) FROM version_image_states WHERE version_id = :v"
            ), {"v": snap.id})).scalar()
            assert remaining == 0
        await engine.dispose()

    run(scenario())
