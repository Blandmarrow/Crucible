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

- **A directory takes neither arm, and is refused rather than rewritten.** Both
  arms match on `file_path == str(p)`, so renaming `{ds}/images` used to return
  200 and strand every row in the dataset. A rename is a same-parent move, so
  the only directories that can hold registered media are structural ones, and a
  prefix rewrite would leave the DB agreeing with disk while the *dataset* stayed
  broken — which is why `/move` no longer offers one either (V-21). `_guard_directory_rename` therefore only ever
  refuses, in the order C → D → A → B (dataset folder → layout name → rows under
  the prefix → derived artifacts under it), most actionable message first.
"""

import os
import shutil
from pathlib import Path

from sqlalchemy import select

from backend.models import Dataset, Image, Video
from backend.tests.conftest import (
    API,
    api_env,
    jpeg_bytes,
    needs_cv2,
    png_bytes,
    run,
    upload_image,
    upload_video,
)

FS = f"{API}/filesystem"


async def _relocate_out_of_layout(env, src: Path, dst_dir: Path) -> Path:
    """Put a dataset's media folder where the app's layout does not allow, and
    point its rows at the new location.

    This is the state `POST /move` produced until V-21 refused it, so the fixture
    is built directly rather than through the endpoint (which now 409s — see
    `docs/dev/file-browser.md` § `POST /move`). Still worth guarding `/rename`
    against: a user who rearranges a dataset folder in their own file manager, or
    a database written before V-21, arrives in exactly this state with no endpoint
    involved. The app does not support that layout, but it must not corrupt
    anything when it meets one.
    """
    dst = dst_dir / src.name
    shutil.move(str(src), str(dst))
    async with env.Session() as db:
        for model in (Image, Video):
            rows = (await db.execute(
                select(model).where(model.file_path.startswith(str(src) + os.sep))
            )).scalars().all()
            for row in rows:
                row.file_path = str(dst / Path(row.file_path).relative_to(src))
        await db.commit()
    return dst


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


def test_a_failed_thumbnail_move_does_not_lose_the_rename(tmp_path):
    """V-87, the twin of `test_rename_collisions_http.py`'s test of the same name.

    The thumbnail move used to sit between `rename_with_sidecar` and the commit,
    so an `OSError` there (read-only `thumbnails/`, full volume) discarded a
    rename that had already happened on disk: the row kept the old path, the
    renamed file was unregistered, and the next `rescan_dataset` adopted it as a
    second row for the same bytes. It is now a post-commit epilogue.
    """
    async def scenario():
        import pathlib

        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("fsr")
            img = await upload_image(env, ds["id"], "a.png")
            root = Path(ds["folder_path"])

            original_replace = pathlib.Path.replace

            def failing_replace(self, target):
                # `rename_with_sidecar` uses `Path.rename`, so the file rename and
                # the sidecar are unaffected — only the thumbnail move fails.
                if str(target).endswith(".webp"):
                    raise OSError("thumbnail move refused")
                return original_replace(self, target)

            pathlib.Path.replace = failing_replace
            try:
                r = await env.client.post(
                    f"{FS}/rename",
                    json={"path": str(root / "images" / "a.png"), "new_name": "b.png"},
                )
            finally:
                pathlib.Path.replace = original_replace

            assert r.status_code == 200, r.text
            assert (root / "images" / "b.png").exists(), "the rename was lost"
            assert not (root / "images" / "a.png").exists()

            async with env.Session() as db:
                row = (await db.execute(select(Image).where(Image.id == img["id"]))).scalar_one()
            assert row.filename == "b.png"
            assert row.file_path == str(root / "images" / "b.png")
            assert row.thumbnail_path == str(root / "thumbnails" / "b.webp"), \
                "the row must state intent"
            assert not (root / "thumbnails" / "b.webp").exists()

            # The self-heal: serve_thumbnail regenerates the missing one.
            assert (await env.client.get(f"{API}/images/{img['id']}/thumbnail")).status_code == 200
            assert (root / "thumbnails" / "b.webp").exists()

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


# ── Directory renames ─────────────────────────────────────────────────────────


def test_renaming_a_datasets_images_folder_is_refused(tmp_path):
    """The hole this section closes: one request that strands every row in the
    dataset behind a 200. Guard D catches it before A gets the chance, because
    "part of a dataset's folder layout" is the more actionable of the two."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("fsr")
            img = await upload_image(env, ds["id"], "a.png")

            images_dir = Path(ds["folder_path"]) / "images"
            r = await env.client.post(
                f"{FS}/rename", json={"path": str(images_dir), "new_name": "pictures"}
            )
            assert r.status_code == 409, r.text
            assert "folder layout" in r.json()["detail"]

            assert images_dir.exists()
            assert not (Path(ds["folder_path"]) / "pictures").exists()
            assert (images_dir / "a.png").exists()
            async with env.Session() as db:
                row = (await db.execute(select(Image).where(Image.id == img["id"]))).scalar_one()
            assert row.file_path == str(images_dir / "a.png")
            r2 = await env.client.get(f"{API}/images/{img['id']}/file")
            assert r2.status_code == 200, r2.text

    run(scenario())


