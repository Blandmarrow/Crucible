"""Request-level regression tests for source & license provenance.

These go through `backend.main.app` (see conftest.py) rather than calling service
helpers, because that is the gap the branch's blockers fell through: the suite was
green while `POST /comfy/run` died on its first image, and while an over-long
captured license made an image's provenance permanently unsaveable. Both are
router-shaped failures that no service-level test can reach.
"""
import csv
import shutil
import json
import os
from pathlib import Path

from PIL import Image as PilImage
from sqlalchemy import select

from backend.models.image import Image
from backend.tests.conftest import (
    API,
    api_env,
    png_bytes,
    run,
    upload_image,
    wait_for_job,
)


# --- B1: the ComfyUI import path ----------------------------------------


class _FakeComfyClient:
    """Stands in for ComfyClient so a run can execute with no ComfyUI present.

    The point is to drive the real `run_plan` job body — the import block that
    unpacked `_register_file_sync`'s return value is the code under test.
    """

    def __init__(self, url: str):
        self.url = url

    async def submit(self, workflow, client_id=None):
        return "prompt-1"

    async def poll_history(self, prompt_id):
        return {"outputs": {"9": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}}}

    async def fetch_image(self, filename, subfolder="", type="output"):
        return png_bytes()

    async def interrupt(self):
        return None


def test_comfy_run_imports_images_through_the_real_router(tmp_path, monkeypatch):
    """`POST /comfy/run` must import its outputs, not die unpacking a 3-tuple.

    `_register_file_sync` grew a `provenance` field; the wrapper here still
    declared and unpacked two values, so every run raised ValueError *after* the
    image and thumbnail were on disk — matching neither handler in the row loop,
    so the files leaked and the row stayed wedged at "running".
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            import backend.routers.comfy as comfy_router
            monkeypatch.setattr(comfy_router, "ComfyClient", _FakeComfyClient)

            r = await env.client.patch(f"{API}/settings/thresholds", json={"comfyui_url": "http://fake:8188"})
            assert r.status_code == 200, r.text

            ds = await env.create_dataset("comfy-ds")
            workflow = {
                "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sdxl.safetensors"}},
                "9": {"class_type": "SaveImage", "inputs": {}},
            }
            r = await env.client.post(f"{API}/comfy/plans", json={
                "dataset_id": ds["id"], "name": "plan", "workflow_json": workflow,
                "output_node_ids": ["9"],
            })
            assert r.status_code == 200, r.text
            plan_id = r.json()["id"]

            r = await env.client.post(f"{API}/comfy/plans/{plan_id}/rows", json={"values": {}})
            assert r.status_code == 200, r.text

            r = await env.client.post(f"{API}/comfy/run", json={"plan_id": plan_id})
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

            rows = (await env.client.get(f"{API}/comfy/plans/{plan_id}/rows")).json()
            assert [row["status"] for row in rows] == ["completed"]

            images = (await env.client.get(f"{API}/images/", params={"dataset_id": ds["id"]})).json()
            assert len(images) == 1

            # No orphans: exactly the one image + its thumbnail + caption sidecar.
            ds_dir = env.datasets_dir / next(iter(p.name for p in env.datasets_dir.iterdir()))
            assert len(list((ds_dir / "images").glob("*.png"))) == 1
            assert len(list((ds_dir / "thumbnails").glob("*.webp"))) == 1

            # And the run's provenance landed: the dataset asserts no license, so
            # the output is stamped as synthetic rather than inheriting.
            detail = (await env.client.get(f"{API}/images/{images[0]['id']}")).json()
            assert detail["provenance"]["license"] == "synthetic"
            assert detail["provenance"]["source_name"] == "ComfyUI"
            assert detail["provenance"]["source_meta"]["checkpoint"] == "sdxl.safetensors"

    run(scenario())


async def _run_one_row_plan(env, ds_id: str, **plan_fields) -> dict:
    """Create a one-row plan, run it, and return the imported image's detail JSON."""
    before = {
        i["id"]
        for i in (await env.client.get(f"{API}/images/", params={"dataset_id": ds_id})).json()
    }
    r = await env.client.post(f"{API}/comfy/plans", json={
        "dataset_id": ds_id, "name": plan_fields.pop("name", "p"),
        "workflow_json": {"9": {"class_type": "SaveImage", "inputs": {}}},
        "output_node_ids": ["9"], **plan_fields,
    })
    assert r.status_code == 200, r.text
    plan_id = r.json()["id"]
    await env.client.post(f"{API}/comfy/plans/{plan_id}/rows", json={"values": {}})
    r = await env.client.post(f"{API}/comfy/run", json={"plan_id": plan_id})
    job = await wait_for_job(env, r.json()["job_id"])
    assert job["status"] == "completed", job
    images = (await env.client.get(f"{API}/images/", params={"dataset_id": ds_id})).json()
    new = [i for i in images if i["id"] not in before]
    assert len(new) == 1, new
    return (await env.client.get(f"{API}/images/{new[0]['id']}")).json()


def test_comfy_output_provenance_follows_the_plans_synthetic_toggle(tmp_path, monkeypatch):
    """Whether ComfyUI output is self-created is the *plan's* declaration.

    It was inferred from the dataset instead: a dataset recording any provenance
    default switched every plan to full inheritance, so a text2img plan on a
    licensed dataset produced generated images carrying a real photographer's
    credit — while stamping `license="synthetic"` unconditionally would instead
    launder a genuinely derived image's CC-BY-NC source past the commercial-use
    export filter. Neither is inferable at run time; only the plan knows.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            import backend.routers.comfy as comfy_router
            monkeypatch.setattr(comfy_router, "ComfyClient", _FakeComfyClient)
            await env.client.patch(f"{API}/settings/thresholds", json={"comfyui_url": "http://fake:8188"})

            ds = await env.create_dataset(
                "licensed", license="CC-BY-NC-4.0", source_name="Flickr",
                source_url="https://flickr.test/p/1", attribution="Photo by Jane Doe")

            # Toggle on (the default): the run's own license and source win over
            # the dataset's, so the export can tell this from a licensed image.
            detail = await _run_one_row_plan(env, ds["id"], name="synthetic")
            prov = detail["provenance"]
            assert prov["license"] == "synthetic"
            assert prov["source_name"] == "ComfyUI"
            # A blank attribution would be read as "inherit" and credit a real
            # photographer for a generated image, so the run writes a concrete
            # one naming its own plan — true, and non-blank, which is what stops
            # the inheritance.
            assert prov["attribution"] == 'Generated by ComfyUI plan "synthetic"'
            # `source_url` still inherits: every candidate value would be
            # invented. Documented in `_comfy_output_provenance`.
            assert prov["inherited"] == ["source_url"]

            # Toggle off: derived output keeps the source's whole story. All four
            # fields inherit together — "" cannot opt one out, because
            # `resolve_provenance` reads "" and NULL alike as "inherit".
            detail = await _run_one_row_plan(
                env, ds["id"], name="derived", output_is_synthetic=False)
            prov = detail["provenance"]
            assert prov["license"] == "CC-BY-NC-4.0"
            assert prov["attribution"] == "Photo by Jane Doe"
            assert prov["source_url"] == "https://flickr.test/p/1"
            assert set(prov["inherited"]) == {
                "source_name", "source_url", "license", "attribution"}

            # What the whole thing is for: the export must not credit Jane Doe
            # for the generated image. Both images are in one dataset, so one
            # CREDITS.md has to keep them apart.
            out = tmp_path / "credits"
            r = await env.client.post(f"{API}/export/plain", json={
                "dataset_id": ds["id"], "output_dir": str(out),
            })
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

            rows = {r["file"]: r for r in
                    csv.DictReader((out / "licenses.csv").open(encoding="utf-8"))}
            assert len(rows) == 2
            synthetic_row = next(r for r in rows.values() if r["license"] == "synthetic")
            derived_row = next(r for r in rows.values() if r["license"] == "CC-BY-NC-4.0")
            assert synthetic_row["attribution"] == 'Generated by ComfyUI plan "synthetic"'
            assert derived_row["attribution"] == "Photo by Jane Doe"

            credits = (out / "CREDITS.md").read_text(encoding="utf-8")
            synthetic_section = credits.split("## Synthetic (AI-generated)")[1].split("## ")[0]
            assert "Jane Doe" not in synthetic_section
            assert 'Generated by ComfyUI plan "synthetic"' in synthetic_section
            # The remaining half, pinned so it stays visible rather than
            # surprising someone reading a manifest: `source_url` has no honest
            # concrete value for a generated image, so it still inherits and the
            # synthetic section links the dataset's URL. Fixing that needs a way
            # to say "explicitly nothing" — see `_comfy_output_provenance`. If
            # this assertion starts failing, the residual is gone: delete it.
            assert "https://flickr.test/p/1" in synthetic_section

    run(scenario())


def test_comfy_plan_synthetic_toggle_round_trips_through_the_api(tmp_path):
    """Default on, PATCHable to off and back — `False` is not "absent"."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("toggle")
            r = await env.client.post(f"{API}/comfy/plans", json={
                "dataset_id": ds["id"], "name": "p", "workflow_json": {},
            })
            assert r.status_code == 200, r.text
            plan = r.json()
            assert plan["output_is_synthetic"] is True

            r = await env.client.patch(
                f"{API}/comfy/plans/{plan['id']}", json={"output_is_synthetic": False})
            assert r.json()["output_is_synthetic"] is False
            # An unrelated PATCH must not silently flip it back on.
            r = await env.client.patch(f"{API}/comfy/plans/{plan['id']}", json={"name": "p2"})
            assert r.json()["output_is_synthetic"] is False
            r = await env.client.patch(
                f"{API}/comfy/plans/{plan['id']}", json={"output_is_synthetic": True})
            assert r.json()["output_is_synthetic"] is True

    run(scenario())


