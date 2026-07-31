"""Replace-mode LUT on a format PIL cannot write back.

`normalize_image_format` falls back to PNG for `.gif`, `.bmp`, `.tiff` and
`.avif` — all of which `media_types.IMAGE_EXTENSIONS` accepts on ingest — so a
replace-mode grade of one of those writes a *different file* from the one it
read. The row has to follow it, and the original has to go.

Found while writing the video re-extraction pass, which does the same extension
swap deliberately; this is the same operation happening by accident.
"""

from pathlib import Path

from sqlalchemy import select

from backend.models import Image
from backend.tests.conftest import (
    API,
    api_env,
    bmp_bytes,
    jpeg_bytes,
    png_bytes,
    run,
    upload_image,
    wait_for_job,
)


def _identity_cube(path: Path) -> Path:
    """A 2x2x2 identity LUT. R varies fastest, G middle, B slowest."""
    lines = ["TITLE \"identity\"", "LUT_3D_SIZE 2"]
    for b in (0.0, 1.0):
        for g in (0.0, 1.0):
            for r in (0.0, 1.0):
                lines.append(f"{r:.6f} {g:.6f} {b:.6f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_lut_replace_follows_the_png_fallback_and_leaves_no_orphan(tmp_path):
    """The row must point at the file that was written, not at the `.bmp` that
    was read — and the `.bmp` must not be left sitting in `images/`. The
    thumbnail path is keyed on the *stem*, so it does not move."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "shot.bmp", bmp_bytes())
            lut = _identity_cube(tmp_path / "identity.cube")

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
                bmp_path = Path(row.file_path)
                thumb = Path(row.thumbnail_path)
            sidecar = bmp_path.with_suffix(".txt")
            sidecar.write_text("kept", encoding="utf-8")

            r = await env.client.post(f"{API}/lut/run", json={
                "dataset_id": ds["id"], "image_ids": [img["id"]],
                "lut_path": str(lut), "intensity": 1.0, "replace": True,
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
            assert row.filename == bmp_path.with_suffix(".png").name
            assert row.file_path == str(bmp_path.with_suffix(".png"))
            assert row.format == "PNG"
            assert Path(row.file_path).exists()
            assert not bmp_path.exists(), "the .bmp was orphaned in images/"

            # A pure extension change: the stem is unchanged, so neither derived
            # artifact moves.
            assert row.thumbnail_path == str(thumb)
            assert thumb.exists()
            # Content, not just existence — a path that recreated an empty
            # sidecar would pass the existence check having lost the caption.
            assert sidecar.read_text(encoding="utf-8") == "kept"

    run(scenario())


def test_lut_replace_never_clobbers_an_unregistered_file_at_the_fallback_path(tmp_path):
    """The collision has to be caught *before* the save, not after: an
    unregistered file hand-dropped into `images/` has no DB row guarding it, and
    by the time `apply_lut_sync` has run it is already gone."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "shot.bmp", bmp_bytes())
            lut = _identity_cube(tmp_path / "identity.cube")

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
                bmp_path = Path(row.file_path)
                before = bmp_path.read_bytes()
            squatter = bmp_path.with_suffix(".png")
            squatter.write_bytes(b"not an image, and not yours to overwrite")

            r = await env.client.post(f"{API}/lut/run", json={
                "dataset_id": ds["id"], "image_ids": [img["id"]],
                "lut_path": str(lut), "intensity": 1.0, "replace": True,
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job

            assert squatter.read_bytes() == b"not an image, and not yours to overwrite"
            async with env.Session() as db:
                row = await db.get(Image, img["id"])
            assert row.file_path == str(bmp_path)
            assert bmp_path.read_bytes() == before

    run(scenario())


def test_lut_replace_still_serves_the_image_when_the_thumbnail_fails(tmp_path, monkeypatch):
    """PM-013 at the LUT call site. `generate_thumbnail` catches nothing, and it
    used to run between the rename and the loop's single commit — so an
    unwritable `thumbnails/` rolled the row back onto the `.bmp` the grade had
    already replaced, and `GET /images/{id}/file` 404'd.

    Patched on the *router module attribute*: `lut.py` imports the name at module
    import. A plain sync `def`, because the call goes through `run_in_executor`."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "shot.bmp", bmp_bytes())
            lut = _identity_cube(tmp_path / "identity.cube")

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
                bmp_path = Path(row.file_path)

            from backend.routers import lut as lut_router

            def boom(_src, _dest):
                raise OSError("no space left on device")

            monkeypatch.setattr(lut_router, "generate_thumbnail", boom)

            r = await env.client.post(f"{API}/lut/run", json={
                "dataset_id": ds["id"], "image_ids": [img["id"]],
                "lut_path": str(lut), "intensity": 1.0, "replace": True,
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
            png_path = bmp_path.with_suffix(".png")
            assert row.file_path == str(png_path)
            assert png_path.exists()
            assert not bmp_path.exists(), "the superseded .bmp was left behind"

            r = await env.client.get(f"{API}/images/{img['id']}/file")
            assert r.status_code == 200, r.text

            # The user is never told by the log. The count in `result_data` is
            # the only signal that the gallery tile is now lying, so the run has
            # to carry it — `completed` with a silently stale preview is the
            # whole defect.
            assert job["result_data"]["thumbnails_stale"] == 1, job["result_data"]
            assert job["result_data"]["processed"] == 1, job["result_data"]

    run(scenario())


def test_lut_reports_all_zero_counts_on_a_clean_run(tmp_path):
    """The counter has to be honest in both directions: a run where nothing went
    wrong reports four zeros beside its `processed`, so a non-zero
    `thumbnails_stale` means something and cannot be a hardcoded constant."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "plain.png")
            lut = _identity_cube(tmp_path / "identity.cube")

            r = await env.client.post(f"{API}/lut/run", json={
                "dataset_id": ds["id"], "image_ids": [img["id"]],
                "lut_path": str(lut), "intensity": 1.0, "replace": True,
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job

            assert job["result_data"] == {
                "processed": 1, "skipped": 0, "failed": 0, "thumbnails_stale": 0,
            }

    run(scenario())


def test_lut_counts_a_disk_collision_as_skipped_not_processed(tmp_path):
    """The collision `continue` is invisible without the counter — a run that
    graded nothing at all used to end `completed` with an empty `result_data`."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "shot.bmp", bmp_bytes())
            lut = _identity_cube(tmp_path / "identity.cube")

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
                bmp_path = Path(row.file_path)
            bmp_path.with_suffix(".png").write_bytes(b"unregistered squatter")

            r = await env.client.post(f"{API}/lut/run", json={
                "dataset_id": ds["id"], "image_ids": [img["id"]],
                "lut_path": str(lut), "intensity": 1.0, "replace": True,
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job

            assert job["result_data"] == {
                "processed": 0, "skipped": 1, "failed": 0, "thumbnails_stale": 0,
            }

    run(scenario())


async def _two_datasets_with_an_occupied_derived_stem(env, suffix: str) -> dict:
    """Two datasets, each holding a source `{name}.jpg` and a registered
    `{name}{suffix}.png` whose thumbnail already owns the derived stem.

    The occupant's *extension* differs from the one the derived file will be
    written under, so `db_names` — which is per image and was never the bug —
    cannot catch it. Only the thumbnail-stem check can, and only if it is asked
    about the right dataset's `thumbnails/` directory. Returns
    `{name: {"ds", "source", "occupant", "thumb", "thumb_bytes"}}`.
    """
    out: dict[str, dict] = {}
    for i, name in enumerate(("alpha", "beta")):
        ds = await env.create_dataset(name)
        source = await upload_image(env, ds["id"], f"{name}.jpg", jpeg_bytes((200, 60 + i, 20)))
        occupant = await upload_image(
            env, ds["id"], f"{name}{suffix}.png", png_bytes((7, 9 + i, 240), (24, 24))
        )
        async with env.Session() as db:
            thumb = Path((await db.get(Image, occupant["id"])).thumbnail_path)
        assert thumb.exists(), thumb
        out[name] = {
            "ds": ds, "source": source, "occupant": occupant,
            "thumb": thumb, "thumb_bytes": thumb.read_bytes(),
        }
    return out


def test_lut_copy_across_two_datasets_does_not_share_one_thumbnail_stem_set(tmp_path):
    """PM-007 class. `occupied_thumb_stems` was one flat set built from
    `images[0]`'s thumbnail directory, while `dest_images` is chosen **per image**
    inside the loop and the job selects on `Image.id.in_(...)` with no dataset
    constraint. A selection spanning two datasets therefore asked one dataset's
    `thumbnails/` about the other's stems: whichever dataset did not seed the set
    had its derived name accepted, and the new `.webp` landed on a live sibling's.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            env_by_name = await _two_datasets_with_an_occupied_derived_stem(env, "_lut")
            lut = _identity_cube(tmp_path / "identity.cube")

            r = await env.client.post(f"{API}/lut/run", json={
                "dataset_id": env_by_name["alpha"]["ds"]["id"],
                "image_ids": [e["source"]["id"] for e in env_by_name.values()],
                "lut_path": str(lut), "intensity": 1.0, "replace": False,
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job
            assert job["result_data"]["processed"] == 2, job["result_data"]

            for name, e in env_by_name.items():
                assert e["thumb"].read_bytes() == e["thumb_bytes"], (
                    f"{name}: the derived thumbnail clobbered a live sibling's"
                )
                async with env.Session() as db:
                    rows = (await db.execute(select(Image).where(
                        Image.dataset_id == e["ds"]["id"]
                    ))).scalars().all()
                derived = [
                    row for row in rows
                    if row.id not in (e["source"]["id"], e["occupant"]["id"])
                ]
                assert len(derived) == 1, [row.filename for row in rows]
                assert derived[0].filename == f"{name}_lut_001.jpg"
                assert Path(derived[0].thumbnail_path) != e["thumb"]
                assert Path(derived[0].thumbnail_path).exists()

    run(scenario())


def test_lut_replace_on_a_png_is_unaffected(tmp_path):
    """The common case stays a plain in-place overwrite — no rename, nothing
    derived moves, and the row keeps every field but its size and history."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "plain.png")
            lut = _identity_cube(tmp_path / "identity.cube")

            r = await env.client.post(f"{API}/lut/run", json={
                "dataset_id": ds["id"], "image_ids": [img["id"]],
                "lut_path": str(lut), "intensity": 1.0, "replace": True,
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job

            async with env.Session() as db:
                rows = (await db.execute(select(Image))).scalars().all()
            assert len(rows) == 1
            assert rows[0].filename == "plain.png"
            assert Path(rows[0].file_path).exists()
            assert [e["op"] for e in rows[0].processing_history] == ["lut"]

    run(scenario())
