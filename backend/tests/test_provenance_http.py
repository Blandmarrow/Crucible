"""Request-level regression tests for source & license provenance.

These go through `backend.main.app` (see conftest.py) rather than calling service
helpers, because that is the gap the branch's blockers fell through: the suite was
green while `POST /comfy/run` died on its first image, and while an over-long
captured license made an image's provenance permanently unsaveable. Both are
router-shaped failures that no service-level test can reach.
"""
import csv
import io
import json

from PIL import Image as PilImage
from sqlalchemy import select

from backend.models.image import Image
from backend.tests.conftest import API, api_env, run, wait_for_job


def _png_bytes(color=(10, 120, 200), size=(16, 16), **text) -> bytes:
    """A real PNG, optionally carrying tEXt chunks (Author/Copyright/…)."""
    from PIL import PngImagePlugin

    info = PngImagePlugin.PngInfo()
    for k, v in text.items():
        info.add_text(k, v)
    buf = io.BytesIO()
    PilImage.new("RGB", size, color).save(buf, "PNG", pnginfo=info)
    return buf.getvalue()


async def _upload(env, dataset_id: str, name: str = "a.png", data: bytes | None = None) -> dict:
    """Upload one image and return its row. The endpoint returns filenames only."""
    r = await env.client.post(
        f"{API}/images/upload",
        params={"dataset_id": dataset_id},
        files=[("files", (name, data or _png_bytes(), "image/png"))],
    )
    assert r.status_code == 201, r.text
    filename = r.json()["files"][0]
    listing = (await env.client.get(f"{API}/images/", params={"dataset_id": dataset_id})).json()
    return next(i for i in listing if i["filename"] == filename)


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
        return _png_bytes()

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
            assert detail["source_meta"]["checkpoint"] == "sdxl.safetensors"

    run(scenario())


def test_comfy_output_inherits_a_dataset_that_records_a_license(tmp_path, monkeypatch):
    """An img2img plan over a licensed source dataset must not launder the license.

    Stamping `license="synthetic"` unconditionally hid a CC-BY-NC source from the
    commercial-use export filter, and left `attribution` inheriting a real
    photographer's credit next to a "synthetic" license.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            import backend.routers.comfy as comfy_router
            monkeypatch.setattr(comfy_router, "ComfyClient", _FakeComfyClient)
            await env.client.patch(f"{API}/settings/thresholds", json={"comfyui_url": "http://fake:8188"})

            ds = await env.create_dataset(
                "licensed", license="CC-BY-NC-4.0", attribution="Photo by Jane Doe")
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
            prov = detail["provenance"]
            assert prov["license"] == "CC-BY-NC-4.0"
            assert prov["attribution"] == "Photo by Jane Doe"
            # All four inherited together — never "synthetic" over a real credit.
            assert set(prov["inherited"]) >= {"license", "attribution"}

    run(scenario())


def test_comfy_output_does_not_credit_a_photographer_for_an_ai_image(tmp_path, monkeypatch):
    """A dataset with *no* license but a real credit line must not leak it.

    The all-or-nothing gate used to test `ds.license` alone, and then tried to
    block inheritance by stamping `source_url=""`/`attribution=""` — which does
    nothing, because `resolve_provenance` treats "" and NULL alike as "inherit".
    So a dataset recording only an attribution produced a `license=synthetic`
    image carrying the photographer's credit, straight into CREDITS.md.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            import backend.routers.comfy as comfy_router
            monkeypatch.setattr(comfy_router, "ComfyClient", _FakeComfyClient)
            await env.client.patch(f"{API}/settings/thresholds", json={"comfyui_url": "http://fake:8188"})

            ds = await env.create_dataset(
                "credited", source_name="Flickr", source_url="https://flickr.test/p/1",
                attribution="Photo by Jane Doe")
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
            prov = (await env.client.get(f"{API}/images/{images[0]['id']}")).json()["provenance"]
            # The dataset records provenance, so it owns the whole story: the
            # output inherits every field rather than mixing "synthetic" in.
            assert prov["attribution"] == "Photo by Jane Doe"
            assert prov["source_url"] == "https://flickr.test/p/1"
            assert prov["license"] == ""
            assert set(prov["inherited"]) >= {"source_name", "source_url", "attribution"}

    run(scenario())


