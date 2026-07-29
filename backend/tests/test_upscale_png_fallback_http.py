"""Replace- and copy-mode upscaling on a format PIL cannot write back.

The upscale twin of `test_lut_replace_extension_http.py`, and the first test
coverage this endpoint has ever had. `upscale_image_sync` runs the same
`normalize_image_format` PNG fallback as `apply_lut_sync` — `.bmp`, `.gif`,
`.tiff` and `.avif` are all ingestible and none of them can be saved back — but
it did not *return* the corrected path, so none of its three callers could
follow it (PM-009 at its second call site).

**What is faked and what is not.** Only the model call: torch and spandrel are
not installed and there is no `.pth` to load. The fake writes through the real
`normalize_image_format` + `image_save_kwargs`, so the correction under test is
the production one, while the routers, the DB writes, the thumbnail and the
filesystem are all real. It paints its output a colour the source does not have,
so reading a thumbnail back proves which file it was cut from — the half of
PM-009 that left the dataset *inconsistent* rather than merely wrong.

`.bmp` only. `.avif` is in `IMAGE_EXTENSIONS` too, but AVIF is a build-time
Pillow feature rather than a version guarantee, so a fixture in that format may
not be readable here.
"""
from pathlib import Path

from sqlalchemy import select

from backend.models import Image
from backend.tests.conftest import (
    API,
    api_env,
    bmp_bytes,
    frame_colour,
    run,
    upload_image,
    wait_for_job,
)
from backend.utils import image_save_kwargs, normalize_image_format

# The source is (200, 90, 40); anything the fake writes is this instead.
UPSCALED = (20, 190, 120)
MODEL = "fake_2x.pth"


def _fake_upscale(src, dest, model_path, replace, target_width=None, target_height=None):
    """Same contract as `upscale_image_sync`, minus the model.

    Deliberately *not* a `shutil.copy2`: the save tail is the code under test, so
    it is reproduced here exactly — `normalize_image_format` decides the format
    and may move the path, `image_save_kwargs` supplies the save options, and the
    written path comes back as `out_path`.
    """
    from PIL import Image as PilImage

    with PilImage.open(src) as probe:
        w, h = probe.size
    out_path = src if replace else dest
    fmt, out_path = normalize_image_format(Path(src).suffix, out_path)
    result = PilImage.new("RGB", (w * 2, h * 2), UPSCALED)
    result.save(out_path, format=fmt, **image_save_kwargs(fmt))
    result.close()
    return {
        "width": w * 2,
        "height": h * 2,
        "file_size_bytes": Path(out_path).stat().st_size,
        "format": fmt,
        "out_path": out_path,
    }


def _install_fake(monkeypatch):
    """Both bindings: `routers/upscaling.py` imports the name at module import,
    while the two crop workers import it inside the coroutine."""
    import backend.routers.upscaling as upscale_router
    from backend.ml import upscaler

    monkeypatch.setattr(upscaler, "upscale_image_sync", _fake_upscale)
    monkeypatch.setattr(upscale_router, "upscale_image_sync", _fake_upscale)


def _is_upscaled(path) -> bool:
    """Was this file cut from the fake's output? Tolerant because thumbnails are
    lossy webp; the two candidate colours are nowhere near each other."""
    return all(abs(a - b) <= 24 for a, b in zip(frame_colour(path), UPSCALED))


async def _run_upscale(env, ds_id, image_id, replace):
    r = await env.client.post(f"{API}/upscaling/run", json={
        "dataset_id": ds_id, "image_ids": [image_id],
        "model_path": MODEL, "replace": replace,
    })
    assert r.status_code == 200, r.text
    return await wait_for_job(env, r.json()["job_id"], timeout=60)


# ── Batch upscale ─────────────────────────────────────────────────────────────

def test_upscale_replace_follows_the_png_fallback_and_leaves_no_orphan(tmp_path, monkeypatch):
    """The row must name the file that was written, the `.bmp` must be gone, and
    the thumbnail must be cut from the PNG — the three-way agreement PM-009 is
    about. The stem does not change, so nothing derived moves."""
    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake(monkeypatch)
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "shot.bmp", bmp_bytes())

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
                bmp_path = Path(row.file_path)
                thumb = Path(row.thumbnail_path)
            sidecar = bmp_path.with_suffix(".txt")
            sidecar.write_text("kept", encoding="utf-8")

            job = await _run_upscale(env, ds["id"], img["id"], replace=True)
            assert job["status"] == "completed", job

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
            assert row.filename == bmp_path.with_suffix(".png").name
            assert row.file_path == str(bmp_path.with_suffix(".png"))
            assert row.format == "PNG"
            assert Path(row.file_path).exists()
            assert not bmp_path.exists(), "the .bmp was orphaned in images/"
            assert row.width == 64 and row.height == 48

            assert row.thumbnail_path == str(thumb)
            assert sidecar.read_text(encoding="utf-8") == "kept"
            # The thumbnail was cut from the file that was written, not from the
            # stale original the router used to pass.
            assert _is_upscaled(thumb), frame_colour(thumb)

    run(scenario())


