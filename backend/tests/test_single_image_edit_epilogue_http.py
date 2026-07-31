"""The two single-image in-place endpoints keep their thumbnail cut *after* the
commit (PM-013).

`POST /images/{id}/resize` and `POST /images/{id}/crop` with `replace: true` both
overwrite the image's file on disk before they describe the result in the row.
`generate_thumbnail` is fallible — a read-only `thumbnails/`, a full disk — and it
used to run between the two, so an `OSError` there rolled the row back onto a file
that no longer matched it: the geometry, `file_size_bytes`, `phash` and the
`processing_history` entry all reverted while the pixels stayed changed.

The resize half also had no `if img.thumbnail_path` guard at all, so a row with no
thumbnail raised `TypeError` outright and lost the same work.

Both epilogues log and continue: a stale thumbnail is cosmetic. Neither reports a
`thumbnails_stale` count — that is a *job* counter `TopBar` reads off
`job.result_data`, and these endpoints return dicts, not jobs.

No cv2 and no torch: PNG round-trips through Pillow only.
"""
from sqlalchemy import select

from backend.models.image import Image
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image


async def _fresh(env, image_id: str) -> Image:
    """Re-read on a new session — the point is what was *committed*."""
    async with env.Session() as db:
        return (await db.execute(select(Image).where(Image.id == image_id))).scalar_one()


async def _one_image(env, dataset_id: str, name: str = "a.png") -> dict:
    return await upload_image(env, dataset_id, name, png_bytes((10, 20, 30), (40, 20)))


class _FailingThumbnails:
    """Patch `backend.routers.images.generate_thumbnail` to raise, restoring it
    on exit — the shape `test_batch_resize_crop_http.py` uses for the batch twin.
    """

    def __enter__(self):
        from backend.routers import images as images_router

        self._mod = images_router
        self._original = images_router.generate_thumbnail

        def failing(*a, **kw):
            raise OSError("thumbnails/ is read-only")

        images_router.generate_thumbnail = failing
        return self

    def __exit__(self, *exc):
        self._mod.generate_thumbnail = self._original
        return False


def test_a_failed_thumbnail_still_commits_the_single_resize(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await _one_image(env, ds["id"])

            with _FailingThumbnails():
                r = await env.client.post(
                    f"{API}/images/{img['id']}/resize",
                    json={"width": 20, "height": 10, "maintain_ar": False},
                )

            assert r.status_code == 200, r.text
            assert r.json() == {"width": 20, "height": 10}

            row = await _fresh(env, img["id"])
            assert (row.width, row.height) == (20, 10)
            assert [h["op"] for h in row.processing_history] == ["resize"]

    run(scenario())


def test_a_resize_on_a_row_with_no_thumbnail_succeeds(tmp_path):
    """The missing guard: `generate_thumbnail(path, None)` raised before the
    commit, so an image with no thumbnail could not be resized at all — and the
    file on disk had already been rewritten by then."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await _one_image(env, ds["id"])
            async with env.Session() as db:
                row = await db.get(Image, img["id"])
                row.thumbnail_path = None
                await db.commit()

            r = await env.client.post(
                f"{API}/images/{img['id']}/resize",
                json={"width": 20, "height": 10, "maintain_ar": False},
            )
            assert r.status_code == 200, r.text

            row = await _fresh(env, img["id"])
            assert (row.width, row.height) == (20, 10)
            assert [h["op"] for h in row.processing_history] == ["resize"]

    run(scenario())


def test_a_failed_thumbnail_still_commits_the_single_replace_crop(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await _one_image(env, ds["id"])

            with _FailingThumbnails():
                r = await env.client.post(
                    f"{API}/images/{img['id']}/crop",
                    json={"x": 0, "y": 0, "width": 20, "height": 20, "replace": True},
                )

            assert r.status_code == 200, r.text
            assert r.json()["width"] == 20 and r.json()["height"] == 20

            row = await _fresh(env, img["id"])
            assert (row.width, row.height) == (20, 20)
            assert [h["op"] for h in row.processing_history] == ["crop"]
            # The row describes the file that is actually on disk now.
            assert row.phash is not None

    run(scenario())
