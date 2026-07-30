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

The destructive sites take that second shape through one helper,
`utils.contained_path`, and the gate covers the **versioning hook** as much as
the unlink: `mark_image_deleted_in_versions` copies the bytes into
`{ds}/.versions/objects/`, so an out-of-tree `file_path` is an arbitrary-file
*read* primitive — retrievable through a snapshot restore — even with the unlink
skipped. V-83 brought the last three sites in: replace-mode extraction
(`videos._delete_previous_frames`), duplicate resolution
(`quality.resolve_duplicates`), and restore's stale-file cleanup
(`version_service._remove_stale_files`). The first two are below; the third is
exercised service-level in `test_versioning_restore.py`
(`test_restore_remove_skips_an_extra_image_whose_path_escaped`), because restore
has no job endpoint of its own.

No row reaches these states through the app. Each test hand-edits the column,
which is exactly the provenance the guard exists for: a path written by an
earlier import, a hand edit, or a restored backup.
"""

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.models import Image, Video
from backend.tests.conftest import (
    API,
    api_env,
    mp4_shots_bytes,
    needs_cv2,
    png_bytes,
    run,
    upload_image,
    upload_video,
    wait_for_job,
)

needs_shot_detection = pytest.mark.skipif(
    importlib.util.find_spec("scenedetect") is None, reason="scenedetect is not installed"
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


def test_resolving_duplicates_skips_an_escaped_path_and_deletes_its_neighbour(tmp_path):
    """`POST /quality/duplicates/resolve` — one of the three V-83 sites. Same
    three assertions as `batch_delete` above, and no gate needed to reach it: the
    endpoint takes the ids straight from the request body."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            keeper = await upload_image(env, ds["id"], "keeper.png")
            bad = await upload_image(env, ds["id"], "bad.png")
            good = await upload_image(env, ds["id"], "good.png")
            escaped = _escaped_file(tmp_path, "keep-me.png", png_bytes())
            await _set(env, Image, bad["id"], file_path=str(escaped))

            async with env.Session() as db:
                good_file = Path((await db.get(Image, good["id"])).file_path)

            r = await env.client.post(
                f"{API}/quality/duplicates/resolve",
                json={"keep_ids": [keeper["id"]], "delete_ids": [bad["id"], good["id"]]},
            )
            assert r.status_code == 204, r.text

            assert escaped.exists(), "an out-of-tree path must never be unlinked"
            assert not good_file.exists()
            async with env.Session() as db:
                assert (await db.execute(select(Image.id))).scalars().all() == [keeper["id"]]

    run(scenario())


@needs_cv2
@needs_shot_detection
def test_replace_mode_extraction_skips_an_escaped_frame_path(tmp_path):
    """`videos._delete_previous_frames` — the V-83 site with the widest blast
    radius, since a replace deletes N rows, N sidecars and N thumbnails in one
    step. The row set is scoped by `source_video_id`/`dataset_id` rather than by a
    request parameter, so a hand-edited `file_path` is the only way in — which is
    also why this needs a full extract-then-replace round trip to reach."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", mp4_shots_bytes())

            r = await env.client.post(
                f"{API}/videos/extract", json={"video_ids": [video["id"]]}
            )
            assert r.status_code == 200, r.text
            for j in r.json()["jobs"]:
                assert (await wait_for_job(env, j["job_id"], timeout=120))["status"] == "completed"

            async with env.Session() as db:
                frames = (await db.execute(
                    select(Image).order_by(Image.filename)
                )).scalars().all()
            assert len(frames) >= 2, [f.filename for f in frames]
            bad, good = frames[0], frames[1]
            images_dir = Path(good.file_path).parent
            bad_orphan = Path(bad.file_path)  # the row will point elsewhere in a moment
            good_file = Path(good.file_path)

            escaped = _escaped_file(tmp_path, "keep-me.jpg", png_bytes())
            await _set(env, Image, bad.id, file_path=str(escaped))

            r2 = await env.client.post(
                f"{API}/videos/extract", json={"video_ids": [video["id"]], "mode": "replace"}
            )
            assert r2.status_code == 200, r2.text
            jobs = [await wait_for_job(env, j["job_id"], timeout=120)
                    for j in r2.json()["jobs"]]
            assert jobs[0]["status"] == "completed", jobs

            assert escaped.exists(), "an out-of-tree path must never be unlinked"
            async with env.Session() as db:
                remaining = (await db.execute(select(Image.id))).scalars().all()
            assert bad.id not in remaining and good.id not in remaining, \
                "the rows go regardless — an undeletable row is the worse failure"

            # The neighbour was still cleaned up. A replace's replacements reuse the
            # previous run's filenames — the delete runs first, so the uniquifier
            # then sees them free — which is what makes a `_001` suffix the signal
            # here: `good`'s name was freed and reused, while `bad`'s in-tree file
            # was left behind untouched and bumped its own replacement to `_001`.
            assert good_file.exists(), "reused by the replacement frame"
            assert not (images_dir / f"{good_file.stem}_001{good_file.suffix}").exists()
            assert bad_orphan.exists(), "the skipped row's in-tree file is not the one to unlink"
            assert (images_dir / f"{bad_orphan.stem}_001{bad_orphan.suffix}").exists()

    run(scenario())