def test_upscale_replace_never_clobbers_an_unregistered_file_at_the_fallback_path(tmp_path, monkeypatch):
    """The guard runs before the save, and skips the image rather than failing
    the job: one squatter must not end a batch of a thousand."""
    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake(monkeypatch)
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "shot.bmp", bmp_bytes())

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
                bmp_path = Path(row.file_path)
                before = bmp_path.read_bytes()
            squatter = bmp_path.with_suffix(".png")
            squatter.write_bytes(b"not an image, and not yours to overwrite")

            job = await _run_upscale(env, ds["id"], img["id"], replace=True)
            assert job["status"] == "completed", job

            assert squatter.read_bytes() == b"not an image, and not yours to overwrite"
            async with env.Session() as db:
                row = await db.get(Image, img["id"])
            assert row.file_path == str(bmp_path)
            assert bmp_path.read_bytes() == before
            assert not row.processing_history

    run(scenario())


def _break_thumbnails(monkeypatch, module):
    """A deterministically failing `generate_thumbnail`, patched on the *router
    module attribute* — both routers import the name at module import. A plain
    sync `def`, because the call goes through `run_in_executor`. Install it after
    the fixture upload, which cuts a thumbnail through `routers/images.py` too."""
    def boom(_src, _dest):
        raise OSError("no space left on device")

    monkeypatch.setattr(module, "generate_thumbnail", boom)


def test_upscale_replace_still_serves_the_image_when_the_thumbnail_fails(tmp_path, monkeypatch):
    """PM-013 at the upscale call site — acquired three commits ago, in the fix
    for PM-009 itself. `generate_thumbnail` ran between the rename and the loop's
    single commit, so an unwritable `thumbnails/` rolled the row back onto the
    `.bmp` the upscale had already replaced, and `GET /images/{id}/file` 404'd."""
    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake(monkeypatch)
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "shot.bmp", bmp_bytes())

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
                bmp_path = Path(row.file_path)

            import backend.routers.upscaling as upscale_router

            _break_thumbnails(monkeypatch, upscale_router)
            job = await _run_upscale(env, ds["id"], img["id"], replace=True)
            assert job["status"] == "completed", job

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
            png_path = bmp_path.with_suffix(".png")
            assert row.file_path == str(png_path)
            assert row.width == 64 and row.height == 48, "the row was rolled back"
            assert png_path.exists()
            assert not bmp_path.exists(), "the superseded .bmp was left behind"

            r = await env.client.get(f"{API}/images/{img['id']}/file")
            assert r.status_code == 200, r.text

    run(scenario())


def test_upscale_replace_on_a_png_is_unaffected(tmp_path, monkeypatch):
    """The common case stays a plain in-place overwrite: one row, same name, no
    unlink."""
    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake(monkeypatch)
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "plain.png")

            job = await _run_upscale(env, ds["id"], img["id"], replace=True)
            assert job["status"] == "completed", job

            async with env.Session() as db:
                rows = (await db.execute(select(Image))).scalars().all()
            assert len(rows) == 1
            assert rows[0].filename == "plain.png"
            assert Path(rows[0].file_path).exists()
            assert [e["op"] for e in rows[0].processing_history] == ["upscale"]

    run(scenario())


def test_upscale_copy_registers_the_written_file_and_reserves_the_png_name(tmp_path, monkeypatch):
    """Copy mode used to fail the whole job: the row was built naming
    `shot_up2x.bmp` while the file written was `.png`, and `generate_thumbnail`
    ran on the path that did not exist.

    The name is reserved under the extension that will actually be written, so an
    unregistered `shot_up2x.png` already in `images/` pushes the new file to
    `_001` instead of being overwritten — `unique_filename` only stats the suffix
    it is handed."""
    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake(monkeypatch)
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "shot.bmp", bmp_bytes())

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
                images_dir = Path(row.file_path).parent
            squatter = images_dir / "shot_up2x.png"
            squatter.write_bytes(b"unregistered, and not yours to overwrite")

            job = await _run_upscale(env, ds["id"], img["id"], replace=False)
            assert job["status"] == "completed", job

            assert squatter.read_bytes() == b"unregistered, and not yours to overwrite"
            async with env.Session() as db:
                rows = (await db.execute(select(Image))).scalars().all()
            new = [r for r in rows if r.id != img["id"]]
            assert len(new) == 1, rows
            derived = new[0]
            assert derived.filename == "shot_up2x_001.png"
            assert derived.file_path == str(images_dir / "shot_up2x_001.png")
            assert derived.format == "PNG"
            assert Path(derived.file_path).exists()
            assert Path(derived.thumbnail_path).exists()
            assert _is_upscaled(derived.thumbnail_path), frame_colour(derived.thumbnail_path)
            # The source is untouched — copy mode adds, it does not replace.
            assert Path(rows[0].file_path if rows[0].id == img["id"] else img["file_path"]).exists()

    run(scenario())