def test_comfy_output_on_a_bare_dataset_records_only_what_it_knows(tmp_path, monkeypatch):
    """With nothing recorded anywhere, the run stamps exactly what it can vouch for."""
    async def scenario():
        async with api_env(tmp_path) as env:
            import backend.routers.comfy as comfy_router
            monkeypatch.setattr(comfy_router, "ComfyClient", _FakeComfyClient)
            await env.client.patch(f"{API}/settings/thresholds", json={"comfyui_url": "http://fake:8188"})

            ds = await env.create_dataset("bare")
            r = await env.client.post(f"{API}/comfy/plans", json={
                "dataset_id": ds["id"], "name": "p",
                "workflow_json": {"9": {"class_type": "SaveImage", "inputs": {}}},
                "output_node_ids": ["9"],
            })
            plan_id = r.json()["id"]
            await env.client.post(f"{API}/comfy/plans/{plan_id}/rows", json={"values": {}})
            r = await env.client.post(f"{API}/comfy/run", json={"plan_id": plan_id})
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

            images = (await env.client.get(f"{API}/images/", params={"dataset_id": ds["id"]})).json()
            detail = (await env.client.get(f"{API}/images/{images[0]['id']}")).json()
            assert detail["provenance"]["license"] == "synthetic"
            assert detail["provenance"]["source_name"] == "ComfyUI"
            # The generator is recorded even here, where nothing would inherit
            # anyway: the value is a true statement of origin, so it does not
            # depend on what the dataset happens to declare.
            assert detail["attribution"] == 'Generated by ComfyUI plan "p"'
            # NULL, not "": there is no URL, and none is the honest record.
            assert detail["source_url"] is None

    run(scenario())


# --- B3: capture truncates, the API rejects ------------------------------


def test_oversized_sidecar_value_truncates_and_stays_editable(tmp_path):
    """An over-long captured value must not make provenance permanently unsaveable.

    Capture had no cap, so a 500-char `rights` string stored a 506-char
    `other:` license — over the column width, so the edit endpoint 422'd on
    every subsequent save of that image.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("import-ds")

            src = tmp_path / "scrape"
            src.mkdir()
            (src / "pic.png").write_bytes(png_bytes())
            (src / "pic.png.json").write_text(json.dumps({
                "category": "somesite",
                "rights": "R" * 500,
                "author": "A" * 400,
            }), encoding="utf-8")

            r = await env.client.post(
                f"{API}/datasets/{ds['id']}/import",
                json={"folder_path": str(src), "import_captions": False},
            )
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

            images = (await env.client.get(f"{API}/images/", params={"dataset_id": ds["id"]})).json()
            assert len(images) == 1
            image_id = images[0]["id"]

            detail = (await env.client.get(f"{API}/images/{image_id}")).json()
            assert len(detail["license"]) <= 64
            assert detail["license"].startswith("other:RRR")
            assert len(detail["attribution"]) <= 2000

            # The whole point: the truncated row round-trips through the editor.
            r = await env.client.patch(
                f"{API}/images/{image_id}/provenance", json={"source_name": "Edited"})
            assert r.status_code == 200, r.text
            assert r.json()["source_name"] == "Edited"
            assert r.json()["license"] == detail["license"]

    run(scenario())


def test_api_rejects_a_license_that_normalizes_past_the_column(tmp_path):
    """API direction rejects where capture truncates — 422, never a silent 70-char row."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"])

            r = await env.client.patch(
                f"{API}/images/{img['id']}/provenance", json={"license": "z" * 64})
            assert r.status_code == 422, r.text

            r = await env.client.patch(
                f"{API}/images/{img['id']}/provenance", json={"license": "z" * 58})
            assert r.status_code == 200, r.text
            assert r.json()["license"] == "other:" + "z" * 58

    run(scenario())


# --- PATCH /provenance and bulk-provenance -------------------------------