def test_renaming_an_empty_images_folder_is_still_refused(tmp_path):
    """The test that dies if guard D is dropped. A, B and C are all
    content-based, and `create_dataset` mkdirs `images/` and `thumbnails/`
    before a single row exists — so a brand-new dataset's layout passes every
    row-based guard while being exactly as unrenameable."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("fsr")
            images_dir = Path(ds["folder_path"]) / "images"
            assert images_dir.is_dir()

            r = await env.client.post(
                f"{FS}/rename", json={"path": str(images_dir), "new_name": "pictures"}
            )
            assert r.status_code == 409, r.text
            assert "folder layout" in r.json()["detail"]
            assert images_dir.exists()
            assert not (Path(ds["folder_path"]) / "pictures").exists()

    run(scenario())


def test_renaming_a_datasets_versions_folder_is_refused(tmp_path):
    """`.versions` is the strongest member of the layout set, not the marginal
    one: **no row anywhere stores a path under it**, so guards A, B and C are
    blind to it permanently — even for a full dataset — and D is the only thing
    that can ever see it.

    Renamed, `restore_version` takes its `elif needs_restore:` branch and every
    snapshot silently restores nothing, `_prune_objects_sync` returns zeros
    forever, and the next `mark_image_deleted_in_versions` mkdirs a fresh
    `.versions/objects`, splitting the store across two folders.

    The object is hand-built rather than snapshotted: the guard reads the
    directory name and nothing else, so this stays fast and unconditional."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("fsr")
            root = Path(ds["folder_path"])
            obj = root / ".versions" / "objects" / "ab" / "cdef"
            obj.parent.mkdir(parents=True)
            obj.write_bytes(b"stored bytes")

            r = await env.client.post(
                f"{FS}/rename", json={"path": str(root / ".versions"), "new_name": "versions_old"}
            )
            assert r.status_code == 409, r.text
            assert "folder layout" in r.json()["detail"]
            assert obj.read_bytes() == b"stored bytes"
            assert not (root / "versions_old").exists()

    run(scenario())


def test_renaming_a_datasets_thumbnails_folder_is_refused(tmp_path):
    """Caught by D *and* by B, which is what makes it the ordering test: the
    asserted substring is `folder layout`, so reordering the guards to run B
    first turns it into `strand` and this fails."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("fsr")
            img = await upload_image(env, ds["id"], "a.png")

            async with env.Session() as db:
                thumb_before = (await db.get(Image, img["id"])).thumbnail_path
            thumbs_dir = Path(thumb_before).parent

            r = await env.client.post(
                f"{FS}/rename", json={"path": str(thumbs_dir), "new_name": "thumbs"}
            )
            assert r.status_code == 409, r.text
            assert "folder layout" in r.json()["detail"]
            assert Path(thumb_before).exists()
            assert not (Path(ds["folder_path"]) / "thumbs").exists()

    run(scenario())


@needs_cv2
def test_renaming_a_videos_thumbnails_folder_is_refused(tmp_path):
    """Guard B alone, and the proof that D's direct-child scope is a decision
    rather than an oversight: `{ds}/videos/thumbnails` is not a direct child of
    the dataset folder, so D never looks at it — B catches it the moment a
    poster exists, and an empty one is inert (`get_video_poster` backfills into
    a re-derived path)."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("fsr")
            vid = await upload_video(env, ds["id"], "clip.mp4")

            async with env.Session() as db:
                poster_before = (await db.get(Video, vid["id"])).poster_path
            poster_dir = Path(poster_before).parent
            assert poster_dir.name == "thumbnails"

            r = await env.client.post(
                f"{FS}/rename", json={"path": str(poster_dir), "new_name": "posters"}
            )
            assert r.status_code == 409, r.text
            assert "strand" in r.json()["detail"]
            assert Path(poster_before).exists()
            assert not (poster_dir.parent / "posters").exists()

    run(scenario())


def test_renaming_a_datasets_own_folder_is_refused(tmp_path):
    """Guard C's exact arm. A prefix rewrite would leave `Dataset.folder_path`
    stale — a path rewrite never touches `Dataset` — so the message points at
    the endpoint that does rename the folder *and* the row."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("fsr")
            img = await upload_image(env, ds["id"], "a.png")
            root = Path(ds["folder_path"])

            r = await env.client.post(
                f"{FS}/rename", json={"path": str(root), "new_name": "renamed_ds"}
            )
            assert r.status_code == 409, r.text
            assert "dataset's own folder" in r.json()["detail"]

            assert root.exists()
            assert not (root.parent / "renamed_ds").exists()
            async with env.Session() as db:
                row = (await db.execute(select(Dataset).where(Dataset.id == ds["id"]))).scalar_one()
            assert row.folder_path == str(root)
            r2 = await env.client.get(f"{API}/images/{img['id']}/file")
            assert r2.status_code == 200, r2.text

    run(scenario())


def test_renaming_an_empty_datasets_own_folder_is_refused(tmp_path):
    """Guard C alone — the case `/move` and `/delete` still permit, since both
    of theirs key off rows and an empty dataset has none."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("fsr")
            root = Path(ds["folder_path"])

            r = await env.client.post(
                f"{FS}/rename", json={"path": str(root), "new_name": "renamed_ds"}
            )
            assert r.status_code == 409, r.text
            assert "dataset's own folder" in r.json()["detail"]
            assert root.exists()
            assert not (root.parent / "renamed_ds").exists()

    run(scenario())