# ── Crop + upscale ────────────────────────────────────────────────────────────

async def _crop(env, image_id, **extra):
    body = {"x": 0, "y": 0, "width": 16, "height": 16, "upscale_model": MODEL, **extra}
    return await env.client.post(f"{API}/images/{image_id}/crop", json=body)


def test_crop_replace_upscale_follows_the_png_fallback(tmp_path, monkeypatch):
    """`_run_crop_upscale_replace`'s `dest` is the original's path, so it is a
    replace in everything but the `replace=False` it passes the helper — and the
    row, the thumbnail and the file on disk still have to agree."""
    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake(monkeypatch)
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "shot.bmp", bmp_bytes())

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
                bmp_path = Path(row.file_path)
                thumb = Path(row.thumbnail_path)

            r = await _crop(env, img["id"], replace=True)
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
            png_path = bmp_path.with_suffix(".png")
            assert row.file_path == str(png_path)
            assert row.filename == png_path.name
            assert row.format == "PNG"
            assert png_path.exists()
            assert not bmp_path.exists(), "the .bmp was orphaned in images/"
            assert _is_upscaled(thumb), frame_colour(thumb)
            # The crop temp file is cleaned up either way.
            assert not list(bmp_path.parent.glob("*_croptmp*"))

    run(scenario())


def test_crop_replace_upscale_still_serves_the_image_when_the_thumbnail_fails(tmp_path, monkeypatch):
    """PM-013's worst ordering: `generate_thumbnail` ran *before the session was
    even opened*, so a raise there meant the row was never updated at all — the
    crop had overwritten the original and nothing recorded it."""
    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake(monkeypatch)
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "shot.bmp", bmp_bytes())

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
                bmp_path = Path(row.file_path)

            import backend.routers.images as images_router

            _break_thumbnails(monkeypatch, images_router)
            r = await _crop(env, img["id"], replace=True)
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
            png_path = bmp_path.with_suffix(".png")
            assert row.file_path == str(png_path)
            assert row.filename == png_path.name
            assert [e["op"] for e in row.processing_history] == ["crop_upscale"], \
                "the row was rolled back"
            assert png_path.exists()
            assert not bmp_path.exists(), "the superseded .bmp was left behind"

            r = await env.client.get(f"{API}/images/{img['id']}/file")
            assert r.status_code == 200, r.text

    run(scenario())


def test_crop_replace_upscale_is_a_409_when_the_fallback_path_is_occupied(tmp_path, monkeypatch):
    """One image, so this endpoint can refuse up front instead of skipping the
    way the batch jobs do — and refusing before anything is touched means no
    `_croptmp` left behind and no COW copy taken for a crop that never ran."""
    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake(monkeypatch)
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "shot.bmp", bmp_bytes())

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
                bmp_path = Path(row.file_path)
                before = bmp_path.read_bytes()
            squatter = bmp_path.with_suffix(".png")
            squatter.write_bytes(b"not an image, and not yours to overwrite")

            r = await _crop(env, img["id"], replace=True)
            assert r.status_code == 409, r.text
            assert "shot.png" in r.json()["detail"]

            assert squatter.read_bytes() == b"not an image, and not yours to overwrite"
            assert bmp_path.read_bytes() == before
            assert not list(bmp_path.parent.glob("*_croptmp*"))
            async with env.Session() as db:
                row = await db.get(Image, img["id"])
            assert row.file_path == str(bmp_path)
            assert not row.processing_history

    run(scenario())


def test_crop_new_file_upscale_registers_what_was_written(tmp_path, monkeypatch):
    """New-file mode: the derived row and its thumbnail name the written file.
    This one used to fail the job outright — `generate_thumbnail` ran on a `.bmp`
    that was never written — leaving an orphan `.png` and no row at all."""
    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake(monkeypatch)
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "shot.bmp", bmp_bytes())

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
                images_dir = Path(row.file_path).parent

            r = await _crop(env, img["id"])
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job

            async with env.Session() as db:
                rows = (await db.execute(select(Image))).scalars().all()
            new = [r for r in rows if r.id != img["id"]]
            assert len(new) == 1, rows
            derived = new[0]
            assert derived.filename == "shot_crop.png"
            assert derived.file_path == str(images_dir / "shot_crop.png")
            assert Path(derived.file_path).exists()
            assert Path(derived.thumbnail_path).exists()
            assert _is_upscaled(derived.thumbnail_path), frame_colour(derived.thumbnail_path)
            # No orphan under the source's extension, and no temp left behind.
            assert not (images_dir / "shot_crop.bmp").exists()
            assert not list(images_dir.glob("*_tmp*"))
            # The source is untouched.
            assert (images_dir / "shot.bmp").exists()

    run(scenario())