def test_patch_provenance_clears_with_empty_and_returns_detections(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d", license="CC-BY-4.0", source_name="Flickr")
            img = await upload_image(env, ds["id"])

            r = await env.client.patch(f"{API}/images/{img['id']}/provenance", json={
                "license": "CC0-1.0", "source_name": "Unsplash",
            })
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["provenance"]["license"] == "CC0-1.0"
            # Present (empty here) so a client can seed its cache from this response
            # instead of showing an image that has lost its detections.
            assert body["detections"] == []

            # "" clears the override, so the dataset default applies again.
            r = await env.client.patch(f"{API}/images/{img['id']}/provenance", json={"license": ""})
            assert r.status_code == 200, r.text
            assert r.json()["license"] is None
            assert r.json()["provenance"]["license"] == "CC-BY-4.0"
            assert "license" in r.json()["provenance"]["inherited"]

    run(scenario())


def test_provenance_writes_are_blocked_while_the_dataset_is_busy(tmp_path):
    """Every sibling write guards; restore_snapshot writes provenance, so this must too."""
    async def scenario():
        async with api_env(tmp_path) as env:
            from backend.services import dataset_busy

            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"])

            with dataset_busy.busy(ds["id"], "versioning"):
                r = await env.client.patch(
                    f"{API}/images/{img['id']}/provenance", json={"license": "owned"})
                assert r.status_code == 409, r.text

                r = await env.client.post(f"{API}/images/bulk-provenance", json={
                    "dataset_id": ds["id"], "image_ids": [img["id"]], "license": "owned",
                })
                assert r.status_code == 409, r.text

            r = await env.client.patch(
                f"{API}/images/{img['id']}/provenance", json={"license": "owned"})
            assert r.status_code == 200, r.text

    run(scenario())


def test_bulk_provenance_guards_every_dataset_in_a_cross_dataset_selection(tmp_path):
    """The selection can span datasets — body.dataset_id is not the whole story."""
    async def scenario():
        async with api_env(tmp_path) as env:
            from backend.services import dataset_busy

            ds_a = await env.create_dataset("a")
            ds_b = await env.create_dataset("b")
            img_a = await upload_image(env, ds_a["id"])
            img_b = await upload_image(env, ds_b["id"])
            ids = [img_a["id"], img_b["id"]]

            # Busy on the dataset that is NOT body.dataset_id.
            with dataset_busy.busy(ds_b["id"], "versioning"):
                r = await env.client.post(f"{API}/images/bulk-provenance", json={
                    "dataset_id": ds_a["id"], "image_ids": ids, "license": "owned",
                })
                assert r.status_code == 409, r.text

            r = await env.client.post(f"{API}/images/bulk-provenance", json={
                "dataset_id": ds_a["id"], "image_ids": ids, "license": "owned",
            })
            assert r.status_code == 200, r.text
            assert r.json()["updated"] == 2      # both, not just ds_a's

            for image_id in ids:
                detail = (await env.client.get(f"{API}/images/{image_id}")).json()
                assert detail["license"] == "owned"

    run(scenario())


def test_bulk_provenance_include_flagged_comes_from_the_body(tmp_path):
    """Hardcoding include_flagged=True selected the opposite set from bulk_rename."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            blurry = await upload_image(env, ds["id"], "blurry.png")
            clean = await upload_image(env, ds["id"], "clean.png", png_bytes((200, 30, 30)))

            async with env.Session() as db:
                row = (await db.execute(
                    select(Image).where(Image.id == blurry["id"]))).scalar_one()
                row.quality_flags = {"is_blurry": True}
                await db.commit()

            # include_flagged=False → the flagged image is excluded (bulk_rename's sense).
            r = await env.client.post(f"{API}/images/bulk-provenance", json={
                "dataset_id": ds["id"], "quality_flags": ["is_blurry"],
                "include_flagged": False, "license": "owned",
            })
            assert r.status_code == 200, r.text
            assert r.json()["updated"] == 1
            assert (await env.client.get(f"{API}/images/{clean['id']}")).json()["license"] == "owned"
            assert (await env.client.get(f"{API}/images/{blurry['id']}")).json()["license"] is None

            # include_flagged=True → only the flagged one.
            r = await env.client.post(f"{API}/images/bulk-provenance", json={
                "dataset_id": ds["id"], "quality_flags": ["is_blurry"],
                "include_flagged": True, "license": "research-only",
            })
            assert r.json()["updated"] == 1
            assert (await env.client.get(f"{API}/images/{blurry['id']}")).json()["license"] == "research-only"

            # Omitted entirely → the same sense as BulkCountRequest and
            # bulk_rename, i.e. False. Every assertion above passes the flag
            # explicitly, so nothing pinned the default it falls back to.
            r = await env.client.post(f"{API}/images/bulk-provenance", json={
                "dataset_id": ds["id"], "quality_flags": ["is_blurry"],
                "license": "public-domain",
            })
            assert r.json()["updated"] == 1
            assert (await env.client.get(f"{API}/images/{clean['id']}")).json()["license"] == "public-domain"
            assert (await env.client.get(f"{API}/images/{blurry['id']}")).json()["license"] == "research-only"

    run(scenario())


# --- cross-dataset move/copy through the real router ---------------------


def test_cross_dataset_move_materializes_each_row_against_its_own_dataset(tmp_path):
    """The `_Stub`-level test passes even if the router resolves the whole batch
    against `rows[0]`'s dataset — which irreversibly mislabels every other image."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds_a = await env.create_dataset("a", license="CC-BY-4.0", source_name="Flickr")
            ds_b = await env.create_dataset("b", license="CC-BY-NC-SA-4.0", source_name="Danbooru")
            ds_dest = await env.create_dataset("dest", license="owned", source_name="Me")

            img_a = await upload_image(env, ds_a["id"], "a.png")
            img_b = await upload_image(env, ds_b["id"], "b.png", png_bytes((9, 9, 9)))

            r = await env.client.post(f"{API}/images/batch/move-dataset", json={
                "image_ids": [img_a["id"], img_b["id"]],
                "target_dataset_id": ds_dest["id"],
            })
            assert r.status_code == 200, r.text

            a = (await env.client.get(f"{API}/images/{img_a['id']}")).json()
            b = (await env.client.get(f"{API}/images/{img_b['id']}")).json()
            assert a["dataset_id"] == b["dataset_id"] == ds_dest["id"]
            # Concrete values, each from its *own* source dataset — not "owned",
            # and not both from whichever dataset happened to be first.
            assert (a["license"], a["source_name"]) == ("CC-BY-4.0", "Flickr")
            assert (b["license"], b["source_name"]) == ("CC-BY-NC-SA-4.0", "Danbooru")

    run(scenario())


def test_cross_dataset_copy_materializes_each_row_against_its_own_dataset(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds_a = await env.create_dataset("a", license="CC-BY-4.0")
            ds_b = await env.create_dataset("b", license="research-only")
            ds_dest = await env.create_dataset("dest", license="owned")

            img_a = await upload_image(env, ds_a["id"], "a.png")
            img_b = await upload_image(env, ds_b["id"], "b.png", png_bytes((9, 9, 9)))

            r = await env.client.post(f"{API}/images/batch/copy-dataset", json={
                "image_ids": [img_a["id"], img_b["id"]],
                "target_dataset_id": ds_dest["id"],
            })
            assert r.status_code == 200, r.text

            copies = (await env.client.get(
                f"{API}/images/", params={"dataset_id": ds_dest["id"]})).json()
            by_name = {c["filename"]: c for c in copies}
            assert len(by_name) == 2
            licenses = {c["filename"]: c["license"] for c in copies}
            assert set(licenses.values()) == {"CC-BY-4.0", "research-only"}
            # Originals untouched — a copy must not rewrite its source.
            assert (await env.client.get(f"{API}/images/{img_a['id']}")).json()["license"] is None

    run(scenario())


# --- derived images keep their parent's provenance -----------------------


def test_crop_copies_parent_provenance(tmp_path):
    """A derivative of a CC-BY-SA image is still CC-BY-SA (same dataset → raw copy)."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(64, 64)))

            r = await env.client.patch(f"{API}/images/{img['id']}/provenance", json={
                "license": "CC-BY-SA-4.0", "source_name": "Flickr", "attribution": "alice",
            })
            assert r.status_code == 200, r.text

            r = await env.client.post(f"{API}/images/{img['id']}/crop", json={
                "x": 0, "y": 0, "width": 32, "height": 32,
            })
            assert r.status_code == 200, r.text
            crop_id = r.json()["id"]

            crop = (await env.client.get(f"{API}/images/{crop_id}")).json()
            assert crop["license"] == "CC-BY-SA-4.0"
            assert crop["source_name"] == "Flickr"
            assert crop["attribution"] == "alice"

    run(scenario())


def test_lut_and_upscale_derivatives_copy_provenance_over_the_async_session(tmp_path, monkeypatch):
    """`copy_provenance` reads the **deferred** `source_meta`.

    Both jobs `undefer` it when building their query. A `_Stub`-based unit test
    cannot see that: a missing `undefer` is a lazy load on an async session, which
    raises MissingGreenlet only on the live path. Only the pixel work is faked —
    the query, the copy and the insert are the real ones.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            import backend.routers.lut as lut_router
            import backend.routers.upscaling as upscale_router
            from backend.ml import lut_processor, upscaler

            def _fake_process(src, out_path, *a, **kw):
                # Same contract as the real helpers: write the file, return the
                # {width, height, file_size_bytes, format, out_path} dict.
                shutil.copy2(src, out_path)
                with PilImage.open(out_path) as probe:
                    w, h = probe.size
                return {
                    "width": w, "height": h, "format": "PNG",
                    "file_size_bytes": os.path.getsize(out_path), "out_path": out_path,
                }

            monkeypatch.setattr(lut_processor, "apply_lut_sync", _fake_process)
            monkeypatch.setattr(lut_router, "apply_lut_sync", _fake_process)
            monkeypatch.setattr(upscaler, "upscale_image_sync", _fake_process)
            monkeypatch.setattr(upscale_router, "upscale_image_sync", _fake_process)
            monkeypatch.setattr(upscaler, "_detect_scale", lambda *a, **kw: 1)

            ds = await env.create_dataset("derive")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(32, 32)))
            async with env.Session() as db:
                row = (await db.execute(select(Image).where(Image.id == img["id"]))).scalar_one()
                row.license, row.source_name, row.source_meta = "CC-BY-SA-4.0", "Flickr", {"post_id": 7}
                await db.commit()

            for path, body in (
                ("/lut/run", {"dataset_id": ds["id"], "image_ids": [img["id"]],
                              "lut_path": str(tmp_path / "fake.cube"), "replace": False}),
                ("/upscaling/run", {"dataset_id": ds["id"], "image_ids": [img["id"]],
                                    "model_path": str(tmp_path / "fake.pth"), "replace": False}),
            ):
                before = {i["id"] for i in
                          (await env.client.get(f"{API}/images/", params={"dataset_id": ds["id"]})).json()}
                r = await env.client.post(f"{API}{path}", json=body)
                assert r.status_code == 200, (path, r.text)
                job = await wait_for_job(env, r.json()["job_id"])
                assert job["status"] == "completed", (path, job)

                images = (await env.client.get(f"{API}/images/", params={"dataset_id": ds["id"]})).json()
                new = [i for i in images if i["id"] not in before]
                assert len(new) == 1, (path, images)
                derived = (await env.client.get(f"{API}/images/{new[0]['id']}")).json()
                assert derived["license"] == "CC-BY-SA-4.0", path
                assert derived["source_name"] == "Flickr", path
                # The deferred column specifically: this is the one that raises.
                assert derived["provenance"]["source_meta"] == {"post_id": 7}, path

    run(scenario())


