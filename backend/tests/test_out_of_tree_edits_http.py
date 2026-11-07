"""In-place edits refuse a file that resolves outside the datasets folder (B4).

Seven edit sites wrote through a raw `Path(img.file_path)`. The likely route is
accidental: a rescan registers a symlink in `{ds}/images/` under its in-tree
path, and a replace-mode edit then saves *through* it, overwriting a file
outside `settings.datasets_dir`. The versioning backup refuses the same path and
lets the overwrite go ahead, so no snapshot holds the original.

Most tests here point the row straight at a file outside the tree instead of
through a link. `contained_path` resolves both the same way, and this way the
tests run on a Windows account that cannot create symlinks; the one test that
plants a real link is gated on `needs_symlinks` and runs in CI.

The last two pin the other half: once the gate passes, the stored form is what
gets written back. With `datasets_dir` itself behind a link — the arrangement
`docs/datasets.md` recommends for keeping data on another drive — a resolved
path written into a row would no longer match the paths every other row holds.
"""
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.models import Image
from backend.models.detection import Detection
from backend.tests.conftest import (
    API,
    api_env,
    bmp_bytes,
    needs_symlinks,
    png_bytes,
    run,
    upload_image,
    wait_for_job,
)
from backend.tests.test_inpaint_http import _add_detection, _run_inpaint
from backend.tests.test_inpaint_http import _install_fake as _install_fake_inpaint
from backend.tests.test_lut_replace_extension_http import _identity_cube
from backend.tests.test_upscale_png_fallback_http import MODEL
from backend.tests.test_upscale_png_fallback_http import _install_fake as _install_fake_upscale
from backend.utils import OUT_OF_TREE_MSG, contained_path

REASON = "outside the datasets folder"


async def _point_outside(env, image_id: str, tmp_path: Path) -> Path:
    """Move the row's file out of the tree and point the row at it. Returns the
    outside path, whose bytes every test then checks are untouched."""
    async with env.Session() as db:
        row = await db.get(Image, image_id)
        inside = Path(row.file_path)
        outside = tmp_path / "elsewhere" / inside.name
        outside.parent.mkdir(exist_ok=True)
        outside.write_bytes(inside.read_bytes())
        row.file_path = str(outside)
        await db.commit()
    return outside


async def _image_count(env) -> int:
    async with env.Session() as db:
        return len((await db.execute(select(Image.id))).all())


def _assert_refused(job: dict, *, done_key: str = "processed") -> None:
    """Refused, and the reason kept once the live `Failed:` message is gone —
    every job here writes `failed_count` and `failure_summary` (follow-up 8)."""
    assert job["status"] == "completed", job
    rd = job["result_data"]
    assert rd[done_key] == 0 and rd["failed"] == 1, rd
    assert rd["failed_count"] == 1, rd
    assert REASON in rd["failure_summary"], rd


# ── The jobs ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("replace", [True, False])
def test_upscale_refuses_a_file_outside_the_tree(tmp_path, monkeypatch, replace):
    """Copy mode too: its destination is derived from the source's folder, so an
    out-of-tree source would put the new file out of tree as well."""
    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake_upscale(monkeypatch)
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(20, 10)))
            outside = await _point_outside(env, img["id"], tmp_path)
            before = outside.read_bytes()

            r = await env.client.post(f"{API}/upscaling/run", json={
                "dataset_id": ds["id"], "image_ids": [img["id"]],
                "model_path": MODEL, "replace": replace,
            })
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)

            _assert_refused(job)
            assert REASON in job["result_data"]["failure_summary"]
            assert outside.read_bytes() == before
            assert sorted(p.name for p in outside.parent.iterdir()) == [outside.name]
            assert await _image_count(env) == 1

    run(scenario())