def test_comfy_output_leaves_url_and_attribution_null_on_a_bare_dataset(tmp_path, monkeypatch):
    """With nothing recorded anywhere, the run stamps only what it actually knows."""
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
            # NULL, not "": nobody to credit and no URL is the honest record.
            assert detail["source_url"] is None
            assert detail["attribution"] is None

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
            (src / "pic.png").write_bytes(_png_bytes())
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
            img = await _upload(env, ds["id"])

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
            img = await _upload(env, ds["id"])

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
            img = await _upload(env, ds["id"])

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
            img_a = await _upload(env, ds_a["id"])
            img_b = await _upload(env, ds_b["id"])
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
            blurry = await _upload(env, ds["id"], "blurry.png")
            clean = await _upload(env, ds["id"], "clean.png", _png_bytes((200, 30, 30)))

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

            img_a = await _upload(env, ds_a["id"], "a.png")
            img_b = await _upload(env, ds_b["id"], "b.png", _png_bytes((9, 9, 9)))

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

            img_a = await _upload(env, ds_a["id"], "a.png")
            img_b = await _upload(env, ds_b["id"], "b.png", _png_bytes((9, 9, 9)))

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
            img = await _upload(env, ds["id"], "a.png", _png_bytes(size=(64, 64)))

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


# --- capture paths -------------------------------------------------------


def test_upload_captures_png_text_attribution(tmp_path):
    """PNGs carry no EXIF Artist tag — reading only EXIF captured nothing at all."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await _upload(
                env, ds["id"], "credited.png",
                _png_bytes(Author="Jane Doe", Copyright="© 2026 Jane Doe"),
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
            (src / "workflow.png").write_bytes(_png_bytes())
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
            assert detail["source_meta"] is None
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
            img = await _upload(env, ds["id"])

            async with env.Session() as db:
                row = (await db.execute(select(Image).where(Image.id == img["id"]))).scalar_one()
                row.source_meta = {"post_id": 7}
                await db.commit()

            r = await env.client.patch(
                f"{API}/images/{img['id']}/provenance", json={"license": "CC0-1.0"})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["license"] == "CC0-1.0"
            assert body["source_meta"] == {"post_id": 7}
            assert body["has_dino_layer_embeddings"] is False

            # GET must serialize it the same way.
            got = (await env.client.get(f"{API}/images/{img['id']}")).json()
            assert got["source_meta"] == {"post_id": 7}

    run(scenario())


def test_snapshot_and_restore_round_trip_through_the_router(tmp_path):
    """create_snapshot reads the deferred source_meta; restore writes all five columns."""
    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.patch(f"{API}/settings/thresholds", json={"versioning_mode": "manual"})
            assert r.status_code == 200, r.text

            ds = await env.create_dataset("d")
            img = await _upload(env, ds["id"])
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
            assert got["source_meta"] == {"post_id": 7}

    run(scenario())


# --- export: filters, manifest placement, cancellation -------------------


async def _export_env(env, tmp_path):
    """A dataset defaulting to CC-BY-NC-4.0 with one owned override + one unlicensed."""
    ds = await env.create_dataset("exp", license="CC-BY-NC-4.0", source_name="Flickr")
    inherits = await _upload(env, ds["id"], "inherits.png")
    owned = await _upload(env, ds["id"], "owned.png", _png_bytes((1, 2, 3)))
    await env.client.patch(f"{API}/images/{owned['id']}/provenance", json={"license": "owned"})

    unlicensed_ds = await env.create_dataset("unl")
    unlicensed = await _upload(env, unlicensed_ds["id"], "unl.png", _png_bytes((4, 5, 6)))
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
            inherits = await _upload(env, ds["id"], "inherits.png")
            owned = await _upload(env, ds["id"], "owned.png", _png_bytes((1, 2, 3)))
            custom = await _upload(env, ds["id"], "custom.png", _png_bytes((4, 5, 6)))
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
            await _upload(env, unl_ds["id"], "bare.png", _png_bytes((7, 8, 9)))
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
            await _upload(env, ds["id"], "a.png")

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
                await _upload(env, ds["id"], f"{n}.png", _png_bytes((n * 20, 30, 40)))

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
                await _upload(env, ds["id"], f"{n}.png", _png_bytes((n * 20, 30, 40)))

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
