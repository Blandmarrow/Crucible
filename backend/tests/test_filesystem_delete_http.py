"""`POST /filesystem/delete` — the delete that is not in `routers/images.py`.

This endpoint destroys registered media just as thoroughly as
`DELETE /images/{id}` does, and until now it did none of the work that surrounds
those deletes: no object-store backup, no busy guard, no orphaned thumbnail or
poster cleanup, no `refresh_stats`, no lineage NULLing — and it had no test at
all. PM-014 is the versioning half of that (a snapshot could no longer
materialize an image the File Browser had deleted); the rest is parity.

Two properties are load-bearing and easy to lose in a refactor:

- **Every fallible statement runs before the `rmtree`/`unlink`.** The rows are
  gathered, the dataset is checked for busy-ness, the hook fires and the deletes
  are staged and flushed — then the filesystem is touched, then `commit()`. A
  failed filesystem delete still leaves the rows intact, because nothing was
  committed; that is the guarantee `test_a_failed_filesystem_delete_leaves_the_rows_intact`
  pins from the other side.
- **The prefix match is an escaped LIKE.** `_` is a single-character LIKE
  wildcard *and* the character `_name_to_slug` puts in every multi-word dataset
  folder, so an unescaped prefix let one dataset's delete take another's rows.
"""

import shutil
from pathlib import Path

from sqlalchemy import select

from backend.models import Image, Video
from backend.models.versioning import VersionImageState
from backend.services import version_service
from backend.tests.conftest import (
    API,
    api_env,
    needs_cv2,
    run,
    upload_image,
    upload_video,
)

FS = f"{API}/filesystem"


async def _row(env, model, row_id: str):
    async with env.Session() as db:
        return await db.get(model, row_id)


def test_deleting_a_registered_image_file_removes_the_row_the_thumbnail_and_the_sidecar(tmp_path):
    """The file branch's orphan rule: `p.unlink()` takes the image and nothing
    else, so the thumbnail beside it in `{ds}/thumbnails/` and the `.txt` sidecar
    beside it in `images/` both have to be removed explicitly — which is exactly
    what all three deletes in `routers/images.py` already do."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "pic.png")

            row = await _row(env, Image, img["id"])
            file_path, thumb = Path(row.file_path), Path(row.thumbnail_path)
            sidecar = file_path.with_suffix(".txt")
            sidecar.write_text("a caption", encoding="utf-8")
            assert thumb.exists()

            r = await env.client.post(f"{FS}/delete", json={"path": str(file_path)})
            assert r.status_code == 200, r.text

            assert not file_path.exists()
            assert not thumb.exists()
            assert not sidecar.exists()
            assert await _row(env, Image, img["id"]) is None

    run(scenario())


@needs_cv2
def test_deleting_a_registered_video_file_removes_the_poster_and_refreshes_stats(tmp_path):
    """V-23: the counts are separate columns, and nothing else recomputes them —
    a dataset card kept claiming a video it no longer had."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")

            row = await _row(env, Video, video["id"])
            file_path, poster = Path(row.file_path), Path(row.poster_path)
            assert poster.exists()

            before = (await env.client.get(f"{API}/datasets/{ds['id']}")).json()
            assert before["video_count"] == 1 and before["video_size_bytes"] > 0

            r = await env.client.post(f"{FS}/delete", json={"path": str(file_path)})
            assert r.status_code == 200, r.text

            assert not file_path.exists()
            assert not poster.exists()
            assert await _row(env, Video, video["id"]) is None

            after = (await env.client.get(f"{API}/datasets/{ds['id']}")).json()
            assert after["video_count"] == 0
            assert after["video_size_bytes"] == 0

    run(scenario())


def test_deleting_the_images_folder_unlinks_the_thumbnails_beside_it(tmp_path):
    """The reason the orphan rule exists at all. Thumbnails live in
    `{ds}/thumbnails/`, *beside* `images/` rather than under it, so an `rmtree`
    of `images/` never reaches them — the directory branch has to.

    A video in the same dataset is the negative control: its poster is under
    `videos/`, untouched by this delete, and its row must survive."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png")
            b = await upload_image(env, ds["id"], "b.png")

            row_a = await _row(env, Image, a["id"])
            row_b = await _row(env, Image, b["id"])
            images_dir = Path(row_a.file_path).parent
            thumbs = [Path(row_a.thumbnail_path), Path(row_b.thumbnail_path)]
            assert all(t.exists() for t in thumbs)
            assert not str(thumbs[0]).startswith(str(images_dir))

            r = await env.client.post(f"{FS}/delete", json={"path": str(images_dir)})
            assert r.status_code == 200, r.text

            assert not images_dir.exists()
            assert not any(t.exists() for t in thumbs)
            async with env.Session() as db:
                assert (await db.execute(select(Image.id))).scalars().all() == []

            detail = (await env.client.get(f"{API}/datasets/{ds['id']}")).json()
            assert detail["image_count"] == 0

    run(scenario())


@needs_cv2
def test_deleting_a_videos_folder_leaves_frames_alive_with_their_lineage_cut(tmp_path):
    """Same contract as `DELETE /videos/{id}`: a frame outlives its source, it
    just stops being addressable by lineage. The rows here are seeded directly
    rather than extracted — the column is what is under test, not the extractor."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")
            frame = await upload_image(env, ds["id"], "frame.png")

            async with env.Session() as db:
                row = await db.get(Image, frame["id"])
                row.source_video_id = video["id"]
                row.source_timestamp_ms = 1200
                await db.commit()

            videos_dir = Path((await _row(env, Video, video["id"])).file_path).parent
            r = await env.client.post(f"{FS}/delete", json={"path": str(videos_dir)})
            assert r.status_code == 200, r.text

            assert await _row(env, Video, video["id"]) is None
            kept = await _row(env, Image, frame["id"])
            assert kept is not None
            assert kept.source_video_id is None
            # The facts about *where in a video* the frame came from travel on.
            assert kept.source_timestamp_ms == 1200
            assert Path(kept.file_path).exists()

    run(scenario())


