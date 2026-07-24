"""Request-level regression tests for image rename/bulk-rename file placement.

The naming helpers (`unique_filename_with_thumb`, the two-phase temp rename in
`bulk_rename`) exist to keep image files, their `.webp` thumbnails, and their `.txt`
sidecars from clobbering one another. No request-level test covered them: a
regression that silently overwrote one image's thumbnail with another's, or lost a
sidecar on rename, would have shipped green. These pin the placement.

Everything keys on the DB `file_path` and unique upload bytes, never on which id
received `image.png` vs `image_001.png` — uploads into an empty dataset get
`sort_order=None` and tie on `created_at`, so position is not deterministic.
Assertions open a fresh `env.Session()` after the request.
"""
from pathlib import Path

from sqlalchemy import select

from backend.models.image import Image
from backend.tests.conftest import (
    API,
    api_env,
    jpeg_bytes,
    png_bytes,
    run,
    upload_image,
)


def _dirs(dataset: dict) -> tuple[Path, Path]:
    root = Path(dataset["folder_path"])
    return root / "images", root / "thumbnails"


async def _rows(env, dataset_id: str) -> list[Image]:
    async with env.Session() as db:
        return list((await db.execute(
            select(Image).where(Image.dataset_id == dataset_id)
        )).scalars().all())


def test_single_rename_onto_occupied_thumb_stem_uniquifies(tmp_path):
    """`a.jpg` renamed onto stem `a` while `a.png` (and its `a.webp` thumbnail)
    already exist: the image name `a.jpg` is free but the thumbnail stem `a` is
    taken, so `unique_filename_with_thumb` must bump to `a_001.jpg`."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            images_dir, thumb_dir = _dirs(ds)

            await upload_image(env, ds["id"], "a.png", png_bytes((10, 120, 200)))
            jpeg = jpeg_bytes((200, 60, 20))
            b = await upload_image(env, ds["id"], "b.jpg", jpeg)

            a_webp_before = (thumb_dir / "a.webp").read_bytes()

            r = await env.client.patch(
                f"{API}/images/{b['id']}/rename", json={"new_stem": "a"}
            )
            assert r.status_code == 200, r.text
            assert r.json()["filename"] == "a_001.jpg"

            assert {p.name for p in images_dir.iterdir()} == {"a.png", "a_001.jpg"}
            assert {p.name for p in thumb_dir.iterdir()} == {"a.webp", "a_001.webp"}
            # The pre-existing thumbnail was not clobbered.
            assert (thumb_dir / "a.webp").read_bytes() == a_webp_before
            assert (images_dir / "a_001.jpg").read_bytes() == jpeg

            async with env.Session() as db:
                row = await db.get(Image, b["id"])
                assert row.filename == "a_001.jpg"
                assert Path(row.file_path).name == "a_001.jpg"
                assert Path(row.thumbnail_path).name == "a_001.webp"

    run(scenario())


def test_rename_moves_caption_sidecar(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            images_dir, _ = _dirs(ds)
            img = await upload_image(env, ds["id"], "a.png")

            r = await env.client.put(
                f"{API}/captions/image/{img['id']}", json={"caption_text": "a cat"}
            )
            assert r.status_code == 200, r.text
            assert (images_dir / "a.txt").read_text(encoding="utf-8") == "a cat"

            r = await env.client.patch(
                f"{API}/images/{img['id']}/rename", json={"new_stem": "renamed"}
            )
            assert r.status_code == 200, r.text

            assert (images_dir / "renamed.png").exists()
            assert (images_dir / "renamed.txt").read_text(encoding="utf-8") == "a cat"
            assert not (images_dir / "a.png").exists()
            assert not (images_dir / "a.txt").exists()

    run(scenario())


def test_bulk_rename_mixed_suffix_thumb_stems_stay_distinct(tmp_path):
    """Three images of different extensions bulk-renamed to one stem must keep
    three distinct filename stems, so their `.webp` thumbnails never collide."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            images_dir, thumb_dir = _dirs(ds)

            up = [
                await upload_image(env, ds["id"], "x.png", png_bytes((1, 2, 3))),
                await upload_image(env, ds["id"], "y.jpg", jpeg_bytes((90, 10, 10))),
                await upload_image(env, ds["id"], "z.png", png_bytes((4, 5, 6))),
            ]
            orig = {i["id"]: (await _row_bytes(env, i["id"])) for i in up}

            r = await env.client.post(
                f"{API}/images/bulk-rename",
                json={"dataset_id": ds["id"], "new_stem": "img"},
            )
            assert r.status_code == 200, r.text
            assert r.json() == {"affected": 3}

            rows = await _rows(env, ds["id"])
            assert len(rows) == 3
            stems = {Path(row.file_path).stem for row in rows}
            assert len(stems) == 3, f"stems collided: {stems}"

            for row in rows:
                assert Path(row.file_path).read_bytes() == orig[row.id]

            thumbs = sorted(p.stem for p in thumb_dir.glob("*.webp"))
            assert len(thumbs) == 3
            assert set(thumbs) == stems
            # No two-phase temp names left behind.
            assert not any("__renaming__" in p.name for p in images_dir.iterdir())
            assert not any("__renaming__" in p.name for p in thumb_dir.iterdir())

    run(scenario())


