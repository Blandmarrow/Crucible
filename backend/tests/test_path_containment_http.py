"""Stored paths that resolve outside `settings.datasets_dir`.

The guard is `utils.safe_dataset_path` / `utils.within_datasets_dir`, and until
now the 403 surface it protects had no test at all. **The sibling-directory name
is the whole point of this module**: the guard used to compare path *strings*
with `startswith`, so `{tmp}/datasets_backup/secret.png` passed a
`{tmp}/datasets` base — a prefix match, not containment. Every fixture here puts
its escaped file in a directory whose name starts with the datasets dir's, so a
regression to `startswith` turns these red rather than leaving them green.

Two behaviours are pinned, and the asymmetry between them is deliberate:

- a **serve** route 403s;
- a **destructive** route skips the filesystem work, logs, and still drops the
  row — an undeletable row the user can see is the worse failure. The one
  exception is `PATCH /videos/{id}/rename`, which 403s, because renaming an
  out-of-tree row is meaningless.

No row reaches these states through the app. Each test hand-edits the column,
which is exactly the provenance the guard exists for: a path written by an
earlier import, a hand edit, or a restored backup.
"""

from pathlib import Path

from sqlalchemy import select

from backend.models import Image, Video
from backend.tests.conftest import (
    API,
    api_env,
    needs_cv2,
    png_bytes,
    run,
    upload_image,
    upload_video,
)


def _escaped_file(tmp_path: Path, name: str, data: bytes = b"outside") -> Path:
    """Write a file into a sibling of the datasets dir sharing its prefix."""
    outside = tmp_path / "datasets_backup"
    outside.mkdir(parents=True, exist_ok=True)
    target = outside / name
    target.write_bytes(data)
    return target


async def _set(env, model, row_id: str, **values) -> None:
    """Hand-edit a stored path, the way a bad import or a manual fix would."""
    async with env.Session() as db:
        row = await db.get(model, row_id)
        for k, v in values.items():
            setattr(row, k, v)
        await db.commit()


def test_a_sibling_directory_sharing_the_prefix_is_not_inside_the_datasets_dir(tmp_path):
    """The V-17 regression pin: 200 under a string `startswith`, 403 under
    `Path.is_relative_to`."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png")
            escaped = _escaped_file(tmp_path, "secret.png", png_bytes())
            assert str(escaped).startswith(str(env.datasets_dir)), (
                "the fixture must share the datasets dir's string prefix or it "
                "does not test anything"
            )
            await _set(env, Image, img["id"], file_path=str(escaped))

            r = await env.client.get(f"{API}/images/{img['id']}/file")
            assert r.status_code == 403, r.text

    run(scenario())


def test_a_stored_thumbnail_outside_the_tree_is_refused(tmp_path):
    """V-20: `thumbnail_path` is as much a stored path as `file_path`, and the
    thumbnail route used to serve it unguarded while the video poster twin
    guarded its own."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png")
            escaped = _escaped_file(tmp_path, "secret.webp", png_bytes())
            await _set(env, Image, img["id"], thumbnail_path=str(escaped))

            r = await env.client.get(f"{API}/images/{img['id']}/thumbnail")
            assert r.status_code == 403, r.text

    run(scenario())


@needs_cv2
def test_a_video_poster_outside_the_tree_is_refused(tmp_path):
    """The twin of the test above — the pair is what makes the two routers agree."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")
            escaped = _escaped_file(tmp_path, "secret.webp", png_bytes())
            await _set(env, Video, video["id"], poster_path=str(escaped))

            r = await env.client.get(f"{API}/videos/{video['id']}/poster")
            assert r.status_code == 403, r.text

    run(scenario())


def test_deleting_an_image_whose_path_escaped_drops_the_row_but_not_the_file(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png")
            escaped = _escaped_file(tmp_path, "keep-me.png", png_bytes())
            await _set(env, Image, img["id"], file_path=str(escaped))

            r = await env.client.delete(f"{API}/images/{img['id']}")
            assert r.status_code == 204, r.text

            assert escaped.exists(), "an out-of-tree path must never be unlinked"
            async with env.Session() as db:
                assert await db.get(Image, img["id"]) is None

    run(scenario())


@needs_cv2
def test_deleting_a_video_whose_path_escaped_drops_the_row_but_not_the_file(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")
            escaped = _escaped_file(tmp_path, "keep-me.mp4")
            await _set(env, Video, video["id"], file_path=str(escaped))

            r = await env.client.delete(f"{API}/videos/{video['id']}")
            assert r.status_code == 204, r.text

            assert escaped.exists()
            async with env.Session() as db:
                assert await db.get(Video, video["id"]) is None

    run(scenario())


@needs_cv2
def test_renaming_a_video_whose_path_escaped_is_a_403(tmp_path):
    """The deliberate asymmetry: a delete proceeds without touching the file, a
    rename refuses outright — there is nothing to salvage by renaming a row whose
    file the app may not touch, and the operation would move an arbitrary file."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")
            escaped = _escaped_file(tmp_path, "keep-me.mp4")
            await _set(env, Video, video["id"], file_path=str(escaped))

            r = await env.client.patch(
                f"{API}/videos/{video['id']}/rename", json={"new_stem": "renamed"}
            )
            assert r.status_code == 403, r.text
            assert escaped.exists()
            async with env.Session() as db:
                assert (await db.get(Video, video["id"])).filename == "clip.mp4"

    run(scenario())


def test_batch_delete_skips_an_escaped_path_and_deletes_its_neighbour(tmp_path):
    """Per row, not per request: one bad path must not spare its neighbours."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            bad = await upload_image(env, ds["id"], "bad.png")
            good = await upload_image(env, ds["id"], "good.png")
            escaped = _escaped_file(tmp_path, "keep-me.png", png_bytes())
            await _set(env, Image, bad["id"], file_path=str(escaped))

            async with env.Session() as db:
                good_file = Path((await db.get(Image, good["id"])).file_path)

            r = await env.client.request(
                "DELETE", f"{API}/images/batch/delete", json=[bad["id"], good["id"]]
            )
            assert r.status_code == 204, r.text

            assert escaped.exists()
            assert not good_file.exists()
            async with env.Session() as db:
                assert (await db.execute(select(Image.id))).scalars().all() == []

    run(scenario())
