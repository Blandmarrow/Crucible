"""`POST /images/bulk-thumbnails` — the repair for a stale-preview run.

Four jobs re-cut an image thumbnail as a best-effort post-commit epilogue
(`batch_lut`, `batch_upscale`, `crop_upscale`, `video_reextract`). When that
raises, the image is correct and committed but the gallery keeps rendering the
old tile, and nothing self-heals it: `GET /images/{id}/thumbnail` only
regenerates when the file is *missing*, and a stale file exists.

The repair therefore has to do two things a naive "just re-encode" would not:
bump `updated_at`, because the client's cache-buster is derived from it, and
gate both stored paths, because `generate_thumbnail` creates its destination's
parent directory.
"""
from pathlib import Path

from backend.models import BackgroundJob, Image
from backend.tests.conftest import (
    API,
    api_env,
    jpeg_bytes,
    png_bytes,
    run,
    upload_image,
    wait_for_job,
)

JUNK = b"not a webp, and definitely not this image"


async def _row(env, image_id: str) -> Image:
    async with env.Session() as db:
        return await db.get(Image, image_id)


async def _repair(env, dataset_id: str, **body) -> dict:
    r = await env.client.post(f"{API}/images/bulk-thumbnails", json={
        "dataset_id": dataset_id, **body,
    })
    assert r.status_code == 200, r.text
    return await wait_for_job(env, r.json()["job_id"], timeout=60)


def test_regenerate_rewrites_the_thumbnail_and_advances_updated_at(tmp_path):
    """The bytes have to change *and* the row has to move.

    `imagesApi.thumbnailUrlVersioned` builds `?v=${Date.parse(updated_at)}`, so a
    repair that rewrites the `.webp` without touching the row leaves the
    `<img src>` byte-identical and the browser serves the stale tile from cache —
    the repair would visibly do nothing. Bite: drop the `updated_at` bump and the
    second assertion fails while the first still passes.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png")

            row = await _row(env, img["id"])
            thumb = Path(row.thumbnail_path)
            before_updated = row.updated_at
            thumb.write_bytes(JUNK)

            job = await _repair(env, ds["id"])
            assert job["status"] == "completed", job
            assert job["result_data"] == {"regenerated": 1, "failed": 0, "skipped": 0}

            assert thumb.read_bytes() != JUNK, "the stale thumbnail was left in place"
            assert thumb.read_bytes()[:4] == b"RIFF"

            row = await _row(env, img["id"])
            assert row.updated_at > before_updated, \
                "the cache-buster never moved — the browser keeps serving the stale tile"

    run(scenario())


def test_regenerate_refuses_a_thumbnail_path_outside_the_datasets_dir(tmp_path):
    """`generate_thumbnail` `mkdir(parents=True)`s its destination's parent, so an
    ungated `thumbnail_path` is an arbitrary-file-**write** primitive, not merely
    a stale tile. The row is skipped and the job still completes — one poisoned
    row must not cost the rest of the dataset its repair.

    Bite: drop the `contained_path` gate and the file appears outside the tree.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            escaped = await upload_image(env, ds["id"], "escaped.png")
            healthy = await upload_image(env, ds["id"], "healthy.png")

            outside = tmp_path / "outside" / "planted.webp"
            async with env.Session() as db:
                row = await db.get(Image, escaped["id"])
                row.thumbnail_path = str(outside)
                await db.commit()

            healthy_thumb = Path((await _row(env, healthy["id"])).thumbnail_path)
            healthy_thumb.write_bytes(JUNK)

            job = await _repair(env, ds["id"])
            assert job["status"] == "completed", job
            assert job["result_data"] == {"regenerated": 1, "failed": 0, "skipped": 1}

            assert not outside.exists(), "wrote a file outside the datasets tree"
            assert not outside.parent.exists(), "created a directory outside the datasets tree"
            assert healthy_thumb.read_bytes() != JUNK

    run(scenario())