def test_lut_refuses_a_file_outside_the_tree(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(20, 10)))
            outside = await _point_outside(env, img["id"], tmp_path)
            before = outside.read_bytes()
            lut = _identity_cube(tmp_path / "identity.cube")

            r = await env.client.post(f"{API}/lut/run", json={
                "dataset_id": ds["id"], "image_ids": [img["id"]],
                "lut_path": str(lut), "intensity": 1.0, "replace": True,
            })
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)

            _assert_refused(job)
            assert REASON in job["result_data"]["failure_summary"]
            assert outside.read_bytes() == before

    run(scenario())


def test_detection_crop_refuses_a_file_outside_the_tree(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(40, 20)))
            async with env.Session() as db:
                db.add(Detection(
                    image_id=img["id"], label="face", bbox=[0.25, 0.25, 0.75, 0.75],
                    score=0.9, model="test", task="detect",
                ))
                await db.commit()
            outside = await _point_outside(env, img["id"], tmp_path)
            before = outside.read_bytes()

            r = await env.client.post(f"{API}/detection/crop", json={
                "dataset_id": ds["id"], "image_ids": [img["id"]], "replace": True,
            })
            job = await wait_for_job(env, r.json()["job_id"])

            _assert_refused(job, done_key="cropped")
            assert outside.read_bytes() == before

    run(scenario())


@pytest.mark.parametrize("route,body", [
    ("resize", {"scale": 0.5}),
    ("crop", {"target_ar": 1.0}),
])
def test_batch_edits_refuse_a_file_outside_the_tree(tmp_path, route, body):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(40, 20)))
            outside = await _point_outside(env, img["id"], tmp_path)
            before = outside.read_bytes()

            r = await env.client.post(
                f"{API}/images/batch/{route}", json={"image_ids": [img["id"]], **body}
            )
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])

            _assert_refused(job)
            assert outside.read_bytes() == before
            assert sorted(p.name for p in outside.parent.iterdir()) == [outside.name]

    run(scenario())


def test_inpaint_still_refuses_a_file_outside_the_tree(tmp_path, monkeypatch):
    """Inpaint already gated; pinned here beside the sites that copied it."""
    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake_inpaint(monkeypatch)
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(48, 32)))
            await _add_detection(env, img["id"])
            outside = await _point_outside(env, img["id"], tmp_path)
            before = outside.read_bytes()

            started = await _run_inpaint(env, ds["id"], [img["id"]])
            job = await wait_for_job(env, started["job_id"], timeout=60)

            _assert_refused(job, done_key="inpainted")
            assert outside.read_bytes() == before

    run(scenario())


# ── The single-image routes ───────────────────────────────────────────────────

@pytest.mark.parametrize("route,body", [
    ("resize", {"scale": 0.5}),
    ("crop", {"x": 0, "y": 0, "width": 10, "height": 10, "replace": True}),
    # The copy branch derives its destination from the source's folder too.
    ("crop", {"x": 0, "y": 0, "width": 10, "height": 10, "replace": False}),
])
def test_single_edits_answer_403_for_a_file_outside_the_tree(tmp_path, route, body):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(40, 20)))
            outside = await _point_outside(env, img["id"], tmp_path)
            before = outside.read_bytes()

            r = await env.client.post(f"{API}/images/{img['id']}/{route}", json=body)

            assert r.status_code == 403, r.text
            assert REASON in r.json()["detail"]
            assert outside.read_bytes() == before
            assert sorted(p.name for p in outside.parent.iterdir()) == [outside.name]
            assert await _image_count(env) == 1

    run(scenario())


# ── The helper ────────────────────────────────────────────────────────────────

def test_contained_path_refuses_rather_than_raises_on_an_unresolvable_path(tmp_path, monkeypatch):
    """Its docstring always said "never raises", but `resolve()` can: a symlink
    loop is `RuntimeError` or `OSError` by Python version. Inside a job loop a
    raise fails the whole run instead of the one image."""
    import backend.utils as utils

    def boom(*_a, **_kw):
        raise RuntimeError("Symlink loop from 'x'")

    monkeypatch.setattr(utils, "within_datasets_dir", boom)
    assert contained_path(str(tmp_path / "x.png"), tmp_path, context="Test") is None


