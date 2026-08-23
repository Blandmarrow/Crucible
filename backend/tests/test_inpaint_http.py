"""`POST /inpaint/run` — the `batch_inpaint` job, end to end without torch.

**What is faked and what is not.** Two things: the model load
(`model_manager.load_lama`, which would download 196 MB and needs torch) and the
model call (`inpaint_image_sync`). The fake writes through the *real*
`normalize_image_format` + `image_save_kwargs`, so the PNG-fallback correction
under test is the production one, while the router, the DB writes, the detection
deletes, the thumbnail and the filesystem are all real. It paints its output a
colour the source does not have, so reading a thumbnail back proves which file it
was cut from.

CI installs `backend/requirements-ci.txt`, which will never carry torch — hence
no `conftest.needs_torch` here, and hence the load fake. `backend.ml.lama_inpainter`
itself imports torch only inside its functions, so importing it is safe.

Detections are inserted as ORM rows: nothing in this file runs a detector.
"""
from pathlib import Path

from sqlalchemy import func, select

from backend.models import Image
from backend.models.detection import Detection
from backend.tests.conftest import (
    API,
    api_env,
    bmp_bytes,
    frame_colour,
    png_bytes,
    run,
    upload_image,
    wait_for_job,
)
from backend.utils import image_save_kwargs, normalize_image_format

# The sources are (10, 120, 200) / (200, 90, 40); anything the fake writes is this.
PAINTED = (30, 200, 90)


def _fake_inpaint(src, dest, mask_png_bytes, replace):
    """Same contract as `inpaint_image_sync`, minus the model.

    Deliberately *not* a `shutil.copy2`: the save tail is part of the code under
    test, so it is reproduced here exactly — `normalize_image_format` decides the
    format and may move the path, `image_save_kwargs` supplies the save options,
    and the written path comes back as `out_path`. Geometry is unchanged, which
    is the whole point of inpainting versus cropping.
    """
    from PIL import Image as PilImage

    assert mask_png_bytes, "the router must hand the model a mask"
    with PilImage.open(src) as probe:
        w, h = probe.size
    out_path = src if replace else dest
    fmt, out_path = normalize_image_format(Path(src).suffix, out_path)
    result = PilImage.new("RGB", (w, h), PAINTED)
    result.save(out_path, format=fmt, **image_save_kwargs(fmt))
    result.close()
    return {
        "width": w,
        "height": h,
        "file_size_bytes": Path(out_path).stat().st_size,
        "format": fmt,
        "phash": "0f0f0f0f0f0f0f0f",
        "out_path": out_path,
    }


def _install_fake(monkeypatch, fake=None):
    """Both bindings: `routers/inpaint.py` imports the name at module import, so
    patching only the `backend.ml.lama_inpainter` attribute would miss it.

    `load_lama` goes too — it would download the weights and import torch."""
    import backend.routers.inpaint as inpaint_router
    from backend.ml import lama_inpainter
    from backend.ml.model_manager import model_manager

    fn = fake or _fake_inpaint
    monkeypatch.setattr(lama_inpainter, "inpaint_image_sync", fn)
    monkeypatch.setattr(inpaint_router, "inpaint_image_sync", fn)

    async def _no_load(*_a, **_kw):
        return None

    monkeypatch.setattr(model_manager, "load_lama", _no_load)


def _is_painted(path) -> bool:
    """Was this file cut from the fake's output? Tolerant because thumbnails are
    lossy webp; the candidate colours are nowhere near each other."""
    return all(abs(a - b) <= 24 for a, b in zip(frame_colour(path), PAINTED))


async def _add_detection(env, image_id: str, label: str = "watermark", *, mask: bool = True):
    """One Detection row, inserted directly. A polygon over the middle ninth of
    the image when `mask`, else bbox-only (the Florence-2/NudeNet shape)."""
    poly = {"polygons": [[[0.33, 0.33], [0.66, 0.33], [0.66, 0.66], [0.33, 0.66]]]}
    async with env.Session() as db:
        det = Detection(
            image_id=image_id,
            label=label,
            bbox=[0.33, 0.33, 0.66, 0.66],
            score=0.9,
            model="test",
            task="grounding",
            mask=__import__("json").dumps(poly) if mask else None,
        )
        db.add(det)
        await db.commit()
        return det.id


