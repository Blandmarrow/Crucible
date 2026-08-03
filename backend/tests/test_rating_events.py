"""`image_rating_events` — the append-only history behind the self-agreement ceiling.

`POST /images/bulk-rating` is the sole writer, and the log's whole value depends
on properties that are easy to "optimise" away without noticing:

- **An unchanged re-rating still writes an event.** Suppress the no-ops and the
  log holds only disagreements, so the ceiling computes to 0% agreement forever.
  That is the single most load-bearing test in this file.
- **`batch_size` records the size of the write**, not 1. It is what separates "I
  looked at this image and pressed 4" from "I swept 1,970 images to Cut", the
  largest bias in the ceiling, and it is the only fact here that exists solely at
  write time and cannot be reconstructed afterwards.
- **A restore writes no events**, and the deliberate non-invariant that follows:
  after a restore `images.aesthetic_rating` can disagree with the last event.
  Pinned here so a future "make the log authoritative" change has to argue.

Rows are seeded directly rather than uploaded where the test is about query and
transaction shape (`test_image_select_all_scope.py`'s `_mk`); real PNG encodes
buy nothing there.
"""
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import settings as app_settings
from backend.database import Base
import backend.models  # noqa: F401 — register every model on Base
from backend.models import Image, ImageRatingEvent
from backend.models.dataset import Dataset
from backend.models.threshold_settings import ThresholdSettings
from backend.models.versioning import VersionImageState
from backend.routers import images as images_router
from backend.services import version_service
from backend.tests.conftest import API, api_env, run


def _mk(dataset_id: str, i: int, **kw) -> Image:
    return Image(
        dataset_id=dataset_id,
        filename=f"img_{i:03d}.png",
        original_filename=f"img_{i:03d}.png",
        file_path=f"/tmp/{dataset_id}/img_{i:03d}.png",
        **kw,
    )


async def _seed(env, dataset_id: str, n: int = 4) -> list[str]:
    rows = [_mk(dataset_id, i) for i in range(n)]
    async with env.Session() as db:
        db.add_all(rows)
        await db.commit()
        return [r.id for r in rows]


async def _events(env, image_id: str | None = None) -> list[ImageRatingEvent]:
    async with env.Session() as db:
        q = select(ImageRatingEvent).order_by(ImageRatingEvent.id)
        if image_id is not None:
            q = q.where(ImageRatingEvent.image_id == image_id)
        return list((await db.execute(q)).scalars().all())


async def _rate(env, dataset_id: str, ids: list[str], rating: int | None):
    return await env.client.post(
        f"{API}/images/bulk-rating",
        json={"dataset_id": dataset_id, "image_ids": ids, "rating": rating},
    )


