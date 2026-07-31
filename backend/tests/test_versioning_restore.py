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
- restore's stale-file cleanup unlinked a raw stored `file_path`, so a
  hand-edited column made `handle_extra_images="remove"` delete outside the
  datasets tree (V-83)
- three of the ten `*_score` columns had no `VersionImageState` mirror, so a
  restore blanked them and a diff never reported them as changed, on the false
  premise that a technical score is recomputed on demand
"""
import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import settings as app_settings
from backend.database import Base
import backend.models  # noqa: F401 — register all models on Base
from backend.models.dataset import Dataset
from backend.models.image import Image
from backend.models.threshold_settings import ThresholdSettings
from backend.models.versioning import DatasetBranch, DatasetVersion, VersionImageState
from backend.models.video import Video
from backend.services import version_service


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _datasets_root(tmp_path, monkeypatch):
    """Point `settings.datasets_dir` at this test's scratch root.

    `dataset_service` only ever creates a folder at `settings.datasets_dir /
    slug`, so *every* real dataset is inside that tree — and
    `_remove_stale_files` gates each path against it (V-83). Without this fixture
    the module's dataset folders sit outside the app's real datasets dir, the
    guard refuses all of them, and the removals these tests exercise silently
    become no-ops that still pass.
    """
    root = tmp_path / "datasets"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(app_settings, "datasets_dir", root)
    return root


async def make_env(
    tmp_path: Path,
    names_contents: dict[str, bytes],
    mode: str = "auto",
    *,
    foreign_keys: bool = False,
):
    """Engine + session factory + one dataset with the given image files.

    `foreign_keys` opts this engine into the `PRAGMA foreign_keys=ON` that
    `backend/database.py` installs on the app engine. SQLite defaults it OFF per
    connection, so without it every FK in the schema is unenforced here and a
    restore that writes a dangling `Image.source_video_id` passes silently —
    exactly the blind spot `test_duplicate_video_fk_enforced.py` documents.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    if foreign_keys:
        from sqlalchemy import event

        @event.listens_for(engine.sync_engine, "connect")
        def _fk_on(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    ds_dir = tmp_path / "datasets" / "ds"
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


def test_restore_remove_skips_an_extra_image_whose_path_escaped(tmp_path):
    """V-83's third site: `_remove_stale_files` gates each path itself.

    `handle_extra_images="remove"` is its riskiest caller — it hands the helper an
    arbitrary row's stored `file_path`/`thumbnail_path` straight through — so a
    hand-edited column used to make a restore unlink outside the datasets tree.
    The escaped file survives, the extra row still goes, and the neighbour extra's
    file is unlinked normally. The sibling-directory name is deliberate: the guard
    is containment, not a string `startswith`, so `{root}_backup` must not pass a
    `{root}` base. See `test_path_containment_http.py` for the request-level half
    of this contract — this one is service-level because restore has no job
    endpoint of its own.
    """
    async def scenario():
        engine, Session, ds_dir, ds_id = await make_env(tmp_path, {"1.png": b"OLD!"})
        imgdir = ds_dir / "images"
        outside = tmp_path / "datasets_backup"
        outside.mkdir(parents=True)
        escaped = outside / "keep-me.png"
        escaped.write_bytes(b"NOT OURS")
        assert str(escaped).startswith(str(app_settings.datasets_dir)), (
            "the fixture must share the datasets dir's string prefix or it does "
            "not test anything"
        )

        async with Session() as db:
            snap = await version_service.create_snapshot(db, ds_id, "s1", "")
            ds = await db.get(Dataset, ds_id)
            # Two images added since the snapshot: both are "extra" and both get
            # removed. One's row has been pointed outside the tree.
            db.add(_image_row(ds, ds_dir, "bad.png", b"BAD!"))
            db.add(_image_row(ds, ds_dir, "good.png", b"GOOD"))
            await db.commit()
            bad = await _get_by_filename(db, ds_id, "bad.png")
            bad.file_path = str(escaped)
            await db.commit()
            good_file = imgdir / "good.png"

            result = await version_service.restore_snapshot(
                db, ds_id, snap.id, handle_extra_images="remove",
                pre_restore_snapshot=False)
            assert result["images_removed"] == 2

            rows = (await db.execute(
                select(Image).where(Image.dataset_id == ds_id))).scalars().all()
            assert [r.filename for r in rows] == ["1.png"], \
                "the rows go regardless — an undeletable row is the worse failure"

        assert escaped.read_bytes() == b"NOT OURS", "an out-of-tree path must never be unlinked"
        assert not good_file.exists(), "the neighbour extra must still be cleaned up"
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
        ds2_dir = tmp_path / "datasets" / "ds2"
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


SCORE_COLUMNS = sorted(c.key for c in Image.__table__.columns if c.key.endswith("_score"))


def _distinct_scores() -> dict[str, float]:
    """A different value per score column, so a mirror wired to the wrong
    neighbour fails as loudly as a missing one."""
    return {name: 0.1 + i / 100 for i, name in enumerate(SCORE_COLUMNS)}


def test_restore_puts_back_every_score(tmp_path):
    """All ten `*_score` columns are snapshotted and restored — covering
    `create_snapshot`'s mirror and the restore write-back in one round trip.

    Three of them (`nsfw_score`, `saturation_score`, `luminance_score`) were
    unmirrored until d1c7b4e9f0a3 on the theory that a score is recomputed on
    demand. Nothing recomputes one: quality scoring is a manual job, so a restore
    silently dropped a value that existed nowhere else — while preserving
    `color_score`, which the same scorer writes in the same pass.
    """
    async def scenario():
        engine, Session, ds_dir, ds_id = await make_env(tmp_path, {"1.png": b"AAAA"})
        scores = _distinct_scores()
        async with Session() as db:
            img = await _get_by_filename(db, ds_id, "1.png")
            for name, value in scores.items():
                setattr(img, name, value)
            await db.commit()

            snap = await version_service.create_snapshot(db, ds_id, "s1", "")

            for name in scores:
                setattr(img, name, None)
            await db.commit()

            await version_service.restore_snapshot(
                db, ds_id, snap.id, pre_restore_snapshot=False)

            await db.refresh(img)
            assert {name: getattr(img, name) for name in scores} == scores
        await engine.dispose()

    run(scenario())


def test_diff_reports_a_changed_technical_score(tmp_path):
    """A score is mutable — every quality re-run can change one — so it belongs
    in the diff as well as the mirror. `_DIFF_COLS`' carve-out is for *immutable*
    columns (frame lineage, written once by extraction).
    """
    async def scenario():
        engine, Session, ds_dir, ds_id = await make_env(tmp_path, {"1.png": b"AAAA"})
        async with Session() as db:
            img = await _get_by_filename(db, ds_id, "1.png")
            img.luminance_score = 0.2
            img.nsfw_score = 0.9
            await db.commit()
            a = await version_service.create_snapshot(db, ds_id, "s1", "")

            img.luminance_score = 0.7
            img.nsfw_score = 0.1
            await db.commit()
            b = await version_service.create_snapshot(db, ds_id, "s2", "")

            diff = await version_service.diff_versions(db, ds_id, a.id, b.id)
            assert diff["summary"]["modified"] == 1, diff
            changes = diff["modified"][0]["changes"]
            # Floats, not `_HEAVY_DIFF_FIELDS`, so both values are reported.
            assert changes["luminance_score"] == {"from": 0.2, "to": 0.7}
            assert changes["nsfw_score"] == {"from": 0.9, "to": 0.1}
        await engine.dispose()

    run(scenario())


def test_restore_after_the_source_video_was_deleted(tmp_path):
    """A snapshot outlives its video; restoring it must not re-impose the FK.

    `VersionImageState.source_video_id` deliberately carries no foreign key, so a
    snapshot keeps naming a video the user later deletes — and deleting a video
    is a supported action that NULLs the live frames' lineage rather than
    destroying curated data. `Image.source_video_id` *is* a real FK, and
    `ondelete="SET NULL"` only covers deleting the parent: updating a child to a
    parent key that no longer exists still violates the constraint.

    So the Pass 2c write-back cannot copy the stored id blindly. Before the fix
    the commit raised `IntegrityError: FOREIGN KEY constraint failed`, the job
    ended `failed`, and every retry failed identically — the snapshot was
    permanently un-restorable while still burning a pre-restore auto-snapshot per
    attempt. The whole suite missed it because the harness leaves the pragma off.
    """
    async def scenario():
        engine, Session, ds_dir, ds_id = await make_env(
            tmp_path, {"frame.png": b"AAAA"}, mode="manual", foreign_keys=True)
        (ds_dir / "videos").mkdir(parents=True, exist_ok=True)
        async with Session() as db:
            video = Video(
                dataset_id=ds_id, filename="clip.mp4", original_filename="clip.mp4",
                file_path=str(ds_dir / "videos" / "clip.mp4"),
            )
            db.add(video)
            await db.flush()
            video_id = video.id

            frame = await _get_by_filename(db, ds_id, "frame.png")
            frame.source_video_id = video_id
            frame.source_timestamp_ms = 4321
            frame.source_shot_index = 7
            await db.commit()

            snap = await version_service.create_snapshot(db, ds_id, "s1", "")

            # Delete the video the way routers/videos.py::delete_video does:
            # the frames survive with their lineage NULLed.
            frame.source_video_id = None
            await db.delete(video)
            await db.commit()

            # The snapshot still names the dead video — that is by design.
            state = (await db.execute(select(VersionImageState).where(
                VersionImageState.version_id == snap.id))).scalar_one()
            assert state.source_video_id == video_id

            result = await version_service.restore_snapshot(
                db, ds_id, snap.id, pre_restore_snapshot=False)
            assert result["files_failed"] == 0

            await db.refresh(frame)
            # Lineage that can no longer resolve comes back NULL, not dangling.
            assert frame.source_video_id is None
            # The rest of the lineage trio is not derived-from-elsewhere and travels.
            assert frame.source_timestamp_ms == 4321
            assert frame.source_shot_index == 7
        await engine.dispose()

    run(scenario())


def test_restore_keeps_lineage_when_the_video_still_exists(tmp_path):
    """The guard must not become a blanket NULL: a live video still restores."""
    async def scenario():
        engine, Session, ds_dir, ds_id = await make_env(
            tmp_path, {"frame.png": b"AAAA"}, mode="manual", foreign_keys=True)
        (ds_dir / "videos").mkdir(parents=True, exist_ok=True)
        async with Session() as db:
            video = Video(
                dataset_id=ds_id, filename="clip.mp4", original_filename="clip.mp4",
                file_path=str(ds_dir / "videos" / "clip.mp4"),
            )
            db.add(video)
            await db.flush()
            video_id = video.id

            frame = await _get_by_filename(db, ds_id, "frame.png")
            frame.source_video_id = video_id
            frame.source_timestamp_ms = 900
            await db.commit()

            snap = await version_service.create_snapshot(db, ds_id, "s1", "")

            # Something unrelated changes, then the user restores.
            frame.source_video_id = None
            frame.caption_text = "edited"
            await db.commit()

            await version_service.restore_snapshot(
                db, ds_id, snap.id, pre_restore_snapshot=False)
            await db.refresh(frame)
            assert frame.source_video_id == video_id
            assert frame.source_timestamp_ms == 900
        await engine.dispose()

    run(scenario())