def test_the_refusal_names_its_reason():
    assert REASON in OUT_OF_TREE_MSG


# ── Real links ────────────────────────────────────────────────────────────────

@needs_symlinks
def test_upscale_refuses_a_link_in_images_that_points_outside(tmp_path, monkeypatch):
    """The live route: a link registered under its in-tree path."""
    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake_upscale(monkeypatch)
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(20, 10)))
            outside = tmp_path / "secret.png"
            outside.write_bytes(png_bytes(size=(20, 10)))
            before = outside.read_bytes()
            async with env.Session() as db:
                row = await db.get(Image, img["id"])
                link = Path(row.file_path).with_name("link.png")
                link.symlink_to(outside)
                row.file_path = str(link)
                await db.commit()

            r = await env.client.post(f"{API}/upscaling/run", json={
                "dataset_id": ds["id"], "image_ids": [img["id"]],
                "model_path": MODEL, "replace": True,
            })
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)

            _assert_refused(job)
            assert outside.read_bytes() == before

    run(scenario())


def _dir_link(link: Path, target: Path) -> None:
    """A directory link: a symlink where the account may make one, else a
    Windows junction, which needs no privilege and is what a user keeping data
    on another drive would most likely use."""
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError:
        if sys.platform != "win32":
            raise
    import _winapi

    _winapi.CreateJunction(str(target), str(link))


@pytest.mark.parametrize("replace", [True, False])
def test_edits_keep_the_stored_form_under_a_linked_datasets_dir(tmp_path, monkeypatch, replace):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        _dir_link(link, real)
    except OSError as exc:
        pytest.skip(f"cannot create a directory link here: {exc}")

    async def scenario():
        async with api_env(link) as env:
            _install_fake_upscale(monkeypatch)
            ds = await env.create_dataset("d")
            # A .bmp, so a replace writes a new .png and the row's path changes.
            img = await upload_image(env, ds["id"], "a.bmp", bmp_bytes())
            r = await env.client.post(f"{API}/upscaling/run", json={
                "dataset_id": ds["id"], "image_ids": [img["id"]],
                "model_path": MODEL, "replace": replace,
            })
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["result_data"]["processed"] == 1, job

            async with env.Session() as db:
                rows = (await db.execute(select(Image))).scalars().all()
            assert len(rows) == (1 if replace else 2)
            for row in rows:
                assert row.file_path.startswith(str(link)), row.file_path
                assert row.thumbnail_path.startswith(str(link)), row.thumbnail_path

    run(scenario())


def test_inpaint_keeps_the_stored_form_under_a_linked_datasets_dir(tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        _dir_link(link, real)
    except OSError as exc:
        pytest.skip(f"cannot create a directory link here: {exc}")

    async def scenario():
        async with api_env(link) as env:
            _install_fake_inpaint(monkeypatch)
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.bmp", bmp_bytes())
            b = await upload_image(env, ds["id"], "b.png", png_bytes(size=(48, 32)))
            await _add_detection(env, a["id"])
            await _add_detection(env, b["id"])
            # Replace on the .bmp (its path changes to .png), copy on the .png.
            for ids, replace in (([a["id"]], True), ([b["id"]], False)):
                started = await _run_inpaint(env, ds["id"], ids, replace=replace)
                job = await wait_for_job(env, started["job_id"], timeout=60)
                assert job["result_data"]["inpainted"] == 1, job

            async with env.Session() as db:
                rows = (await db.execute(select(Image))).scalars().all()
            assert len(rows) == 3
            for row in rows:
                assert row.file_path.startswith(str(link)), row.file_path
                assert row.thumbnail_path.startswith(str(link)), row.thumbnail_path

    run(scenario())