async def _run_inpaint(env, ds_id, image_ids, *, replace=True, labels=None, dilate_px=6):
    body = {
        "dataset_id": ds_id, "image_ids": image_ids,
        "replace": replace, "dilate_px": dilate_px,
    }
    if labels is not None:
        body["labels"] = labels
    r = await env.client.post(f"{API}/inpaint/run", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ── Replace mode ──────────────────────────────────────────────────────────────

def test_replace_writes_the_row_the_file_and_the_thumbnail_consistently(tmp_path, monkeypatch):
    """The three-way agreement: the row's size/phash describe the file that was
    written, and the thumbnail was cut from it. Geometry is untouched."""
    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake(monkeypatch)
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "shot.png", png_bytes(size=(48, 32)))
            await _add_detection(env, img["id"])

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
                src = Path(row.file_path)
                thumb = Path(row.thumbnail_path)
                old_phash = row.phash

            started = await _run_inpaint(env, ds["id"], [img["id"]])
            assert started["total"] == 1 and started["skipped"] == 0
            job = await wait_for_job(env, started["job_id"], timeout=60)
            assert job["status"] == "completed", job
            assert job["result_data"]["inpainted"] == 1
            assert job["result_data"]["failed"] == 0

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
            assert row.file_path == str(src)
            assert row.width == 48 and row.height == 32, "inpainting must not move an edge"
            assert row.file_size_bytes == src.stat().st_size
            assert row.phash == "0f0f0f0f0f0f0f0f" != old_phash
            assert _is_painted(src), frame_colour(src)
            assert _is_painted(thumb), frame_colour(thumb)

    run(scenario())


def test_replace_clears_the_flag_and_deletes_what_it_painted_over(tmp_path, monkeypatch):
    """The consumed detections are gone and `has_watermark` is False — the region
    they name no longer contains anything. A detection with a *different* label
    survives when the run filtered on one."""
    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake(monkeypatch)
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "shot.png")
            wm_id = await _add_detection(env, img["id"], "watermark")
            face_id = await _add_detection(env, img["id"], "face")

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
                row.quality_flags = {"has_watermark": True, "is_blurry": True}
                await db.commit()

            job = await wait_for_job(
                env, (await _run_inpaint(env, ds["id"], [img["id"]], labels=["watermark"]))["job_id"],
                timeout=60,
            )
            assert job["status"] == "completed", job

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
                remaining = (await db.execute(select(Detection.id))).scalars().all()
            assert row.quality_flags["has_watermark"] is False
            assert row.quality_flags["is_blurry"] is True, "other flags are untouched"
            assert wm_id not in remaining
            assert face_id in remaining, "a label the run did not paint must survive"

    run(scenario())


def test_replace_records_the_op_and_stales_only_a_scored_row(tmp_path, monkeypatch):
    """`record_in_place` is the single writer of both columns: the history entry
    is written either way, the bit only for a row that carries a measurement."""
    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake(monkeypatch)
            ds = await env.create_dataset("d")
            scored = await upload_image(env, ds["id"], "scored.png")
            bare = await upload_image(env, ds["id"], "bare.png")
            for i in (scored, bare):
                await _add_detection(env, i["id"])

            async with env.Session() as db:
                row = await db.get(Image, scored["id"])
                row.watermark_score = 0.8
                await db.commit()

            job = await wait_for_job(
                env, (await _run_inpaint(env, ds["id"], [scored["id"], bare["id"]]))["job_id"],
                timeout=60,
            )
            assert job["status"] == "completed", job

            async with env.Session() as db:
                s = await db.get(Image, scored["id"])
                b = await db.get(Image, bare["id"])
            for row in (s, b):
                assert [e["op"] for e in row.processing_history] == ["inpaint"]
                assert row.processing_history[0]["dilate_px"] == 6
            assert s.scores_stale is True
            assert not b.scores_stale, "an unscored row has no measurement to invalidate"
            # The stale score is left in place on purpose: the bit flags it rather
            # than the run silently guessing a new one.
            assert s.watermark_score == 0.8

    run(scenario())


def test_replace_follows_the_png_fallback_and_leaves_nothing_derived_behind(tmp_path, monkeypatch):
    """`.bmp` → `.png`: the row moves, the orphan goes, and the stem is unchanged
    so the thumbnail and the `.txt` sidecar stay exactly where they are."""
    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake(monkeypatch)
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "shot.bmp", bmp_bytes())
            await _add_detection(env, img["id"])

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
                bmp_path = Path(row.file_path)
                thumb = Path(row.thumbnail_path)
            sidecar = bmp_path.with_suffix(".txt")
            sidecar.write_text("kept", encoding="utf-8")

            job = await wait_for_job(
                env, (await _run_inpaint(env, ds["id"], [img["id"]]))["job_id"], timeout=60,
            )
            assert job["status"] == "completed", job

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
            assert row.filename == bmp_path.with_suffix(".png").name
            assert row.file_path == str(bmp_path.with_suffix(".png"))
            assert row.format == "PNG"
            assert Path(row.file_path).exists()
            assert not bmp_path.exists(), "the .bmp was orphaned in images/"
            assert row.thumbnail_path == str(thumb)
            assert sidecar.read_text(encoding="utf-8") == "kept"
            assert _is_painted(thumb), frame_colour(thumb)

    run(scenario())


