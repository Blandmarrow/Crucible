"""`POST /filesystem/rename` — the rename that is not in `routers/images.py`.

The File Browser renames registered media just as `PATCH /images/{id}/rename`
does, and until now it rewrote `file_path`/`filename` and nothing else: the
thumbnail stayed at the old stem, the `.txt` sidecar was orphaned beside it, and
the sync ran *after* `p.rename(...)`, so no guard could refuse with the
filesystem still intact.

Two properties are load-bearing here:

- **A stem another image owns is refused, not adopted.** Thumbnails are `.webp`
  keyed by stem, so `b.jpg` renamed to `a.jpg` beside a registered `a.png` gives
  two rows one derived path, and the next `bulk_rename` / `batch_move_dataset` /
  crop / restore recomputes `thumbnail_path_for` and moves or overwrites the
  *other* image's thumbnail (PM-007). This endpoint is one of the three that
  **adopt** a name rather than pick one, so it cannot step to `_001` the way
  upload does — a typed name is taken as typed or refused.
- **The pure extension change still passes.** `a.jpg` → `a.png` leaves the stem
  alone, so the thumbnail and the sidecar stay exactly where they are and no
  uniquifier is involved. That is CLAUDE.md's explicit carve-out.

The `Video` arm deliberately does none of this to its poster: nothing re-derives
a poster path — every consumer reads `Video.poster_path` — so a poster whose stem
no longer matches its video is the normal state, not a hazard.
"""

from pathlib import Path

from sqlalchemy import select

from backend.models import Image, Video
from backend.tests.conftest import (
    API,
    api_env,
    jpeg_bytes,
    needs_cv2,
    run,
    upload_image,
    upload_video,
)

FS = f"{API}/filesystem"