def test_bulk_rename_swap_permutation_no_clobber(tmp_path):
    """Reorder two identically-stemmed images so each one's target name is the
    other's current name, then renumber: the two-phase temp rename must swap file
    ownership without either blob overwriting the other (PM-001 class)."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            images_dir, _ = _dirs(ds)

            await upload_image(env, ds["id"], "a.png", png_bytes((11, 22, 33)))
            await upload_image(env, ds["id"], "b.png", png_bytes((44, 55, 66)))

            r = await env.client.post(
                f"{API}/images/bulk-rename",
                json={"dataset_id": ds["id"], "new_stem": "pic"},
            )
            assert r.status_code == 200, r.text

            # Capture each id's name and bytes after the first rename
            # (now pic.png/pic_001.png).
            orig = {}
            name_before = {}
            rows = await _rows(env, ds["id"])
            for row in rows:
                orig[row.id] = Path(row.file_path).read_bytes()
                name_before[row.id] = row.filename

            # Reverse the sort order, then renumber honoring it: targets permute.
            rows_sorted = sorted(rows, key=lambda r: r.file_path)
            updates = [
                {"id": rows_sorted[0].id, "sort_order": 1},
                {"id": rows_sorted[1].id, "sort_order": 0},
            ]
            r = await env.client.patch(
                f"{API}/images/batch/reorder",
                json={"dataset_id": ds["id"], "updates": updates},
            )
            assert r.status_code == 200, r.text

            r = await env.client.post(
                f"{API}/images/bulk-rename",
                json={"dataset_id": ds["id"], "new_stem": "pic",
                      "sort_by_sort_order": True},
            )
            assert r.status_code == 200, r.text

            rows = await _rows(env, ds["id"])
            for row in rows:
                # The permutation must actually happen — otherwise the second
                # rename is a no-op and this test stops covering the two-phase
                # temp rename entirely.
                assert row.filename != name_before[row.id]
                # Each blob still findable at its own row's path — nothing clobbered.
                assert Path(row.file_path).read_bytes() == orig[row.id]
            # Both original blobs still present on disk (a permutation, not a loss).
            on_disk = {(images_dir / p.name).read_bytes() for p in images_dir.glob("pic*.png")}
            assert on_disk == set(orig.values())
            assert not any("__renaming__" in p.name for p in images_dir.iterdir())

    run(scenario())


async def _row_bytes(env, image_id: str) -> bytes:
    async with env.Session() as db:
        row = await db.get(Image, image_id)
        return Path(row.file_path).read_bytes()