def test_renaming_a_folder_that_holds_dataset_folders_is_refused(tmp_path):
    """Guard C's prefix arm, which dies if C is written as equality only: the
    datasets root is not any dataset's own folder, and with no rows in it — or
    with rows the equality arm cannot see — nothing else refuses it."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("fsr")

            r = await env.client.post(
                f"{FS}/rename",
                json={"path": str(env.datasets_dir), "new_name": "datasets_old"},
            )
            assert r.status_code == 409, r.text
            assert "dataset's own folder" in r.json()["detail"]

            assert env.datasets_dir.exists()
            assert not (env.datasets_dir.parent / "datasets_old").exists()
            assert Path(ds["folder_path"]).is_dir()

    run(scenario())


def test_a_folder_of_registered_media_outside_the_layout_is_refused(tmp_path):
    """Guard A on its own: `{ds}/archive/images` carries a layout name but is
    not a direct child of the dataset folder, so D cannot fire and only the rows
    under the prefix give it away.

    `_relocate_out_of_layout` builds the fixture, since V-21 stopped `/move` from
    producing it — see that helper for why the state is still worth guarding."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("fsr")
            img = await upload_image(env, ds["id"], "a.png")

            root = Path(ds["folder_path"])
            archive = root / "archive"
            archive.mkdir()
            moved = await _relocate_out_of_layout(env, root / "images", archive)
            assert (moved / "a.png").exists()

            r2 = await env.client.post(
                f"{FS}/rename", json={"path": str(moved), "new_name": "pictures"}
            )
            assert r2.status_code == 409, r2.text
            assert "belong to a dataset" in r2.json()["detail"]

            assert (moved / "a.png").exists()
            assert not (archive / "pictures").exists()
            async with env.Session() as db:
                row = (await db.execute(select(Image).where(Image.id == img["id"]))).scalar_one()
            assert row.file_path == str(moved / "a.png")

    run(scenario())


def test_a_directory_with_no_registered_media_still_renames(tmp_path):
    """The negative control, in both places it matters. The guards key off
    structure and rows — not "is this under a dataset" — so an ordinary folder
    inside a dataset renames just as a loose one outside does, which is the file
    browser's actual job. The loose half holds an *unregistered* image so that
    "holds media files" and "holds rows" are not accidentally the same thing."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("fsr")
            await upload_image(env, ds["id"], "a.png")

            loose = tmp_path / "notes"
            loose.mkdir()
            (loose / "readme.txt").write_text("hello")
            (loose / "stray.png").write_bytes(png_bytes())

            r = await env.client.post(
                f"{FS}/rename", json={"path": str(loose), "new_name": "notes2"}
            )
            assert r.status_code == 200, r.text
            assert (tmp_path / "notes2" / "stray.png").exists()
            assert not loose.exists()

            inside = Path(ds["folder_path"]) / "notes"
            inside.mkdir()
            r2 = await env.client.post(
                f"{FS}/rename", json={"path": str(inside), "new_name": "notes2"}
            )
            assert r2.status_code == 200, r2.text
            assert (Path(ds["folder_path"]) / "notes2").is_dir()
            assert not inside.exists()

    run(scenario())


def test_an_underscore_in_a_dataset_folder_does_not_refuse_a_siblings_rename(tmp_path):
    """The twin of `test_filesystem_delete_http.py`'s escaping test. `_` is a
    single-character LIKE wildcard and `_name_to_slug` puts one in every
    multi-word folder name, so unescaped, guard A's prefix over-matches the
    *other* dataset's rows and 409s a rename that touches nothing. The two slugs
    must stay the same length for the wildcard to line up."""
    async def scenario():
        async with api_env(tmp_path) as env:
            a = await env.create_dataset("my dataset")
            b = await env.create_dataset("myxdataset")
            assert Path(a["folder_path"]).name == "my_dataset"
            assert Path(b["folder_path"]).name == "myxdataset"

            await upload_image(env, a["id"], "mine.png")
            await upload_image(env, b["id"], "theirs.png")

            # B's rows move under `{b}/archive/images/`, so the only thing A's
            # `{a}/archive/` prefix can match is B's — via the wildcard.
            b_archive = Path(b["folder_path"]) / "archive"
            b_archive.mkdir()
            await _relocate_out_of_layout(env, Path(b["folder_path"]) / "images", b_archive)

            a_archive = Path(a["folder_path"]) / "archive"
            a_archive.mkdir()
            r2 = await env.client.post(
                f"{FS}/rename", json={"path": str(a_archive), "new_name": "archive2"}
            )
            assert r2.status_code == 200, r2.text
            assert (Path(a["folder_path"]) / "archive2").is_dir()

    run(scenario())