def test_detection_crop_copies_parent_provenance(tmp_path):
    """The detection-crop worker's own `undefer` + `copy_provenance`, live."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("dets")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(64, 64)))
            async with env.Session() as db:
                row = (await db.execute(select(Image).where(Image.id == img["id"]))).scalar_one()
                row.license, row.source_name, row.source_meta = "CC-BY-NC-4.0", "Danbooru", {"post_id": 3}
                await db.commit()

            r = await env.client.post(f"{API}/detection/manual", json={
                "image_id": img["id"], "label": "face", "bbox": [0.1, 0.1, 0.5, 0.5],
            })
            assert r.status_code in (200, 201), r.text

            r = await env.client.post(f"{API}/detection/crop", json={
                "dataset_id": ds["id"], "image_ids": [img["id"]], "labels": ["face"],
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

            images = (await env.client.get(f"{API}/images/", params={"dataset_id": ds["id"]})).json()
            crops = [i for i in images if i["id"] != img["id"]]
            assert len(crops) == 1, images
            crop = (await env.client.get(f"{API}/images/{crops[0]['id']}")).json()
            assert crop["license"] == "CC-BY-NC-4.0"
            assert crop["source_name"] == "Danbooru"
            assert crop["provenance"]["source_meta"] == {"post_id": 3}

    run(scenario())


# --- capture paths -------------------------------------------------------


def test_upload_captures_png_text_attribution(tmp_path):
    """PNGs carry no EXIF Artist tag — reading only EXIF captured nothing at all."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(
                env, ds["id"], "credited.png",
                png_bytes(Author="Jane Doe", Copyright="© 2026 Jane Doe"),
            )
            detail = (await env.client.get(f"{API}/images/{img['id']}")).json()
            assert "Jane Doe" in (detail["attribution"] or "")

    run(scenario())


def test_import_does_not_adopt_an_unrelated_json_file(tmp_path):
    """`workflow.json` beside `workflow.png` is not a provenance sidecar."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            src = tmp_path / "src"
            src.mkdir()
            (src / "workflow.png").write_bytes(png_bytes())
            (src / "workflow.json").write_text(
                json.dumps({"nodes": [{"type": "KSampler"}], "links": []}), encoding="utf-8")

            r = await env.client.post(
                f"{API}/datasets/{ds['id']}/import",
                json={"folder_path": str(src), "import_captions": False},
            )
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

            images = (await env.client.get(f"{API}/images/", params={"dataset_id": ds["id"]})).json()
            detail = (await env.client.get(f"{API}/images/{images[0]['id']}")).json()
            assert detail["provenance"]["source_meta"] is None
            assert detail["source_name"] is None

    run(scenario())


def test_patch_provenance_serializes_every_deferred_column(tmp_path):
    """Replaces a test that applied `undefer` itself and never called the endpoint.

    `ImageOut` reads `has_dino_layer_embeddings` and `source_meta`, both backed by
    deferred columns. Serializing an instance whose deferred column was never
    loaded raises MissingGreenlet on an async session — a live-only 500 that a
    test doing its own undefer can never reproduce, because it has already fixed
    the thing under test.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"])

            async with env.Session() as db:
                row = (await db.execute(select(Image).where(Image.id == img["id"]))).scalar_one()
                row.source_meta = {"post_id": 7}
                await db.commit()

            r = await env.client.patch(
                f"{API}/images/{img['id']}/provenance", json={"license": "CC0-1.0"})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["license"] == "CC0-1.0"
            assert body["provenance"]["source_meta"] == {"post_id": 7}
            assert body["has_dino_layer_embeddings"] is False

            # GET must serialize it the same way.
            got = (await env.client.get(f"{API}/images/{img['id']}")).json()
            assert got["provenance"]["source_meta"] == {"post_id": 7}

    run(scenario())


def test_snapshot_and_restore_round_trip_through_the_router(tmp_path):
    """create_snapshot reads the deferred source_meta; restore writes all five columns."""
    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.patch(f"{API}/settings/thresholds", json={"versioning_mode": "manual"})
            assert r.status_code == 200, r.text

            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"])
            async with env.Session() as db:
                row = (await db.execute(select(Image).where(Image.id == img["id"]))).scalar_one()
                row.license, row.source_name, row.source_meta = "CC-BY-SA-4.0", "Flickr", {"post_id": 7}
                await db.commit()

            # Large datasets snapshot as a background job; this one is inline.
            r = await env.client.post(f"{API}/datasets/{ds['id']}/versions",
                                      json={"name": "s1", "description": ""})
            assert r.status_code in (200, 201), r.text
            if "job_id" in r.json():
                await wait_for_job(env, r.json()["job_id"])
                versions = (await env.client.get(f"{API}/datasets/{ds['id']}/versions")).json()
                snapshot_id = versions[0]["id"]
            else:
                snapshot_id = r.json()["id"]

            await env.client.patch(f"{API}/images/{img['id']}/provenance",
                                   json={"license": "owned", "source_name": "Me"})

            r = await env.client.post(
                f"{API}/datasets/{ds['id']}/versions/{snapshot_id}/restore",
                json={"pre_restore_snapshot": False},
            )
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

            got = (await env.client.get(f"{API}/images/{img['id']}")).json()
            assert got["license"] == "CC-BY-SA-4.0"
            assert got["source_name"] == "Flickr"
            assert got["provenance"]["source_meta"] == {"post_id": 7}

    run(scenario())