def test_renaming_a_registered_image_carries_its_thumbnail_and_sidecar(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("fsr")
            img = await upload_image(env, ds["id"], "a.png")
            await env.client.put(
                f"{API}/captions/image/{img['id']}", json={"caption_text": "a caption"}
            )

            root = Path(ds["folder_path"])
            old_file = root / "images" / "a.png"
            old_thumb = root / "thumbnails" / "a.webp"
            assert old_file.exists() and old_thumb.exists()
            assert (root / "images" / "a.txt").exists()

            r = await env.client.post(
                f"{FS}/rename", json={"path": str(old_file), "new_name": "renamed.png"}
            )
            assert r.status_code == 200, r.text

            new_file = root / "images" / "renamed.png"
            new_thumb = root / "thumbnails" / "renamed.webp"
            assert new_file.exists() and not old_file.exists()
            assert new_thumb.exists() and not old_thumb.exists()
            assert (root / "images" / "renamed.txt").read_text(encoding="utf-8") == "a caption"
            assert not (root / "images" / "a.txt").exists()

            async with env.Session() as db:
                row = (await db.execute(select(Image).where(Image.id == img["id"]))).scalar_one()
            assert row.filename == "renamed.png"
            assert row.file_path == str(new_file)
            assert row.thumbnail_path == str(new_thumb)
            # A name the user typed is not auto-generated.
            assert row.is_auto_named is False

    run(scenario())


def test_taking_a_sibling_images_thumbnail_stem_is_a_409(tmp_path):
    """`b.jpg` → `a.jpg` clears both the `new_path.exists()` check and
    `uq_dataset_filename`, and would then hand `a.png`'s thumbnail away."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("fsr")
            await upload_image(env, ds["id"], "a.png")
            b = await upload_image(env, ds["id"], "b.jpg", jpeg_bytes())

            root = Path(ds["folder_path"])
            r = await env.client.post(
                f"{FS}/rename", json={"path": str(root / "images" / "b.jpg"), "new_name": "a.jpg"}
            )
            assert r.status_code == 409, r.text
            assert "a.png" in r.json()["detail"]

            # Refused with the filesystem intact — the guards run before the
            # rename, which is the whole point of gathering the rows first.
            assert (root / "images" / "b.jpg").exists()
            assert not (root / "images" / "a.jpg").exists()
            assert (root / "thumbnails" / "a.webp").exists()
            assert (root / "thumbnails" / "b.webp").exists()
            async with env.Session() as db:
                row = (await db.execute(select(Image).where(Image.id == b["id"]))).scalar_one()
            assert row.filename == "b.jpg"

    run(scenario())


def test_a_name_another_row_already_holds_is_a_409(tmp_path):
    """The `uq_dataset_filename` pre-check, which would otherwise surface as a
    raw IntegrityError 500. Only reachable once the other row's file is gone
    from disk — `new_path.exists()` catches the ordinary case first."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("fsr")
            await upload_image(env, ds["id"], "a.png")
            await upload_image(env, ds["id"], "b.png")

            root = Path(ds["folder_path"])
            (root / "images" / "a.png").unlink()  # behind the DB's back

            r = await env.client.post(
                f"{FS}/rename", json={"path": str(root / "images" / "b.png"), "new_name": "a.png"}
            )
            assert r.status_code == 409, r.text
            assert "already named" in r.json()["detail"]
            assert (root / "images" / "b.png").exists()

    run(scenario())


def test_a_pure_extension_change_is_allowed(tmp_path):
    """The one rename that disturbs nothing derived: the stem is unchanged, so
    the thumbnail and the sidecar stay put and no uniquifier is needed."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("fsr")
            img = await upload_image(env, ds["id"], "a.jpg", jpeg_bytes())
            await env.client.put(
                f"{API}/captions/image/{img['id']}", json={"caption_text": "keep me"}
            )

            root = Path(ds["folder_path"])
            r = await env.client.post(
                f"{FS}/rename", json={"path": str(root / "images" / "a.jpg"), "new_name": "a.png"}
            )
            assert r.status_code == 200, r.text

            assert (root / "images" / "a.png").exists()
            assert not (root / "images" / "a.jpg").exists()
            assert (root / "thumbnails" / "a.webp").exists()
            assert (root / "images" / "a.txt").read_text(encoding="utf-8") == "keep me"

            async with env.Session() as db:
                row = (await db.execute(select(Image).where(Image.id == img["id"]))).scalar_one()
            assert row.filename == "a.png"
            assert row.thumbnail_path == str(root / "thumbnails" / "a.webp")

    run(scenario())


def test_an_unregistered_file_still_renames(tmp_path):
    """No row, no guards — a plain rename is the file browser's actual job."""
    loose = tmp_path / "notes.txt"
    loose.write_text("hello")

    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.post(
                f"{FS}/rename", json={"path": str(loose), "new_name": "notes2.txt"}
            )
            assert r.status_code == 200, r.text

    run(scenario())
    assert (tmp_path / "notes2.txt").read_text() == "hello"
    assert not loose.exists()


@needs_cv2
def test_renaming_a_registered_video_leaves_its_poster_alone(tmp_path):
    """The asymmetry with the image arm above, stated as a test: a poster stem
    need not equal its video's, so there is nothing to guard and nothing to
    move."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("fsr")
            vid = await upload_video(env, ds["id"], "clip.mp4")

            root = Path(ds["folder_path"])
            async with env.Session() as db:
                before = (await db.execute(select(Video).where(Video.id == vid["id"]))).scalar_one()
                poster_before = before.poster_path
            assert poster_before and Path(poster_before).exists()

            r = await env.client.post(
                f"{FS}/rename",
                json={"path": str(root / "videos" / "clip.mp4"), "new_name": "clip2.mp4"},
            )
            assert r.status_code == 200, r.text

            assert (root / "videos" / "clip2.mp4").exists()
            async with env.Session() as db:
                row = (await db.execute(select(Video).where(Video.id == vid["id"]))).scalar_one()
            assert row.filename == "clip2.mp4"
            assert row.file_path == str(root / "videos" / "clip2.mp4")
            assert row.poster_path == poster_before
            assert Path(poster_before).exists()

    run(scenario())