def test_a_rating_write_logs_one_event_per_image(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            ids = await _seed(env, ds["id"])

            r = await _rate(env, ds["id"], ids[:2], 4)
            assert r.status_code == 200, r.text

            events = await _events(env)
            assert len(events) == 2
            assert {e.image_id for e in events} == set(ids[:2])
            assert {e.rating for e in events} == {4}
            assert {e.dataset_id for e in events} == {ds["id"]}
            # The whole write shares one timestamp, which is why ordering is by id.
            assert len({e.created_at for e in events}) == 1
            assert [e.id for e in events] == sorted(e.id for e in events)

    run(scenario())


def test_an_unchanged_re_rating_still_writes_an_event(tmp_path):
    """The property the ceiling is built on.

    `bulk_rating` already argues it for `rating_stale` — looking again is the
    whole event the bit is about. If a no-op re-rate were suppressed, the only
    pairs in the log would be disagreements and self-agreement would read 0%
    forever.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            ids = await _seed(env, ds["id"], n=1)

            await _rate(env, ds["id"], ids, 3)
            await _rate(env, ds["id"], ids, 3)  # identical answer, second look

            events = await _events(env, ids[0])
            assert [e.rating for e in events] == [3, 3]

    run(scenario())


def test_clearing_a_rating_is_an_event_with_a_null_rating(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            ids = await _seed(env, ds["id"], n=1)

            await _rate(env, ds["id"], ids, 2)
            await _rate(env, ds["id"], ids, None)

            events = await _events(env, ids[0])
            assert [e.rating for e in events] == [2, None]
            async with env.Session() as db:
                img = await db.get(Image, ids[0])
                assert img.aesthetic_rating is None

    run(scenario())


def test_batch_size_records_the_write_not_one(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            ids = await _seed(env, ds["id"], n=4)

            await _rate(env, ds["id"], ids, 1)          # a 4-image sweep
            await _rate(env, ds["id"], ids[:1], 4)      # one considered look

            events = await _events(env)
            sweep = [e for e in events if e.rating == 1]
            single = [e for e in events if e.rating == 4]
            assert len(sweep) == 4 and {e.batch_size for e in sweep} == {4}
            assert len(single) == 1 and single[0].batch_size == 1

    run(scenario())


def test_a_whole_dataset_write_stamps_the_resolved_size(tmp_path):
    """`batch_size` is the size of the *resolved* selection, so a
    select-all-matching-filters sweep is as identifiable as an explicit id list."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await _seed(env, ds["id"], n=5)

            r = await env.client.post(
                f"{API}/images/bulk-rating",
                json={"dataset_id": ds["id"], "rating": 1},  # no image_ids
            )
            assert r.json() == {"updated": 5}
            assert {e.batch_size for e in await _events(env)} == {5}

    run(scenario())


def test_a_busy_dataset_writes_neither_rating_nor_event(tmp_path):
    """One transaction: the 409 fires before either statement, so there is no
    state in which the rating landed and its event did not."""
    async def scenario():
        async with api_env(tmp_path) as env:
            from backend.services import dataset_busy

            ds = await env.create_dataset("d")
            ids = await _seed(env, ds["id"], n=2)

            with dataset_busy.busy(ds["id"], "versioning"):
                r = await _rate(env, ds["id"], ids, 4)
            assert r.status_code == 409, r.text

            assert await _events(env) == []
            async with env.Session() as db:
                img = await db.get(Image, ids[0])
                assert img.aesthetic_rating is None

    run(scenario())


def test_events_chunk_with_the_id_list(tmp_path, monkeypatch):
    """The INSERT … SELECT rides the same `chunked()` loop as the UPDATE and uses
    the same `in_(batch)` predicate, so the two cannot cover different sets.

    The patch **counts**, and `seen` is asserted, because 7 events is exactly
    what an *unchunked* pass produces too: if `bulk_rating` ever stopped
    resolving `chunked` as a module global, the monkeypatch would silently no-op
    and this test would stay green testing nothing. `[2, 2, 2, 1]` is the one
    observation a single unchunked pass cannot make.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            # Seeded before the patch is installed, so nothing but the rating
            # write is measured.
            ids = await _seed(env, ds["id"], n=7)

            real_chunked = images_router.chunked
            seen: list[int] = []

            def counting_chunked(seq, size=2):
                for batch in real_chunked(seq, 2):
                    seen.append(len(batch))
                    yield batch

            monkeypatch.setattr(images_router, "chunked", counting_chunked)
            r = await _rate(env, ds["id"], ids, 2)
            assert r.json() == {"updated": 7}
            # One loop drives both statements, so seven ids at size 2 is four
            # passes and not eight.
            assert seen == [2, 2, 2, 1]

            events = await _events(env)
            assert len(events) == 7
            assert {e.image_id for e in events} == set(ids)
            # batch_size is the whole write, not the chunk it happened to land in.
            assert {e.batch_size for e in events} == {7}

    run(scenario())


def test_a_cross_dataset_selection_stamps_each_event_with_its_own_image_dataset(tmp_path):
    """`dataset_id` comes from `Image.dataset_id`, per row — not from the body.

    An explicit `image_ids` selection can span datasets, which is the whole
    reason `bulk_rating` guards *every* dataset it touches rather than
    `body.dataset_id` alone. Every other test in this file rates within one
    dataset, where a `literal(body.dataset_id)` would be indistinguishable.

    No `foreign_keys=True`: the column carries no FK by design, so the pragma
    would prove nothing here.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            a = await env.create_dataset("a")
            b = await env.create_dataset("b")
            ids_a = await _seed(env, a["id"], n=2)
            ids_b = await _seed(env, b["id"], n=2)

            # One write, addressed with `a` as the body's dataset, spanning both.
            r = await _rate(env, a["id"], ids_a + ids_b, 3)
            assert r.json() == {"updated": 4}

            events = await _events(env)
            assert {e.image_id: e.dataset_id for e in events} == {
                **{i: a["id"] for i in ids_a},
                **{i: b["id"] for i in ids_b},
            }
            # Stated separately so the failure names itself: stamping the body's
            # dataset onto every row collapses this to `{a}`.
            assert {e.dataset_id for e in events} == {a["id"], b["id"]}

    run(scenario())


def test_deleting_an_image_cascades_its_events(tmp_path):
    """Requires `foreign_keys=True`: SQLite defaults the pragma OFF per connection
    and this harness builds its own engine, so without it every FK — and every
    CASCADE — in the schema is unenforced and this test passes for free."""
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = await env.create_dataset("d")
            ids = await _seed(env, ds["id"], n=2)
            await _rate(env, ds["id"], ids, 4)
            assert len(await _events(env)) == 2

            async with env.Session() as db:
                await db.delete(await db.get(Image, ids[0]))
                await db.commit()

            remaining = await _events(env)
            assert [e.image_id for e in remaining] == [ids[1]]

    run(scenario())


def test_a_cross_dataset_copy_carries_the_rating_but_not_the_events(tmp_path):
    """A copy mints a new `Image.id`. The rating travels as authored data; the
    events do not, because carrying them would count one human decision twice in
    the ceiling."""
    async def scenario():
        async with api_env(tmp_path) as env:
            from backend.tests.conftest import upload_image

            src = await env.create_dataset("src")
            dst = await env.create_dataset("dst")
            img = await upload_image(env, src["id"], "a.png")

            await _rate(env, src["id"], [img["id"]], 4)
            assert len(await _events(env)) == 1

            r = await env.client.post(
                f"{API}/images/batch/copy-dataset",
                json={"image_ids": [img["id"]], "target_dataset_id": dst["id"]},
            )
            assert r.status_code == 200, r.text

            async with env.Session() as db:
                copies = (await db.execute(
                    select(Image).where(Image.dataset_id == dst["id"])
                )).scalars().all()
            assert len(copies) == 1
            assert copies[0].aesthetic_rating == 4
            assert copies[0].id != img["id"]

            events = await _events(env)
            assert [e.image_id for e in events] == [img["id"]]

    run(scenario())


def test_version_image_state_carries_no_rating_event_columns():
    """The mirroring rule is about columns on `Image`; this is a separate table of
    human acts. A future "for consistency" mirror has to argue with this test —
    and with `test_a_restore_writes_no_events…`, which is the reason."""
    cols = set(VersionImageState.__table__.columns.keys())
    assert not [c for c in cols if "event" in c]
    assert "batch_size" not in cols


# ---------------------------------------------------------------------------
# Restore. Service-level rather than over HTTP, following
# `test_versioning_restore.py`: the property is about what `restore_snapshot`
# writes, and the versioning routes add a job queue that buys nothing here.
# ---------------------------------------------------------------------------


@pytest.fixture
def _datasets_root(tmp_path, monkeypatch):
    root = tmp_path / "datasets"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(app_settings, "datasets_dir", root)
    return root


def test_a_restore_writes_no_events_and_may_disagree_with_the_log(
    tmp_path, _datasets_root
):
    """An event means "a human looked at these pixels and said this"; a rollback
    is not that. Replaying one would synthesise a disagreement the user never
    made, in the exact statistic this log exists to produce.

    The documented cost is asserted here too: `images.aesthetic_rating` can
    disagree with the last event, so **that is not an invariant** and nothing may
    derive the current rating from the log.
    """
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/v.db")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, expire_on_commit=False)

        ds_dir = _datasets_root / "ds"
        (ds_dir / "images").mkdir(parents=True)
        (ds_dir / "thumbnails").mkdir(parents=True)
        fp = ds_dir / "images" / "a.png"
        fp.write_bytes(b"AAAA")

        async with Session() as db:
            ds = Dataset(name="t", folder_path=str(ds_dir))
            db.add(ds)
            db.add(ThresholdSettings(id=1, versioning_mode="auto"))
            await db.flush()
            img = Image(
                dataset_id=ds.id, filename="a.png", original_filename="a.png",
                subfolder="", file_path=str(fp),
                thumbnail_path=str(ds_dir / "thumbnails" / "a.webp"),
                aesthetic_rating=1,
            )
            db.add(img)
            await db.commit()
            ds_id, image_id = ds.id, img.id
            # One event for the rating above, as `bulk_rating` would have written.
            db.add(ImageRatingEvent(
                image_id=image_id, dataset_id=ds_id, rating=1, batch_size=1,
            ))
            await db.commit()

        async with Session() as db:
            snap = await version_service.create_snapshot(db, ds_id, "s1", "")
            version_id = snap.id

        # A considered re-rating after the snapshot: 1 -> 4, with its event.
        async with Session() as db:
            img = await db.get(Image, image_id)
            img.aesthetic_rating = 4
            db.add(ImageRatingEvent(
                image_id=image_id, dataset_id=ds_id, rating=4, batch_size=1,
            ))
            await db.commit()

        async with Session() as db:
            await version_service.restore_snapshot(
                db, ds_id, version_id, pre_restore_snapshot=False
            )

        async with Session() as db:
            events = list((await db.execute(
                select(ImageRatingEvent).order_by(ImageRatingEvent.id)
            )).scalars().all())
            img = await db.get(Image, image_id)

        # The restore rolled the column back and left the history alone.
        assert [e.rating for e in events] == [1, 4]
        assert img.aesthetic_rating == 1
        # ...which is the deliberate non-invariant: the column now disagrees with
        # the last event, and that is correct.
        assert img.aesthetic_rating != events[-1].rating

        await engine.dispose()

    run(scenario())
