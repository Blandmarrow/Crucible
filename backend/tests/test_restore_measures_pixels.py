"""Restore measures what it wrote rather than trusting a mirror (GPT-016, restore half).

`VersionImageState` does not mirror `phash`, and the comment claiming restore
"recomputes it from the restored file" was false: an in-place restore kept the
edited image's phash over the original's pixels, and a row restore re-created got
`phash=None`, which drops it out of every duplicate scan. `duplicate_dataset`
from a snapshot had the same NULL.

Measuring is the fix, not mirroring: `Image.phash` is itself stale after a resize
or batch crop (016's open half), so a mirror backfilled from it would copy that
staleness into history, and it would need a migration. `_measure_into` reads the
written file for phash, size, format and geometry.
"""
import io
import random
from pathlib import Path

import imagehash
from PIL import Image as PILImage
from sqlalchemy import select

from backend.models import Dataset
from backend.models.image import Image
from backend.services import dataset_service, version_service
from backend.tests.conftest import API, api_env, run, upload_image


def _patterned_png(w=128, h=96) -> bytes:
    """Blocky noise: phash needs structure, and a crop of it changes the hash."""
    rnd = random.Random(0)
    im = PILImage.new("RGB", (w, h))
    px = im.load()
    cols = [(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(40)]
    for y in range(h):
        for x in range(w):
            px[x, y] = cols[((x // 12) * 7 + (y // 12) * 13) % 40]
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def _actual(path):
    with PILImage.open(path) as f:
        return str(imagehash.phash(f)), f.size


async def _mode(env, mode):
    r = await env.client.patch(f"{API}/settings/thresholds", json={"versioning_mode": mode})
    assert r.status_code == 200, r.text


async def _row(env, img_id):
    async with env.Session() as db:
        return await db.get(Image, img_id)


def test_an_in_place_restore_measures_phash_and_size(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            await _mode(env, "auto")
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", _patterned_png())
            h0 = (await _row(env, img["id"])).phash
            async with env.Session() as db:
                snap = await version_service.create_snapshot(db, ds["id"], "s1", "")
            r = await env.client.post(
                f"{API}/images/{img['id']}/crop",
                json={"x": 0, "y": 0, "width": 64, "height": 48, "replace": True},
            )
            assert r.status_code == 200, r.text
            cropped = await _row(env, img["id"])
            assert cropped.phash != h0
            # Make the size mirror wrong too, so only a measurement can fix it.
            async with env.Session() as db:
                from backend.models.versioning import VersionImageState
                st = (await db.execute(select(VersionImageState).where(
                    VersionImageState.version_id == snap.id))).scalars().one()
                st.file_size_bytes = 1
                await db.commit()

            async with env.Session() as db:
                await version_service.restore_snapshot(
                    db, ds["id"], snap.id, pre_restore_snapshot=False
                )
            after = await _row(env, img["id"])
            actual, size = _actual(after.file_path)
            assert size == (128, 96) == (after.width, after.height)
            assert actual == h0 == after.phash
            assert after.file_size_bytes == Path(after.file_path).stat().st_size
    run(scenario())


def test_a_re_created_row_gets_a_measured_phash(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            await _mode(env, "auto")
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "b.png", _patterned_png(64, 64))
            async with env.Session() as db:
                snap = await version_service.create_snapshot(db, ds["id"], "s2", "")
            assert (await env.client.delete(f"{API}/images/{img['id']}")).status_code == 204
            async with env.Session() as db:
                res = await version_service.restore_snapshot(
                    db, ds["id"], snap.id, pre_restore_snapshot=False
                )
            assert res["images_re_created"] == 1
            row = await _row(env, img["id"])
            assert row is not None and row.phash is not None
            assert row.phash == _actual(row.file_path)[0]
    run(scenario())


def test_a_duplicate_made_from_a_snapshot_has_a_phash(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            await _mode(env, "manual")
            ds = await env.create_dataset("d")
            await upload_image(env, ds["id"], "c.png", _patterned_png())
            async with env.Session() as db:
                snap = await version_service.create_snapshot(db, ds["id"], "s", "")
            async with env.Session() as db:
                summary = await dataset_service.duplicate_dataset(
                    db, await db.get(Dataset, ds["id"]), "clone",
                    job_id=None, source_version_id=snap.id,
                )
            async with env.Session() as db:
                rows = (await db.execute(select(Image).where(
                    Image.dataset_id == summary["dataset_id"]))).scalars().all()
            assert len(rows) == 1
            assert rows[0].phash is not None
            assert rows[0].phash == _actual(rows[0].file_path)[0]
    run(scenario())