def test_regenerate_scopes_by_subfolder(tmp_path):
    """The scope is the whole point: a deterministic failure means "everything
    that run touched", and a subfolder is how a user names that."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            inside = await upload_image(env, ds["id"], "inside.png")
            outside = await upload_image(env, ds["id"], "outside.png")

            r = await env.client.post(f"{API}/images/batch/move-subfolder", json={
                "image_ids": [inside["id"]], "subfolder": "shots",
            })
            assert r.status_code == 200, r.text

            inside_thumb = Path((await _row(env, inside["id"])).thumbnail_path)
            outside_thumb = Path((await _row(env, outside["id"])).thumbnail_path)
            inside_thumb.write_bytes(JUNK)
            outside_thumb.write_bytes(JUNK)

            job = await _repair(env, ds["id"], subfolder="shots")
            assert job["status"] == "completed", job
            assert job["result_data"]["regenerated"] == 1, job["result_data"]

            assert inside_thumb.read_bytes() != JUNK
            assert outside_thumb.read_bytes() == JUNK, "the repair escaped its subfolder"

    run(scenario())


def test_regenerate_survives_an_unreadable_source_and_reports_it(tmp_path):
    """The repair must not inherit the failure mode it exists to fix: one image
    PIL cannot open costs itself and nothing else.

    Bite: remove the per-row `try/except` and the job ends `failed` with the
    healthy thumbnail never re-cut.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            broken = await upload_image(env, ds["id"], "broken.png")
            healthy = await upload_image(env, ds["id"], "healthy.png")

            Path((await _row(env, broken["id"])).file_path).write_bytes(b"truncated garbage")
            healthy_thumb = Path((await _row(env, healthy["id"])).thumbnail_path)
            healthy_thumb.write_bytes(JUNK)

            job = await _repair(env, ds["id"])
            assert job["status"] == "completed", job
            assert job["result_data"] == {"regenerated": 1, "failed": 1, "skipped": 0}
            assert healthy_thumb.read_bytes() != JUNK

    run(scenario())


def test_regenerate_507s_when_the_volume_is_full_and_creates_no_job(tmp_path, monkeypatch):
    """This is the repair you run *because* the disk filled, so a 507 is the
    answer — not a queued job that logs 400 failures and reports them."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await upload_image(env, ds["id"], "a.png")

            import backend.routers.images as images_router
            from backend.utils import InsufficientDiskSpaceError

            def full(*_args, **_kwargs):
                raise InsufficientDiskSpaceError("Not enough free disk space")

            monkeypatch.setattr(images_router, "require_free_space", full)

            r = await env.client.post(f"{API}/images/bulk-thumbnails", json={
                "dataset_id": ds["id"],
            })
            assert r.status_code == 507, r.text

            async with env.Session() as db:
                from sqlalchemy import select as sa_select
                jobs = (await db.execute(sa_select(BackgroundJob))).scalars().all()
            assert jobs == [], "a job row was created for a request that 507'd"

    run(scenario())


def test_regenerate_covers_a_cross_dataset_selection(tmp_path):
    """An explicit `image_ids` selection can span datasets — the gallery toolbar
    shows a per-dataset breakdown precisely because of that — so `dataset_id` is
    a label for the job row here, never a filter."""
    async def scenario():
        async with api_env(tmp_path) as env:
            a = await env.create_dataset("a")
            b = await env.create_dataset("b")
            img_a = await upload_image(env, a["id"], "a.png", png_bytes())
            img_b = await upload_image(env, b["id"], "b.jpg", jpeg_bytes())

            thumb_a = Path((await _row(env, img_a["id"])).thumbnail_path)
            thumb_b = Path((await _row(env, img_b["id"])).thumbnail_path)
            thumb_a.write_bytes(JUNK)
            thumb_b.write_bytes(JUNK)

            job = await _repair(env, a["id"], image_ids=[img_a["id"], img_b["id"]])
            assert job["status"] == "completed", job
            assert job["result_data"] == {"regenerated": 2, "failed": 0, "skipped": 0}

            assert thumb_a.read_bytes() != JUNK
            assert thumb_b.read_bytes() != JUNK, \
                "the other dataset's image was filtered out of its own selection"

    run(scenario())