def test_a_thumbnail_failure_is_counted_and_does_not_fail_the_item(tmp_path, monkeypatch):
    """PM-013 at this call site: the paint is committed and the image serves; only
    the gallery tile is stale, and the count is what lets TopBar say so."""
    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake(monkeypatch)
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "shot.png")
            await _add_detection(env, img["id"])

            import backend.routers.inpaint as inpaint_router

            def boom(_src, _dest):
                raise OSError("no space left on device")

            # Installed after the upload, which cuts a thumbnail of its own
            # through `routers/images.py`.
            monkeypatch.setattr(inpaint_router, "generate_thumbnail", boom)
            job = await wait_for_job(
                env, (await _run_inpaint(env, ds["id"], [img["id"]]))["job_id"], timeout=60,
            )
            assert job["status"] == "completed", job
            assert job["result_data"] == {
                "inpainted": 1, "skipped_no_detection": 0, "failed": 0, "thumbnails_stale": 1,
            }

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
            assert _is_painted(Path(row.file_path)), "the paint is committed regardless"
            assert row.processing_history

    run(scenario())


def test_an_image_with_no_matching_detection_is_skipped_not_touched(tmp_path, monkeypatch):
    """The pre-filter keeps `total` honest, and the untouched image keeps its
    bytes, its history and its flags."""
    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake(monkeypatch)
            ds = await env.create_dataset("d")
            has = await upload_image(env, ds["id"], "has.png")
            none = await upload_image(env, ds["id"], "none.png")
            await _add_detection(env, has["id"])

            async with env.Session() as db:
                before = Path((await db.get(Image, none["id"])).file_path).read_bytes()

            started = await _run_inpaint(env, ds["id"], [has["id"], none["id"]])
            assert started["total"] == 1, "only the image with a detection is work"
            assert started["skipped"] == 1
            job = await wait_for_job(env, started["job_id"], timeout=60)
            assert job["status"] == "completed", job
            assert job["result_data"]["inpainted"] == 1

            async with env.Session() as db:
                row = await db.get(Image, none["id"])
            assert Path(row.file_path).read_bytes() == before
            assert not row.processing_history

    run(scenario())


def test_a_failing_image_does_not_fail_the_run(tmp_path, monkeypatch):
    """One bad image is a `failed` count and a `continue`, never the end of a
    batch of a thousand."""
    async def scenario():
        async with api_env(tmp_path) as env:
            # Keyed on the filename, not on a call counter: the worker's
            # `IN (...)` query has no ORDER BY, so which image the loop reaches
            # first is not ours to assume.
            def flaky(src, dest, mask_png_bytes, replace):
                if Path(src).name.startswith("a"):
                    raise RuntimeError("CUDA out of memory")
                return _fake_inpaint(src, dest, mask_png_bytes, replace)

            _install_fake(monkeypatch, flaky)
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png")
            b = await upload_image(env, ds["id"], "b.png")
            for i in (a, b):
                await _add_detection(env, i["id"])

            job = await wait_for_job(
                env, (await _run_inpaint(env, ds["id"], [a["id"], b["id"]]))["job_id"], timeout=60,
            )
            assert job["status"] == "completed", job
            assert job["result_data"]["failed"] == 1
            assert job["result_data"]["inpainted"] == 1

            async with env.Session() as db:
                failed = await db.get(Image, a["id"])
                dets = (await db.execute(
                    select(func.count()).select_from(Detection).where(Detection.image_id == a["id"])
                )).scalar_one()
            assert not failed.processing_history
            assert dets == 1, "a failed paint must not delete the detection it did not consume"

    run(scenario())


# ── Copy mode ─────────────────────────────────────────────────────────────────

def test_copy_mode_makes_a_nowm_derivative_and_leaves_the_original_alone(tmp_path, monkeypatch):
    """A new row beside the parent, carrying its provenance; the parent keeps its
    pixels, its detections and its flag — it still has the watermark."""
    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake(monkeypatch)
            ds = await env.create_dataset("d", source_name="scrape", license="cc0")
            img = await upload_image(env, ds["id"], "shot.png")
            det_id = await _add_detection(env, img["id"])

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
                row.source_name = "a photographer"
                row.quality_flags = {"has_watermark": True}
                await db.commit()
                src_before = Path(row.file_path).read_bytes()

            job = await wait_for_job(
                env,
                (await _run_inpaint(env, ds["id"], [img["id"]], replace=False))["job_id"],
                timeout=60,
            )
            assert job["status"] == "completed", job
            assert job["result_data"]["inpainted"] == 1

            async with env.Session() as db:
                parent = await db.get(Image, img["id"])
                rows = (await db.execute(
                    select(Image).where(Image.dataset_id == ds["id"])
                )).scalars().all()
                still_there = (await db.execute(select(Detection.id))).scalars().all()
            derivative = next(r for r in rows if r.id != img["id"])

            assert derivative.filename == "shot_nowm.png"
            assert derivative.source_name == "a photographer", "provenance follows the picture"
            assert derivative.phash == "0f0f0f0f0f0f0f0f"
            assert _is_painted(Path(derivative.file_path))
            assert _is_painted(Path(derivative.thumbnail_path))

            assert Path(parent.file_path).read_bytes() == src_before
            assert not parent.processing_history
            assert parent.quality_flags["has_watermark"] is True
            assert det_id in still_there, "copy mode consumes nothing"

    run(scenario())