def test_version_diff_reports_every_provenance_field(tmp_path):
    """`diff_versions` had zero coverage, and its field lists are hand-synced.

    `_DIFF_COLS` selects the columns and a separate tuple decides which are
    compared; a field present in one and missing from the other silently reports
    "unchanged" for a value that did change. That drift already happened once on
    this branch, so this pins all five provenance fields end to end.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            from backend.services import version_service

            r = await env.client.patch(f"{API}/settings/thresholds", json={"versioning_mode": "manual"})
            assert r.status_code == 200, r.text

            ds = await env.create_dataset("diffme")
            img = await upload_image(env, ds["id"])

            async def snapshot(name: str) -> str:
                r = await env.client.post(f"{API}/datasets/{ds['id']}/versions",
                                          json={"name": name, "description": ""})
                assert r.status_code in (200, 201), r.text
                if "job_id" in r.json():
                    await wait_for_job(env, r.json()["job_id"])
                    versions = (await env.client.get(f"{API}/datasets/{ds['id']}/versions")).json()
                    return next(v["id"] for v in versions if v["name"] == name)
                return r.json()["id"]

            async with env.Session() as db:
                row = (await db.execute(select(Image).where(Image.id == img["id"]))).scalar_one()
                row.source_name, row.source_url = "Flickr", "https://flickr.test/p/1"
                row.license, row.attribution = "CC-BY-4.0", "Photo by Jane Doe"
                row.source_meta = {"post_id": 1}
                await db.commit()
            before = await snapshot("v1")

            await env.client.patch(f"{API}/images/{img['id']}/provenance", json={
                "source_name": "Me", "source_url": "https://mine.test/x",
                "license": "owned", "attribution": "Me",
            })
            async with env.Session() as db:
                row = (await db.execute(select(Image).where(Image.id == img["id"]))).scalar_one()
                row.source_meta = {"post_id": 2}
                await db.commit()
            after = await snapshot("v2")

            r = await env.client.get(f"{API}/datasets/{ds['id']}/versions/diff",
                                     params={"v1": before, "v2": after})
            assert r.status_code == 200, r.text
            changes = r.json()["modified"][0]["changes"]
            assert changes["source_name"] == {"from": "Flickr", "to": "Me"}
            assert changes["source_url"] == {"from": "https://flickr.test/p/1", "to": "https://mine.test/x"}
            assert changes["license"] == {"from": "CC-BY-4.0", "to": "owned"}
            assert changes["attribution"] == {"from": "Photo by Jane Doe", "to": "Me"}
            # A heavy JSON column — diffed, but reported as a marker.
            assert changes["source_meta"] == {"changed": True}

            # Structural: everything the comparison loop reads must be selected.
            selected = {c.key for c in version_service._DIFF_COLS}
            compared = version_service._DIFF_COMPARE_FIELDS
            assert set(compared) <= selected, sorted(set(compared) - selected)

    run(scenario())


def test_patch_dataset_provenance_defaults_on_both_branches(tmp_path):
    """`PATCH /datasets/{id}` was never exercised — only POST.

    `update_dataset` applies the defaults through two separate paths (the rename
    branch commits first, then re-applies; the normal branch does not), and a
    dataset default is what every non-overriding image inherits, so a field
    silently dropped on one branch changes the license of a whole dataset.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d1")
            img = await upload_image(env, ds["id"])

            async def prov():
                return (await env.client.get(f"{API}/images/{img['id']}")).json()["provenance"]

            # Normal branch: set all four.
            r = await env.client.patch(f"{API}/datasets/{ds['id']}", json={
                "source_name": "Flickr", "source_url": "https://flickr.test/p/1",
                "license": "cc-by-4.0", "attribution": "Photo by Jane Doe",
            })
            assert r.status_code == 200, r.text
            assert r.json()["license"] == "CC-BY-4.0"     # normalized by the schema
            assert await prov() == {
                "source_name": "Flickr", "source_url": "https://flickr.test/p/1",
                "license": "CC-BY-4.0", "attribution": "Photo by Jane Doe",
                "source_meta": None,
                "inherited": ["source_name", "source_url", "license", "attribution"],
            }

            # None leaves a field alone; "" clears it. The two are not the same.
            r = await env.client.patch(f"{API}/datasets/{ds['id']}", json={"attribution": ""})
            assert r.status_code == 200, r.text
            after = await prov()
            assert after["attribution"] == ""            # cleared
            assert after["license"] == "CC-BY-4.0"       # untouched by omission

            # Rename branch: the rename commits first, so the provenance write has
            # to survive it — in the same request.
            r = await env.client.patch(f"{API}/datasets/{ds['id']}", json={
                "name": "d2", "license": "owned", "source_name": "Me",
            })
            assert r.status_code == 200, r.text
            assert r.json()["name"] == "d2"
            after = await prov()
            assert after["license"] == "owned"
            assert after["source_name"] == "Me"
            assert after["source_url"] == "https://flickr.test/p/1"   # not clobbered

    run(scenario())


def test_stats_license_breakdown_matches_the_gallery_filter(tmp_path):
    """The breakdown resolves inheritance, and its buckets are bounded.

    Nothing tested this at any level. It is the only aggregate that runs the
    COALESCE in SQL rather than through `resolve_provenance`, so it is the one
    place where the two can disagree about what an image's license is.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            from backend.services.dataset_service import (
                LICENSE_BREAKDOWN_LIMIT,
                LICENSE_BREAKDOWN_OTHER_KEY,
            )

            ds = await env.create_dataset("stats", license="CC-BY-NC-4.0")
            for n in range(3):
                await upload_image(env, ds["id"], f"inh{n}.png", png_bytes((n * 30, 1, 2)))
            owned = await upload_image(env, ds["id"], "owned.png", png_bytes((9, 9, 9)))
            await env.client.patch(
                f"{API}/images/{owned['id']}/provenance", json={"license": "owned"})

            stats = (await env.client.get(f"{API}/datasets/{ds['id']}/stats")).json()
            breakdown = stats["license_breakdown"]
            # NULL license buckets under the dataset default, not under "".
            assert breakdown == {"CC-BY-NC-4.0": 3, "owned": 1}
            assert sum(breakdown.values()) == stats["image_count"]

            # …and each bucket count is what the gallery filter returns for that id.
            for lic, count in breakdown.items():
                r = await env.client.get(f"{API}/images/", params={
                    "dataset_id": ds["id"], "license_filter": json.dumps([lic])})
                assert r.status_code == 200, r.text
                assert len(r.json()) == count, lic

            # Unbounded `other:` free text is one bucket per distinct value, so the
            # tail collapses instead of growing the response without limit.
            for n in range(LICENSE_BREAKDOWN_LIMIT + 5):
                extra = await upload_image(env, ds["id"], f"o{n}.png", png_bytes((n, 200, 7)))
                await env.client.patch(f"{API}/images/{extra['id']}/provenance",
                                       json={"license": f"other:terms {n}"})

            stats = (await env.client.get(f"{API}/datasets/{ds['id']}/stats")).json()
            breakdown = stats["license_breakdown"]
            assert len(breakdown) == LICENSE_BREAKDOWN_LIMIT + 1
            assert LICENSE_BREAKDOWN_OTHER_KEY in breakdown
            # The collapsed bucket keeps the totals honest.
            assert sum(breakdown.values()) == stats["image_count"]

    run(scenario())


def test_licenses_in_use_offers_every_value_the_filter_accepts(tmp_path):
    """The pickers' free-text options: what exists, and only what filters cleanly.

    A free-text license is data, not vocabulary, so a dropdown can only offer one
    by asking. The contract that matters is that nothing it offers is a value the
    gallery filter would reject or resolve differently — that mismatch is exactly
    how a picker sends users to an empty gallery.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("picker", license="other:Studio EULA")

            # A dataset default no image carries yet is still offered — otherwise
            # the license just typed into the defaults is unpickable everywhere.
            assert (await env.client.get(
                f"{API}/datasets/{ds['id']}/licenses-in-use")).json() == [
                    {"license": "other:Studio EULA", "count": 0}]

            for n in range(2):
                await upload_image(env, ds["id"], f"inh{n}.png", png_bytes((n * 40, 3, 4)))
            override = await upload_image(env, ds["id"], "own.png", png_bytes((7, 7, 7)))
            await env.client.patch(f"{API}/images/{override['id']}/provenance",
                                   json={"license": "other:Client X, revision 2"})

            rows = (await env.client.get(f"{API}/datasets/{ds['id']}/licenses-in-use")).json()
            # Inherited default first (2 images), then the single override.
            assert rows == [
                {"license": "other:Studio EULA", "count": 2},
                {"license": "other:Client X, revision 2", "count": 1},
            ]

            # Every offered value round-trips through the gallery filter with the
            # count it advertised. Note the comma inside the second one: this is
            # why license_filter is a JSON array and never comma-separated.
            for row in rows:
                r = await env.client.get(f"{API}/images/", params={
                    "dataset_id": ds["id"], "license_filter": json.dumps([row["license"]])})
                assert r.status_code == 200, r.text
                assert len(r.json()) == row["count"], row["license"]

            # An unlicensed dataset reports the "" bucket (the gallery expresses it
            # through license_missing, and renders its own option for it).
            bare = await env.create_dataset("bare")
            await upload_image(env, bare["id"], "b.png", png_bytes((2, 2, 2)))
            assert (await env.client.get(
                f"{API}/datasets/{bare['id']}/licenses-in-use")).json() == [
                    {"license": "", "count": 1}]

            # ...and that "" is the one row a consumer may NOT forward: this
            # endpoint reports it, `GET /images/` rejects it. Dropping it is
            # useCustomLicenses's job, so anything reading the raw response
            # instead of the hook 400s here rather than filtering.
            assert (await env.client.get(f"{API}/images/", params={
                "dataset_id": bare["id"], "license_filter": json.dumps([""]),
            })).status_code == 400

            assert (await env.client.get(
                f"{API}/datasets/nope/licenses-in-use")).status_code == 404

    run(scenario())


