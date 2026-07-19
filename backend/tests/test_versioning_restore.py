"""Regression tests for dataset-versioning restore data-loss bugs.

Each test runs the real service code against a scratch SQLite DB and dataset
folder under tmp_path. Async scenarios are driven via asyncio.run() so no
async pytest plugin is required.

Covered failure classes (all previously reproduced live):
- filename swaps/chains since the snapshot aborted restore with an
  IntegrityError on uq_dataset_filename (Pass 2 had no DB-level staging)
- a deleted image whose old name a newer image took could never be restored
  (re-creation INSERT collided), in both keep and remove modes
- pre-restore auto-snapshots always landed on the branch named "main" and
  moved main's head, corrupting branch state for non-main users
- images moved to another dataset (same ID) made restore crash on a PK
  collision; their content was never backed up
- a failed pre-restore snapshot was warning-and-continue, silently making the
  restore non-undoable
"""
import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.database import Base
import backend.models  # noqa: F401 — register all models on Base
from backend.models.dataset import Dataset
from backend.models.image import Image
from backend.models.threshold_settings import ThresholdSettings
from backend.models.versioning import DatasetBranch, DatasetVersion
from backend.services import version_service


def run(coro):
    return asyncio.run(coro)


async def make_env(tmp_path: Path, names_contents: dict[str, bytes], mode: str = "auto"):
    """Engine + session factory + one dataset with the given image files."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
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
            db.add(_image_row(ds, ds_dir, fn, content))
        await db.commit()
        dataset_id = ds.id
    return engine, Session, ds_dir, dataset_id


def _image_row(ds: Dataset, ds_dir: Path, fn: str, content: bytes) -> Image:
    fp = ds_dir / "images" / fn
    fp.write_bytes(content)
    return Image(
        dataset_id=ds.id, filename=fn, original_filename=fn, subfolder="",
        file_path=str(fp),
        thumbnail_path=str(ds_dir / "thumbnails" / (fp.stem + ".webp")),
    )


async def _get_by_filename(db, dataset_id: str, fn: str) -> Image | None:
    result = await db.execute(select(Image).where(
        Image.dataset_id == dataset_id, Image.filename == fn))
    return result.scalar_one_or_none()


async def _delete_like_router(db, img: Image) -> None:
    """Delete an image the way routers/images.py::delete_image does."""
    await version_service.mark_image_deleted_in_versions(img.id, img.file_path, db)
    path = Path(img.file_path)
    await db.delete(img)
    await db.commit()
    path.unlink(missing_ok=True)


def test_restore_filename_swap(tmp_path):
    async def scenario():
        engine, Session, ds_dir, ds_id = await make_env(
            tmp_path, {"1.png": b"AAAA", "2.png": b"BBBB"})
        imgdir = ds_dir / "images"
        async with Session() as db:
            snap = await version_service.create_snapshot(db, ds_id, "s1", "")
            a = await _get_by_filename(db, ds_id, "1.png")
            b = await _get_by_filename(db, ds_id, "2.png")
            # user swaps the two names through a temp name (two manual renames)
            (imgdir / "1.png").rename(imgdir / "tmp.png")
            (imgdir / "2.png").rename(imgdir / "1.png")
            (imgdir / "tmp.png").rename(imgdir / "2.png")
            a.filename = "tmpswap.png"
            await db.commit()
            b.filename, b.file_path = "1.png", str(imgdir / "1.png")
            await db.commit()
            a.filename, a.file_path = "2.png", str(imgdir / "2.png")
            await db.commit()

            result = await version_service.restore_snapshot(
                db, ds_id, snap.id, pre_restore_snapshot=False)
            assert result["files_failed"] == 0
            assert result["files_unavailable"] == 0

            await db.refresh(a)
            await db.refresh(b)
            assert a.filename == "1.png" and b.filename == "2.png"
        assert (imgdir / "1.png").read_bytes() == b"AAAA"
        assert (imgdir / "2.png").read_bytes() == b"BBBB"
        assert not list(imgdir.glob("*__restore_tmp*"))
        await engine.dispose()

    run(scenario())


def test_restore_name_reuse_keep_renames_extra_aside(tmp_path):
    async def scenario():
        engine, Session, ds_dir, ds_id = await make_env(tmp_path, {"1.png": b"OLD!"})
        imgdir = ds_dir / "images"
        async with Session() as db:
            snap = await version_service.create_snapshot(db, ds_id, "s1", "")
            a = await _get_by_filename(db, ds_id, "1.png")
            original_id = a.id
            await _delete_like_router(db, a)
            # a newer upload takes the freed name, with different content
            ds = await db.get(Dataset, ds_id)
            db.add(_image_row(ds, ds_dir, "1.png", b"NEW!"))
            await db.commit()

            result = await version_service.restore_snapshot(
                db, ds_id, snap.id, handle_extra_images="keep",
                pre_restore_snapshot=False)
            assert result["images_re_created"] == 1

            restored = await _get_by_filename(db, ds_id, "1.png")
            assert restored is not None and restored.id == original_id
            extra = await _get_by_filename(db, ds_id, "1_001.png")
            assert extra is not None, "extra should be renamed aside, not lost"
        assert (imgdir / "1.png").read_bytes() == b"OLD!"
        assert (imgdir / "1_001.png").read_bytes() == b"NEW!"
        await engine.dispose()

    run(scenario())


def test_restore_name_reuse_remove_deletes_extra(tmp_path):
    async def scenario():
        engine, Session, ds_dir, ds_id = await make_env(tmp_path, {"1.png": b"OLD!"})
        imgdir = ds_dir / "images"
        async with Session() as db:
            snap = await version_service.create_snapshot(db, ds_id, "s1", "")
            a = await _get_by_filename(db, ds_id, "1.png")
            original_id = a.id
            await _delete_like_router(db, a)
            ds = await db.get(Dataset, ds_id)
            db.add(_image_row(ds, ds_dir, "1.png", b"NEW!"))
            await db.commit()

            result = await version_service.restore_snapshot(
                db, ds_id, snap.id, handle_extra_images="remove",
                pre_restore_snapshot=False)
            assert result["images_re_created"] == 1
            assert result["images_removed"] == 1

            rows = (await db.execute(
                select(Image).where(Image.dataset_id == ds_id))).scalars().all()
            assert len(rows) == 1 and rows[0].id == original_id
        assert (imgdir / "1.png").read_bytes() == b"OLD!"
        await engine.dispose()

    run(scenario())


def test_pre_restore_snapshot_lands_on_active_branch(tmp_path):
    async def scenario():
        engine, Session, ds_dir, ds_id = await make_env(tmp_path, {"1.png": b"AAAA"})
        async with Session() as db:
            s_main = await version_service.create_snapshot(db, ds_id, "main-snap", "")
            branch, s_exp = await version_service.create_branch(db, ds_id, "exp")
            ds = await db.get(Dataset, ds_id)
            ds.current_branch_id = branch.id
            await db.commit()
            img = await _get_by_filename(db, ds_id, "1.png")
            img.aesthetic_score = 9.9  # make the restore non-trivial
            await db.commit()

            result = await version_service.restore_snapshot(
                db, ds_id, s_exp.id, pre_restore_snapshot=True)
            pre = await db.get(DatasetVersion, result["pre_restore_version_id"])
            main = (await db.execute(select(DatasetBranch).where(
                DatasetBranch.dataset_id == ds_id,
                DatasetBranch.name == "main"))).scalar_one()

            assert pre.branch_id == branch.id, "safety snapshot must land on the active branch"
            assert main.head_version_id == s_main.id, "main's head must not move"
        await engine.dispose()

    run(scenario())


def test_restore_after_cross_dataset_move(tmp_path):
    async def scenario():
        engine, Session, ds_dir, ds_id = await make_env(tmp_path, {"1.png": b"AAAA"})
        imgdir = ds_dir / "images"
        ds2_dir = tmp_path / "ds2"
        (ds2_dir / "images").mkdir(parents=True)
        (ds2_dir / "thumbnails").mkdir(parents=True)
        async with Session() as db:
            ds2 = Dataset(name="t2", folder_path=str(ds2_dir))
            db.add(ds2)
            await db.commit()

            snap = await version_service.create_snapshot(db, ds_id, "s1", "")
            img = await _get_by_filename(db, ds_id, "1.png")
            moved_id = img.id
            # simulate routers/images.py::batch_move_dataset (hook, then DB, then FS)
            await version_service.mark_image_deleted_in_versions(img.id, img.file_path, db)
            new_path = ds2_dir / "images" / "1.png"
            img.dataset_id, img.file_path = ds2.id, str(new_path)
            img.thumbnail_path = str(ds2_dir / "thumbnails" / "1.webp")
            await db.commit()
            Path(imgdir / "1.png").rename(new_path)

            result = await version_service.restore_snapshot(
                db, ds_id, snap.id, pre_restore_snapshot=False)
            assert result["files_failed"] == 0
            assert result["images_re_created"] == 1

            restored = await _get_by_filename(db, ds_id, "1.png")
            assert restored is not None
            assert restored.id != moved_id, "moved-away ID must not be re-used"
            moved = await db.get(Image, moved_id)
            assert moved.dataset_id == ds2.id, "moved image must be untouched"
            assert (imgdir / "1.png").read_bytes() == b"AAAA"

            # restoring again must adopt the copy, not duplicate it
            await version_service.restore_snapshot(
                db, ds_id, snap.id, pre_restore_snapshot=False)
            rows = (await db.execute(
                select(Image).where(Image.dataset_id == ds_id))).scalars().all()
            assert len(rows) == 1
        await engine.dispose()

    run(scenario())


def test_restore_aborts_when_pre_snapshot_fails(tmp_path, monkeypatch):
    async def scenario():
        engine, Session, ds_dir, ds_id = await make_env(tmp_path, {"1.png": b"AAAA"})
        imgdir = ds_dir / "images"
        async with Session() as db:
            snap = await version_service.create_snapshot(db, ds_id, "s1", "")
            img = await _get_by_filename(db, ds_id, "1.png")
            img.aesthetic_score = 9.9
            await db.commit()

            async def boom(*args, **kwargs):
                raise RuntimeError("disk on fire")

            monkeypatch.setattr(version_service, "create_snapshot", boom)
            with pytest.raises(RuntimeError, match="Pre-restore snapshot failed"):
                await version_service.restore_snapshot(
                    db, ds_id, snap.id, pre_restore_snapshot=True)
            await db.rollback()

            await db.refresh(img)
            assert img.aesthetic_score == 9.9, "aborted restore must change nothing"
        assert (imgdir / "1.png").read_bytes() == b"AAAA"
        await engine.dispose()

    run(scenario())
