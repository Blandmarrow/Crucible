"""PATCH /videos/{id}/rename.

The mirror of image rename, with two differences that are easy to get wrong.

The container extension is never user-settable: `video_mime` picks the browser's
decoder from the suffix, so letting a rename turn `clip.mkv` into `clip.mp4`
would hand the browser a matroska labelled as MP4.

Occupied poster stems come from the sibling *rows* as well as from disk. A row
whose poster has never been cut has nothing in videos/thumbnails/, so globbing
alone would let this rename take a stem that another container will claim on its
first view — and the poster written then would overwrite this one's.
"""

from pathlib import Path

from sqlalchemy import select

from backend.models import Video
from backend.tests.conftest import API, api_env, run, upload_video


def _row(env, video_id):
    async def get(db):
        return (await db.execute(select(Video).where(Video.id == video_id))).scalar_one()
    return get


def test_rename_slugifies_and_moves_the_file(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")
            async with env.Session() as db:
                old = Path((await db.execute(select(Video))).scalar_one().file_path)

            r = await env.client.patch(
                f"{API}/videos/{video['id']}/rename", json={"new_stem": "Episode Two!"}
            )
            assert r.status_code == 200, r.text
            assert r.json()["filename"] == "episode_two.mp4"

            async with env.Session() as db:
                row = (await db.execute(select(Video))).scalar_one()
            assert row.filename == "episode_two.mp4"
            assert Path(row.file_path).exists()
            assert not old.exists()

    run(scenario())


def test_the_poster_moves_with_the_file(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")
            async with env.Session() as db:
                old_poster = Path((await db.execute(select(Video))).scalar_one().poster_path)
            assert old_poster.exists()

            await env.client.patch(f"{API}/videos/{video['id']}/rename", json={"new_stem": "renamed"})

            async with env.Session() as db:
                row = (await db.execute(select(Video))).scalar_one()
            assert row.poster_path.endswith("/videos/thumbnails/renamed.webp")
            assert Path(row.poster_path).exists()
            assert not old_poster.exists()

            r = await env.client.get(f"{API}/videos/{video['id']}/poster")
            assert r.status_code == 200

    run(scenario())


def test_the_extension_is_preserved_and_not_user_settable(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mkv")

            r = await env.client.patch(
                f"{API}/videos/{video['id']}/rename", json={"new_stem": "renamed.mp4"}
            )
            # The dot is not a suffix boundary here — slugify strips it and the
            # real container extension is re-attached.
            assert r.json()["filename"] == "renamedmp4.mkv"

            r = await env.client.get(f"{API}/videos/{video['id']}/file")
            assert r.headers["content-type"] == "video/x-matroska"

    run(scenario())


def test_a_collision_with_a_sibling_row_gets_a_suffix(tmp_path):
    """The uq_dataset_video_filename constraint is what makes this mandatory —
    without the db_names check the commit raises an IntegrityError."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await upload_video(env, ds["id"], "taken.mp4")
            video = await upload_video(env, ds["id"], "other.mp4")

            r = await env.client.patch(
                f"{API}/videos/{video['id']}/rename", json={"new_stem": "taken"}
            )
            assert r.status_code == 200, r.text
            assert r.json()["filename"] == "taken_001.mp4"

    run(scenario())


def test_a_cross_container_stem_collision_is_avoided(tmp_path):
    """`a.mp4` and `a.mkv` are different files but share one poster stem, so the
    rename must not take a stem a sibling row already owns even though the
    filenames themselves would not collide."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await upload_video(env, ds["id"], "shared.mkv")
            video = await upload_video(env, ds["id"], "other.mp4")

            r = await env.client.patch(
                f"{API}/videos/{video['id']}/rename", json={"new_stem": "shared"}
            )
            assert r.json()["filename"] == "shared_001.mp4"

            # Both posters survive as distinct files.
            async with env.Session() as db:
                rows = (await db.execute(select(Video))).scalars().all()
            posters = {r.poster_path for r in rows}
            assert len(posters) == 2
            assert all(Path(p).exists() for p in posters)

    run(scenario())


def test_a_stem_collision_against_a_row_with_no_poster_on_disk_is_avoided(tmp_path):
    """The half of the occupied set that a disk glob cannot supply: a Phase 0
    row never viewed has no poster file, so only its filename says the stem is
    taken."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await upload_video(env, ds["id"], "shared.mkv")
            video = await upload_video(env, ds["id"], "other.mp4")

            async with env.Session() as db:
                for row in (await db.execute(select(Video))).scalars().all():
                    if row.filename == "shared.mkv":
                        Path(row.poster_path).unlink()
                        row.poster_path = None
                await db.commit()

            r = await env.client.patch(
                f"{API}/videos/{video['id']}/rename", json={"new_stem": "shared"}
            )
            assert r.json()["filename"] == "shared_001.mp4"

    run(scenario())


def test_renaming_to_its_own_stem_steps_the_counter_and_stays_consistent(tmp_path):
    """`clip` → `clip_001.mp4`, not `clip.mp4`: the row's own file is still on
    disk when `unique_filename` looks, and no `disk_exclude` is passed. This is
    exactly what `PATCH /images/{id}/rename` does with its own stem — pinned so
    the two do not drift, not because the counter step is desirable.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")

            r = await env.client.patch(f"{API}/videos/{video['id']}/rename", json={"new_stem": "clip"})
            assert r.json()["filename"] == "clip_001.mp4"

            # Whatever name it lands on, row and disk agree afterwards.
            async with env.Session() as db:
                row = (await db.execute(select(Video))).scalar_one()
            assert Path(row.file_path).exists()
            assert Path(row.poster_path).exists()

    run(scenario())


def test_an_empty_or_path_bearing_stem_is_rejected(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")

            for stem in ("", "   ", "../escape", "sub/dir", "x" * 201):
                r = await env.client.patch(
                    f"{API}/videos/{video['id']}/rename", json={"new_stem": stem}
                )
                assert r.status_code == 400, f"{stem!r} was accepted"

            # Punctuation-only does *not* 400: slugify_filename falls back to
            # "image" rather than returning empty, so the guard below it is
            # unreachable. Same in rename_image; recorded rather than diverged.
            r = await env.client.patch(f"{API}/videos/{video['id']}/rename", json={"new_stem": "!!!"})
            assert r.json()["filename"] == "image.mp4"

    run(scenario())


def test_rename_404s_for_an_unknown_video(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.patch(f"{API}/videos/nope/rename", json={"new_stem": "x"})
            assert r.status_code == 404

    run(scenario())


def test_rename_409s_while_the_dataset_is_busy(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            from backend.services import dataset_busy

            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")

            with dataset_busy.busy(ds["id"], "versioning"):
                r = await env.client.patch(
                    f"{API}/videos/{video['id']}/rename", json={"new_stem": "renamed"}
                )
            assert r.status_code == 409

    run(scenario())