# --- export: filters, manifest placement, cancellation -------------------


async def _export_env(env, tmp_path):
    """A dataset defaulting to CC-BY-NC-4.0 with one owned override + one unlicensed."""
    ds = await env.create_dataset("exp", license="CC-BY-NC-4.0", source_name="Flickr")
    inherits = await upload_image(env, ds["id"], "inherits.png")
    owned = await upload_image(env, ds["id"], "owned.png", png_bytes((1, 2, 3)))
    await env.client.patch(f"{API}/images/{owned['id']}/provenance", json={"license": "owned"})

    unlicensed_ds = await env.create_dataset("unl")
    unlicensed = await upload_image(env, unlicensed_ds["id"], "unl.png", png_bytes((4, 5, 6)))
    return ds, unlicensed_ds, inherits, owned, unlicensed


def test_export_license_filters_run_on_the_effective_license(tmp_path):
    """Every license filter resolves image-over-dataset, so a NULL row is not "unlicensed"."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, unl_ds, inherits, owned, unlicensed = await _export_env(env, tmp_path)

            async def will_export(dataset_id, **params):
                r = await env.client.get(f"{API}/export/preview/{dataset_id}", params=params)
                assert r.status_code == 200, r.text
                return r.json()["will_export"]

            # commercial_only: CC-BY-NC is not commercial, "owned" is.
            assert await will_export(ds["id"], commercial_only=True) == 1

            # license_filter is a JSON array (an `other:` id may contain commas).
            assert await will_export(ds["id"], license_filter=json.dumps(["CC-BY-NC-4.0"])) == 1
            assert await will_export(ds["id"], license_filter=json.dumps(["owned"])) == 1
            assert await will_export(ds["id"], license_filter=json.dumps([])) == 2   # empty = no filter

            # exclude_unlicensed keeps everything here: both resolve to a license.
            assert await will_export(ds["id"], exclude_unlicensed=True) == 2
            # ...and drops the dataset that records nothing anywhere.
            assert await will_export(unl_ds["id"], exclude_unlicensed=True) == 0
            assert await will_export(unl_ds["id"]) == 1

    run(scenario())


def test_gallery_license_filter_runs_on_the_effective_license(tmp_path):
    """`GET /images/` joins Dataset and coalesces — the branch's only new SQL join.

    An image with a NULL license carries its dataset's default, so both the badge
    value and every filter have to resolve inheritance rather than testing
    `Image.license` alone.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("gal", license="CC-BY-NC-4.0")
            inherits = await upload_image(env, ds["id"], "inherits.png")
            owned = await upload_image(env, ds["id"], "owned.png", png_bytes((1, 2, 3)))
            custom = await upload_image(env, ds["id"], "custom.png", png_bytes((4, 5, 6)))
            await env.client.patch(f"{API}/images/{owned['id']}/provenance", json={"license": "owned"})
            # A free-text id containing a comma — the reason the param is a JSON
            # array and never a comma-separated string.
            free_text = "other:Internal use, no redistribution"
            await env.client.patch(
                f"{API}/images/{custom['id']}/provenance", json={"license": free_text})

            async def names(**params):
                r = await env.client.get(f"{API}/images/", params={"dataset_id": ds["id"], **params})
                assert r.status_code == 200, r.text
                return {i["filename"] for i in r.json()}

            # The badge value is the *effective* license, inherited included.
            listing = (await env.client.get(f"{API}/images/", params={"dataset_id": ds["id"]})).json()
            by_name = {i["filename"]: i for i in listing}
            assert by_name["inherits.png"]["license"] == "CC-BY-NC-4.0"   # inherited
            assert by_name["owned.png"]["license"] == "owned"             # own value

            # The filter matches on an inherited value too.
            assert await names(license_filter=json.dumps(["CC-BY-NC-4.0"])) == {"inherits.png"}
            assert await names(license_filter=json.dumps(["owned"])) == {"owned.png"}
            # ...and an `other:` id survives the JSON-array round trip with its comma.
            assert await names(license_filter=json.dumps([free_text])) == {"custom.png"}
            assert await names(license_filter=json.dumps(["owned", free_text])) == {
                "owned.png", "custom.png"}
            # Empty list means "no filter", never "match nothing".
            assert len(await names(license_filter=json.dumps([]))) == 3

            # license_missing, both directions. Everything here resolves to a
            # license, so "missing only" is empty.
            assert await names(license_missing=True) == set()
            assert len(await names(license_missing=False)) == 3

            unl_ds = await env.create_dataset("unl")
            await upload_image(env, unl_ds["id"], "bare.png", png_bytes((7, 8, 9)))
            r = await env.client.get(
                f"{API}/images/", params={"dataset_id": unl_ds["id"], "license_missing": True})
            assert {i["filename"] for i in r.json()} == {"bare.png"}
            assert inherits["filename"] == "inherits.png"

    run(scenario())