def test_delete_backs_the_file_up_to_the_object_store_first(tmp_path):
    """V-22 / PM-014. The Core `delete(Image).where(...)` this endpoint used
    looked exactly like `batch_delete`'s — which fires the hook two lines above
    it. Without the hook, a snapshot taken before the delete still lists the
    image but has no bytes to restore it from."""
    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.patch(
                f"{API}/settings/thresholds", json={"versioning_mode": "auto"}
            )
            assert r.status_code == 200, r.text

            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "pic.png")

            async with env.Session() as db:
                await version_service.create_snapshot(db, ds["id"], "s1", "")

            async with env.Session() as db:
                state = (await db.execute(
                    select(VersionImageState).where(VersionImageState.image_id == img["id"])
                )).scalars().all()
            assert len(state) == 1
            assert state[0].file_hash is None, "COW is lazy — nothing is stored until a mutation"

            file_path = Path((await _row(env, Image, img["id"])).file_path)
            r = await env.client.post(f"{FS}/delete", json={"path": str(file_path)})
            assert r.status_code == 200, r.text

            async with env.Session() as db:
                state = (await db.execute(
                    select(VersionImageState).where(VersionImageState.image_id == img["id"])
                )).scalars().all()
            sha = state[0].file_hash
            assert sha is not None, "the hook did not fire before the row was deleted"
            objects = Path(ds["folder_path"]) / ".versions" / "objects"
            assert (objects / sha[:2] / sha[2:]).exists()

    run(scenario())


def test_delete_409s_while_the_dataset_is_busy_and_leaves_the_files_alone(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            from backend.services import dataset_busy

            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "pic.png")
            row = await _row(env, Image, img["id"])
            images_dir = Path(row.file_path).parent

            with dataset_busy.busy(ds["id"], "versioning"):
                r = await env.client.post(f"{FS}/delete", json={"path": str(images_dir)})
            assert r.status_code == 409, r.text

            assert Path(row.file_path).exists()
            assert Path(row.thumbnail_path).exists()
            assert await _row(env, Image, img["id"]) is not None

    run(scenario())


def test_a_failed_filesystem_delete_leaves_the_rows_intact(tmp_path, monkeypatch):
    """The other half of the ordering. Every DB statement is staged and flushed
    before the `rmtree`, but nothing is committed until after it — so a refused
    filesystem delete rolls all of it back rather than leaving a dataset of rows
    pointing at files that are still there."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "pic.png")
            row = await _row(env, Image, img["id"])
            images_dir = Path(row.file_path).parent

            def boom(*a, **kw):
                raise PermissionError("nope")

            monkeypatch.setattr(shutil, "rmtree", boom)
            r = await env.client.post(f"{FS}/delete", json={"path": str(images_dir)})
            assert r.status_code == 403, r.text

            assert Path(row.file_path).exists()
            assert Path(row.thumbnail_path).exists()
            assert await _row(env, Image, img["id"]) is not None

    run(scenario())


def test_an_underscore_in_a_dataset_folder_does_not_delete_a_siblings_rows(tmp_path):
    """`ColumnOperators.startswith` defaults to `autoescape=False`, and `_` is a
    LIKE wildcard. `_name_to_slug` turns every space into `_`, so `my_dataset`
    matched `myxdataset` — and this is the endpoint where that over-match is a
    `DELETE`. The two slugs must stay the same length for the wildcard to line
    up, which is the whole trick of the fixture."""
    async def scenario():
        async with api_env(tmp_path) as env:
            a = await env.create_dataset("my dataset")
            b = await env.create_dataset("myxdataset")
            assert Path(a["folder_path"]).name == "my_dataset"
            assert Path(b["folder_path"]).name == "myxdataset"

            mine = await upload_image(env, a["id"], "mine.png")
            theirs = await upload_image(env, b["id"], "theirs.png")
            neighbour = await _row(env, Image, theirs["id"])

            images_dir = Path((await _row(env, Image, mine["id"])).file_path).parent
            r = await env.client.post(f"{FS}/delete", json={"path": str(images_dir)})
            assert r.status_code == 200, r.text

            assert await _row(env, Image, mine["id"]) is None
            assert await _row(env, Image, theirs["id"]) is not None
            assert Path(neighbour.file_path).exists()
            assert Path(neighbour.thumbnail_path).exists()

    run(scenario())