def test_gallery_license_filter_rejects_malformed_and_all_blank_lists(tmp_path):
    """Two ways a client can silently get *every* image back instead of a filter."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("gal")
            await upload_image(env, ds["id"], "a.png")

            async def status(value):
                r = await env.client.get(
                    f"{API}/images/", params={"dataset_id": ds["id"], "license_filter": value})
                return r.status_code

            assert await status("CC0-1.0,owned") == 400        # not JSON at all
            assert await status(json.dumps({"a": 1})) == 400   # JSON, but not a list
            assert await status(json.dumps([1, 2])) == 400     # list, but not of strings
            # `""` is a meaningful entry for the *export* filters ("no license
            # recorded"), so a client can reasonably send it here — applying no
            # filter and returning everything would be a silent lie.
            assert await status(json.dumps([""])) == 400
            assert await status(json.dumps(["  ", ""])) == 400
            # A *mixed* list too: dropping the blank silently narrows the filter
            # to the non-blank ids and returns fewer images than were asked for.
            assert await status(json.dumps(["CC0-1.0", ""])) == 400
            assert await status(json.dumps(["", "owned"])) == 400
            # …and a well-formed list still works.
            assert await status(json.dumps(["CC0-1.0", "owned"])) == 200

    run(scenario())


def test_kohya_export_puts_manifests_beside_the_image_folder(tmp_path):
    """Manifests live in the export root; images live one level down, so the
    manifest `file` column has to be relative to the root, not a basename."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, *_ = await _export_env(env, tmp_path)
            out = tmp_path / "kohya"

            r = await env.client.post(f"{API}/export/kohya", json={
                "dataset_id": ds["id"], "output_dir": str(out),
                "n_repeats": 10, "concept_token": "concept",
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

            assert (out / "CREDITS.md").exists()
            rows = list(csv.DictReader((out / "licenses.csv").open(encoding="utf-8")))
            assert {r["file"] for r in rows} == {"10_concept/inherits.png", "10_concept/owned.png"}
            by_file = {r["file"]: r for r in rows}
            assert by_file["10_concept/inherits.png"]["license"] == "CC-BY-NC-4.0"  # inherited
            assert by_file["10_concept/owned.png"]["license"] == "owned"            # own value

    run(scenario())


def test_a_cancelled_export_leaves_the_canonical_manifest_to_the_real_run(tmp_path, monkeypatch):
    """Cancel mid-run, then re-export to completion into the same directory.

    The cancelled run must ship its own partial manifest — an unattributed pile of
    files is exactly what this feature exists to prevent — but it must not claim
    `CREDITS.md`, because `_manifest_dest` never overwrites, so the later complete
    manifest would be stranded on `CREDITS.2.md` forever.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            from backend.services import export_service
            from backend.workers.job_queue import job_queue

            ds = await env.create_dataset("cancelme", license="CC-BY-4.0")
            for n in range(4):
                await upload_image(env, ds["id"], f"{n}.png", png_bytes((n * 20, 30, 40)))

            out = tmp_path / "out"
            real_write_image = export_service._write_image
            written: list[str] = []

            def _cancel_after_first(src, dest_img, *a, **kw):
                # Cancel from inside the loop so the check at the top of the next
                # iteration fires — deterministic, unlike racing a DELETE.
                written.append(dest_img.name)
                if len(written) == 1 and job_queue._current_job_id:
                    job_queue.request_cancel(job_queue._current_job_id)
                return real_write_image(src, dest_img, *a, **kw)

            monkeypatch.setattr(export_service, "_write_image", _cancel_after_first)

            r = await env.client.post(f"{API}/export/plain", json={
                "dataset_id": ds["id"], "output_dir": str(out),
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "cancelled", job

            assert not (out / "CREDITS.md").exists()
            partial = (out / "CREDITS.partial.md").read_text(encoding="utf-8")
            assert "did not finish" in partial
            assert "CC BY 4.0" in partial
            partial_rows = list(csv.DictReader((out / "licenses.partial.csv").open(encoding="utf-8")))
            # Only what actually reached disk before the stop.
            assert len(partial_rows) == 1

            # Now the same export, uninterrupted, into the same directory.
            monkeypatch.setattr(export_service, "_write_image", real_write_image)
            r = await env.client.post(f"{API}/export/plain", json={
                "dataset_id": ds["id"], "output_dir": str(out),
            })
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

            complete = (out / "CREDITS.md").read_text(encoding="utf-8")
            assert "did not finish" not in complete
            rows = list(csv.DictReader((out / "licenses.csv").open(encoding="utf-8")))
            assert len(rows) == 4
            # The partial one is still there, still clearly labelled as partial.
            assert (out / "CREDITS.partial.md").exists()

    run(scenario())


def test_a_failed_export_still_ships_a_partial_manifest(tmp_path, monkeypatch):
    """A *failed* export must ship its manifest too, not only a cancelled one.

    The loop wrote the partial manifest from `except asyncio.CancelledError`
    alone, so every other way an export can stop — a truncated image, ENOSPC,
    EACCES — left the files it had already written on disk with no CREDITS.md at
    all: the unattributed pile this whole feature exists to prevent.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            from backend.services import export_service

            ds = await env.create_dataset(
                "failme", license="CC-BY-4.0", attribution="Photo by Jane Doe")
            for n in range(4):
                await upload_image(env, ds["id"], f"{n}.png", png_bytes((n * 20, 30, 40)))

            out = tmp_path / "out"
            real_write_image = export_service._write_image
            written: list[str] = []

            def _fail_on_third(src, dest_img, *a, **kw):
                written.append(dest_img.name)
                if len(written) == 3:
                    raise OSError(28, "No space left on device")
                return real_write_image(src, dest_img, *a, **kw)

            monkeypatch.setattr(export_service, "_write_image", _fail_on_third)

            r = await env.client.post(f"{API}/export/plain", json={
                "dataset_id": ds["id"], "output_dir": str(out),
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "failed", job

            # The failure keeps the canonical names free for a later good run.
            assert not (out / "CREDITS.md").exists()
            partial = (out / "CREDITS.partial.md").read_text(encoding="utf-8")
            assert "did not finish" in partial
            assert "Photo by Jane Doe" in partial

            rows = list(csv.DictReader((out / "licenses.partial.csv").open(encoding="utf-8")))
            # Exactly the files that reached disk before the failure — the third
            # image raised before it was written, so it is not claimed here.
            assert len(rows) == 2, rows
            for row in rows:
                assert (out / row["file"]).exists(), row["file"]
                assert row["license"] == "CC-BY-4.0"      # inherited from the dataset

    run(scenario())


def test_comfy_row_cleanup_survives_a_db_error(tmp_path, monkeypatch):
    """A DB failure mid-row must still run the per-row cleanup.

    The `except Exception` handler did its DB work *before* its file work and
    without a `rollback()`. After a failed `flush()` the session is unusable, so
    the one error class the handler's own comment claims to cover made the
    handler itself raise `PendingRollbackError`: files orphaned on disk, the row
    committed as "running", and the run aborted with the later rows never tried.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            import backend.routers.comfy as comfy_router
            monkeypatch.setattr(comfy_router, "ComfyClient", _FakeComfyClient)
            await env.client.patch(f"{API}/settings/thresholds", json={"comfyui_url": "http://fake:8188"})

            ds = await env.create_dataset("comfy-db-error")
            r = await env.client.post(f"{API}/comfy/plans", json={
                "dataset_id": ds["id"], "name": "plan",
                "workflow_json": {"9": {"class_type": "SaveImage", "inputs": {}}},
                "output_node_ids": ["9"],
            })
            assert r.status_code == 200, r.text
            plan_id = r.json()["id"]
            for _ in range(3):
                await env.client.post(f"{API}/comfy/plans/{plan_id}/rows", json={"values": {}})

            # Fail the *second* row inside `session.flush()`. A value the JSON
            # serializer cannot encode fails at the same point a colliding
            # filename's IntegrityError would, and leaves the session in the same
            # state: every further statement raises until someone rolls back.
            calls = {"n": 0}
            real_source_meta = comfy_router._comfy_source_meta

            def _poison_second_row(plan, row, workflow):
                calls["n"] += 1
                if calls["n"] == 2:
                    return {"generator": object()}
                return real_source_meta(plan, row, workflow)

            monkeypatch.setattr(comfy_router, "_comfy_source_meta", _poison_second_row)

            r = await env.client.post(f"{API}/comfy/run", json={"plan_id": plan_id})
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            # The run survives the bad row and still finishes the ones after it.
            assert job["status"] == "completed", job
            assert job["result_data"]["failed"] == 1
            assert job["result_data"]["completed"] == 2

            rows = (await env.client.get(f"{API}/comfy/plans/{plan_id}/rows")).json()
            assert sorted(row["status"] for row in rows) == ["completed", "completed", "failed"]
            failed = next(row for row in rows if row["status"] == "failed")
            assert failed["error_msg"]                    # not wedged at "running"
            assert failed["image_id"] is None
            assert failed["image_ids"] == []

            # Two good images, and the failed row left nothing behind on disk.
            images = (await env.client.get(f"{API}/images/", params={"dataset_id": ds["id"]})).json()
            assert len(images) == 2
            ds_dir = next(p for p in env.datasets_dir.iterdir() if p.is_dir())
            assert len(list((ds_dir / "images").glob("*.png"))) == 2
            assert len(list((ds_dir / "thumbnails").glob("*.webp"))) == 2

    run(scenario())


def test_re_export_into_the_same_directory_supersedes_its_manifest(tmp_path):
    """A second full export of the same subtree must own `CREDITS.md`.

    `_manifest_dest` never overwrote, so re-exporting after adding an image left
    `CREDITS.md` describing the old, smaller set and stranded the current one on
    `CREDITS.2.md` — the wrong file is the one a redistributor opens. Superseding
    is safe only when every file the old manifest lists is under this run's
    output directory, which is what distinguishes it from kohya's `10_x/` beside
    `20_x/` case below.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("re-export", license="CC-BY-4.0")
            await upload_image(env, ds["id"], "one.png")
            out = tmp_path / "out"

            async def export(**extra):
                r = await env.client.post(f"{API}/export/kohya", json={
                    "dataset_id": ds["id"], "output_dir": str(out),
                    "n_repeats": 10, "concept_token": "concept", **extra,
                })
                assert r.status_code == 200, r.text
                job = await wait_for_job(env, r.json()["job_id"])
                assert job["status"] == "completed", job
                return job

            job = await export()
            assert job["result_data"]["manifest_files"] == ["CREDITS.md", "licenses.csv"]

            await upload_image(env, ds["id"], "two.png", png_bytes((5, 6, 7)))
            job = await export()
            # Same names again — overwritten in place, not chained.
            assert job["result_data"]["manifest_files"] == ["CREDITS.md", "licenses.csv"]
            assert not (out / "CREDITS.2.md").exists()
            rows = list(csv.DictReader((out / "licenses.csv").open(encoding="utf-8")))
            assert {r["file"] for r in rows} == {"10_concept/one.png", "10_concept/two.png"}

            # A *different* subtree in the same directory is an addition, not a
            # replacement, so it must not destroy the manifest describing 10_concept.
            job = await export(n_repeats=20)
            # licenses.csv chains because its `file` column differs. CREDITS.md is
            # byte-identical (it groups by license/source and names no files), so
            # the existing one already describes this run and is left alone.
            assert job["result_data"]["manifest_files"] == ["licenses.2.csv"]
            rows = list(csv.DictReader((out / "licenses.csv").open(encoding="utf-8")))
            assert {r["file"] for r in rows} == {"10_concept/one.png", "10_concept/two.png"}

    run(scenario())


def test_manifest_file_column_is_not_formula_guarded(tmp_path):
    """The `file` column is a path this code generated, not scraped input.

    Prefixing it with `'` corrupted every filename starting with `-`, `=` or `@`,
    so the manifest named a file that does not exist. The four provenance columns
    keep the guard — those are the untrusted ones.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("csv")
            img = await upload_image(env, ds["id"], "a.png")
            # Ingest slugifies, so force the name the writer has to handle.
            async with env.Session() as db:
                row = (await db.execute(select(Image).where(Image.id == img["id"]))).scalar_one()
                old = Path(row.file_path)
                new = old.with_name("-leading-dash.png")
                old.rename(new)
                row.filename, row.file_path = new.name, str(new)
                await db.commit()
            await env.client.patch(f"{API}/images/{img['id']}/provenance", json={
                "attribution": "=HYPERLINK(\"http://evil.test\")",
            })
            out = tmp_path / "out"
            r = await env.client.post(f"{API}/export/plain", json={
                "dataset_id": ds["id"], "output_dir": str(out),
            })
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

            row = next(iter(csv.DictReader((out / "licenses.csv").open(encoding="utf-8"))))
            assert row["file"] == "images/-leading-dash.png"    # no `'` prefix
            assert (out / row["file"]).exists()
            assert row["attribution"].startswith("'=")           # still guarded

    run(scenario())


def test_exclude_no_derivatives_drops_only_known_nd_licenses(tmp_path):
    """An export ships resized/cropped copies, which is what ND forbids.

    Unlike `commercial_only` this is *not* conservative about unknowns: only a
    license known to be ND is dropped, so an `other:` free-text license — which
    may well permit derivatives — is not silently excluded.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("nd")
            nd = await upload_image(env, ds["id"], "nd.png")
            free = await upload_image(env, ds["id"], "free.png", png_bytes((1, 2, 3)))
            other = await upload_image(env, ds["id"], "other.png", png_bytes((4, 5, 6)))
            await env.client.patch(f"{API}/images/{nd['id']}/provenance",
                                   json={"license": "CC-BY-ND-4.0"})
            await env.client.patch(f"{API}/images/{free['id']}/provenance",
                                   json={"license": "CC-BY-4.0"})
            await env.client.patch(f"{API}/images/{other['id']}/provenance",
                                   json={"license": "other:ask first"})

            r = await env.client.get(f"{API}/export/preview/{ds['id']}",
                                     params={"exclude_no_derivatives": True})
            assert r.status_code == 200, r.text
            assert r.json()["will_export"] == 2
            assert r.json()["excluded_license"] == 1

            out = tmp_path / "out"
            r = await env.client.post(f"{API}/export/plain", json={
                "dataset_id": ds["id"], "output_dir": str(out),
                "exclude_no_derivatives": True,
            })
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job
            rows = list(csv.DictReader((out / "licenses.csv").open(encoding="utf-8")))
            assert {r["file"] for r in rows} == {"images/free.png", "images/other.png"}

            # And the manifest states the obligations rather than leaving a
            # redistributor to look the license up.
            r = await env.client.post(f"{API}/export/plain", json={
                "dataset_id": ds["id"], "output_dir": str(tmp_path / "all"),
            })
            await wait_for_job(env, r.json()["job_id"])
            credits = (tmp_path / "all" / "CREDITS.md").read_text(encoding="utf-8")
            assert "CC BY-ND 4.0 — attribution required, no derivatives" in credits
            # A free-text license cannot pass itself off as a vocabulary entry.
            assert "ask first (unrecognised license, recorded as free text)" in credits

    run(scenario())


def test_freetext_will_export_counts_what_the_nd_filter_waved_through(tmp_path):
    """The preview's counterpart to the asymmetry above.

    `commercial_only` drops a license it cannot classify; `exclude_no_derivatives`
    keeps it. So a free-text "CC BY-ND (custom)" ships from a run that ticked
    "exclude no-derivatives", and nothing said so. This counter is what the Export
    page warns from.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("freetext")
            nd = await upload_image(env, ds["id"], "nd.png")
            known = await upload_image(env, ds["id"], "known.png", png_bytes((1, 2, 3)))
            other = await upload_image(env, ds["id"], "other.png", png_bytes((4, 5, 6)))
            await upload_image(env, ds["id"], "bare.png", png_bytes((7, 8, 9)))
            await env.client.patch(f"{API}/images/{nd['id']}/provenance",
                                   json={"license": "CC-BY-ND-4.0"})
            await env.client.patch(f"{API}/images/{known['id']}/provenance",
                                   json={"license": "CC-BY-4.0"})
            await env.client.patch(f"{API}/images/{other['id']}/provenance",
                                   json={"license": "other:CC BY-ND (custom)"})
            # Only the free-text image is captioned, so a non-license filter can
            # be used below to drop exactly it.
            r = await env.client.put(f"{API}/captions/image/{other['id']}",
                                     json={"caption_text": "a cat"})
            assert r.status_code == 200, r.text

            async def preview(**params):
                r = await env.client.get(f"{API}/export/preview/{ds['id']}", params=params)
                assert r.status_code == 200, r.text
                return r.json()

            # The ND filter drops the vocabulary ND image and keeps the free-text
            # one that says the same thing in prose.
            p = await preview(exclude_no_derivatives=True)
            assert p["will_export"] == 3 and p["excluded_license"] == 1
            assert p["freetext_will_export"] == 1
            # An unlicensed image is not free text; the two counters are disjoint.
            assert p["unlicensed_will_export"] == 1

            # The counter is unconditional — the client decides when to surface it.
            assert (await preview())["freetext_will_export"] == 1

            # ...and it accounts for every filter, not just the license ones.
            p = await preview(exclude_no_derivatives=True, captioned_only=True)
            assert p["will_export"] == 1 and p["freetext_will_export"] == 1
            p = await preview(exclude_no_derivatives=True, license_filter='["CC-BY-4.0"]')
            assert p["freetext_will_export"] == 0

    run(scenario())


def test_unlicensed_will_export_accounts_for_every_filter(tmp_path):
    """Not just the license ones.

    `unlicensed_count` is whole-dataset scope on purpose, so the client used to
    guess whether those images ship from the three license flags — and claimed
    "they still export" whenever a caption or aesthetic filter had already
    dropped them.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("mix")
            await upload_image(env, ds["id"], "bare.png")
            captioned = await upload_image(env, ds["id"], "cap.png", png_bytes((1, 2, 3)))
            r = await env.client.put(f"{API}/captions/image/{captioned['id']}",
                                     json={"caption_text": "a cat"})
            assert r.status_code == 200, r.text

            async def preview(**params):
                r = await env.client.get(f"{API}/export/preview/{ds['id']}", params=params)
                assert r.status_code == 200, r.text
                return r.json()

            p = await preview()
            assert p["unlicensed_count"] == 2 and p["unlicensed_will_export"] == 2

            # A non-license filter drops the unlicensed image; the counter follows.
            p = await preview(captioned_only=True)
            assert p["unlicensed_count"] == 2          # still whole-dataset scope
            assert p["unlicensed_will_export"] == 1
            assert p["will_export"] == 1

            p = await preview(exclude_unlicensed=True)
            assert p["unlicensed_will_export"] == 0

    run(scenario())


def test_captions_only_export_lists_the_files_it_actually_wrote(tmp_path):
    """A captions-only run writes no images, so listing `.png` names was a lie."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, *_ = await _export_env(env, tmp_path)
            out = tmp_path / "captions"

            r = await env.client.post(f"{API}/export/kohya", json={
                "dataset_id": ds["id"], "output_dir": str(out),
                "captions_only": True, "caption_format": "txt",
            })
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

            rows = list(csv.DictReader((out / "licenses.csv").open(encoding="utf-8")))
            for row in rows:
                assert row["file"].endswith(".txt"), row["file"]
                assert (out / row["file"]).exists(), row["file"]

    run(scenario())
