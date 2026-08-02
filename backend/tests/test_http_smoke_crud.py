"""Request-level smoke tests for the CRUD & filesystem routers.

The suite is otherwise service-level, which is exactly how a router that crashes
can ship green: a hard failure in a router's request/response wiring — a bad
unpack, a serializer that touches an unloaded deferred column, a status code that
never matches the client's expectation — is invisible to a test that calls the
service helper directly. `test_provenance_http.py` closes that gap for provenance;
this file does the broad, shallow version for the ordinary CRUD endpoints a user
hits constantly. Each test drives one router through `backend.main.app` over
`httpx.ASGITransport` (see conftest.py): roughly one happy path plus one failure
per router, chosen so no monkeypatched fakes are needed.
"""
import json

from backend.tests.conftest import (
    API,
    api_env,
    mp4_bytes,
    needs_cv2,
    png_bytes,
    run,
    upload_image,
)


# --- datasets ------------------------------------------------------------


def test_datasets_crud_and_subfolders(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("crud-ds", description="hi")

            listing = (await env.client.get(f"{API}/datasets/")).json()
            assert any(d["id"] == ds["id"] for d in listing)

            got = await env.client.get(f"{API}/datasets/{ds['id']}")
            assert got.status_code == 200, got.text
            assert got.json()["name"] == "crud-ds"

            r = await env.client.patch(f"{API}/datasets/{ds['id']}", json={"description": "bye"})
            assert r.status_code == 200, r.text
            assert r.json()["description"] == "bye"

            # Subfolders: none, then declare one.
            subs = await env.client.get(f"{API}/datasets/{ds['id']}/subfolders")
            assert subs.status_code == 200, subs.text
            r = await env.client.post(f"{API}/datasets/{ds['id']}/subfolders", json={"path": "sub"})
            assert r.status_code == 201, r.text
            assert r.json()["path"] == "sub"
            paths = {s["path"] for s in (await env.client.get(f"{API}/datasets/{ds['id']}/subfolders")).json()}
            assert "sub" in paths

            # The dataset owns exactly one folder on disk; DELETE removes it.
            dirs_before = {p.name for p in env.datasets_dir.iterdir() if p.is_dir()}
            assert len(dirs_before) == 1
            r = await env.client.delete(f"{API}/datasets/{ds['id']}")
            assert r.status_code == 204, r.text
            assert not any(p.is_dir() for p in env.datasets_dir.iterdir())

    run(scenario())


def test_datasets_failures(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            assert (await env.client.get(f"{API}/datasets/does-not-exist")).status_code == 404

            await env.create_dataset("dup")
            r = await env.client.post(f"{API}/datasets/", json={"name": "dup"})
            assert r.status_code == 400, r.text   # duplicate name is a 400, not 409/422

    run(scenario())


# --- images --------------------------------------------------------------


def test_images_crud_and_crop(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("img-ds")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(32, 32)))

            listing = (await env.client.get(f"{API}/images/", params={"dataset_id": ds["id"]})).json()
            assert [i["id"] for i in listing] == [img["id"]]

            assert (await env.client.get(f"{API}/images/{img['id']}")).status_code == 200

            r = await env.client.patch(f"{API}/images/{img['id']}/rename", json={"new_stem": "renamed"})
            assert r.status_code == 200, r.text
            assert r.json()["filename"] == "renamed.png"

            assert (await env.client.get(f"{API}/images/{img['id']}/file")).status_code == 200

            r = await env.client.post(f"{API}/images/{img['id']}/crop", json={
                "x": 0, "y": 0, "width": 16, "height": 16,
            })
            assert r.status_code == 200, r.text
            assert (r.json()["width"], r.json()["height"]) == (16, 16)

            # DELETE removes both the row and the file on disk.
            assert len(list(env.datasets_dir.rglob("renamed.png"))) == 1
            r = await env.client.delete(f"{API}/images/{img['id']}")
            assert r.status_code == 204, r.text
            assert len(list(env.datasets_dir.rglob("renamed.png"))) == 0

    run(scenario())


def test_images_failures(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            # Upload to a dataset that does not exist.
            r = await env.client.post(
                f"{API}/images/upload",
                params={"dataset_id": "nope"},
                files=[("files", ("a.png", png_bytes(), "image/png"))],
            )
            assert r.status_code == 404, r.text

            # A blank entry in the license filter list is rejected, not silently dropped.
            ds = await env.create_dataset("d")
            r = await env.client.get(f"{API}/images/", params={
                "dataset_id": ds["id"], "license_filter": json.dumps([""]),
            })
            assert r.status_code == 400, r.text

    run(scenario())


# --- captions ------------------------------------------------------------


def test_captions_roundtrip_and_sidecar(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("cap-ds")
            img = await upload_image(env, ds["id"], "a.png")

            r = await env.client.put(f"{API}/captions/image/{img['id']}", json={
                "caption_text": "a red square",
            })
            assert r.status_code == 200, r.text
            assert (await env.client.get(f"{API}/captions/image/{img['id']}")).json()["caption_text"] == "a red square"

            # A .txt sidecar lands next to the image file.
            sidecars = list(env.datasets_dir.rglob("a.txt"))
            assert len(sidecars) == 1
            assert sidecars[0].read_text(encoding="utf-8").strip() == "a red square"

    run(scenario())


def test_captions_bulk_find_replace(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("cap-bulk")
            a = await upload_image(env, ds["id"], "a.png")
            b = await upload_image(env, ds["id"], "b.png", png_bytes((9, 9, 9)))
            for img in (a, b):
                await env.client.put(f"{API}/captions/image/{img['id']}", json={"caption_text": "cat, dog"})

            r = await env.client.post(f"{API}/captions/dataset/{ds['id']}/find-replace", json={
                "find": "cat", "replace": "fox",
            })
            assert r.status_code == 200, r.text
            assert r.json()["updated"] == 2
            assert (await env.client.get(f"{API}/captions/image/{a['id']}")).json()["caption_text"] == "fox, dog"

    run(scenario())


def test_captions_missing_image_404(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            assert (await env.client.get(f"{API}/captions/image/nope")).status_code == 404

    run(scenario())


# --- settings ------------------------------------------------------------


def test_settings_thresholds_roundtrip(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            assert (await env.client.get(f"{API}/settings/thresholds")).status_code == 200

            r = await env.client.patch(f"{API}/settings/thresholds", json={"blur_threshold": 42.5})
            assert r.status_code == 200, r.text
            assert (await env.client.get(f"{API}/settings/thresholds")).json()["blur_threshold"] == 42.5

            # A constraint violation is a 422, not a silent clamp.
            assert (await env.client.patch(
                f"{API}/settings/thresholds", json={"blur_threshold": 0})).status_code == 422

    run(scenario())


def test_settings_secrets_roundtrip(tmp_path, monkeypatch):
    # A precondition, not cleanup: start from "no ambient token" whatever the shell
    # exports. Restoring the variable afterwards is api_env's job — this endpoint writes
    # os.environ["HF_TOKEN"] itself, which monkeypatch cannot undo. Depth lives in
    # test_settings_secrets.py; this is the shallow shape check.
    monkeypatch.delenv("HF_TOKEN", raising=False)

    async def scenario():
        async with api_env(tmp_path) as env:
            assert (await env.client.get(f"{API}/settings/secrets")).status_code == 200

            r = await env.client.patch(f"{API}/settings/secrets", json={"gelbooru_user_id": "4242"})
            assert r.status_code == 200, r.text
            body = (await env.client.get(f"{API}/settings/secrets")).json()
            assert body["gelbooru_user_id"] == {"masked": "****", "source": "db"}

            # Over the column length is a 422, not a truncation.
            assert (await env.client.patch(
                f"{API}/settings/secrets", json={"hf_token": "x" * 501})).status_code == 422

    run(scenario())


# --- jobs ----------------------------------------------------------------


def test_jobs_list_and_missing(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            assert (await env.client.get(f"{API}/jobs/")).status_code == 200
            assert (await env.client.get(f"{API}/jobs/nope")).status_code == 404

    run(scenario())


# --- providers -----------------------------------------------------------


def test_providers_crud(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.post(f"{API}/providers/", json={
                "name": "local", "base_url": "http://localhost:1234/v1",
            })
            assert r.status_code == 201, r.text
            pid = r.json()["id"]

            assert any(p["id"] == pid for p in (await env.client.get(f"{API}/providers/")).json())

            r = await env.client.patch(f"{API}/providers/{pid}", json={"default_model": "llava"})
            assert r.status_code == 200, r.text
            assert r.json()["default_model"] == "llava"

            assert (await env.client.delete(f"{API}/providers/{pid}")).status_code == 204
            assert (await env.client.patch(f"{API}/providers/nope", json={"name": "x"})).status_code == 404

    run(scenario())


# --- filesystem ----------------------------------------------------------


def test_filesystem_roots_and_list(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            assert (await env.client.get(f"{API}/filesystem/roots")).status_code == 200

            r = await env.client.get(f"{API}/filesystem/list", params={"path": str(env.datasets_dir)})
            assert r.status_code == 200, r.text

            assert (await env.client.get(
                f"{API}/filesystem/list", params={"path": str(tmp_path / "nope")})).status_code == 404

    run(scenario())


@needs_cv2
def test_filesystem_preview_serves_images_and_videos_but_nothing_else(tmp_path):
    """One route for both kinds. The video branch is what lets the browser's
    preview panel play a clip: FileResponse supplies Range/206 on its own, and
    the content type has to come from `video_mime` because mimetypes.guess_type
    is unreliable for .mkv."""
    async def scenario():
        async with api_env(tmp_path) as env:
            img = tmp_path / "a.png"
            img.write_bytes(png_bytes())
            clip = tmp_path / "a.mkv"
            clip.write_bytes(mp4_bytes())
            note = tmp_path / "a.txt"
            note.write_text("a caption, not media")

            r = await env.client.get(f"{API}/filesystem/preview", params={"path": str(img)})
            assert r.status_code == 200, r.text
            assert r.headers["content-type"] == "image/png"

            r = await env.client.get(f"{API}/filesystem/preview", params={"path": str(clip)})
            assert r.status_code == 200, r.text
            assert r.headers["content-type"] == "video/x-matroska"
            assert r.headers["accept-ranges"] == "bytes"

            r = await env.client.get(f"{API}/filesystem/preview", params={"path": str(note)})
            assert r.status_code == 400

            r = await env.client.get(f"{API}/filesystem/preview", params={"path": str(tmp_path / "nope.png")})
            assert r.status_code == 404

    run(scenario())


@needs_cv2
def test_filesystem_image_meta_stays_image_only(tmp_path):
    """/preview widened to all media; /image-meta deliberately did not — there
    is no generation metadata to read out of a container."""
    async def scenario():
        async with api_env(tmp_path) as env:
            clip = tmp_path / "a.mp4"
            clip.write_bytes(mp4_bytes())

            r = await env.client.get(f"{API}/filesystem/image-meta", params={"path": str(clip)})
            assert r.status_code == 400

    run(scenario())


# --- comfy plans & rows --------------------------------------------------


def test_comfy_plans_and_rows_crud(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("comfy-ds")

            r = await env.client.post(f"{API}/comfy/plans", json={
                "dataset_id": ds["id"], "name": "p", "workflow_json": {},
            })
            assert r.status_code == 200, r.text   # comfy create returns 200, not 201
            plan_id = r.json()["id"]

            assert (await env.client.get(f"{API}/comfy/plans/{plan_id}")).status_code == 200

            r = await env.client.post(f"{API}/comfy/plans/{plan_id}/rows", json={"values": {"a": "1"}})
            assert r.status_code == 200, r.text
            rows = (await env.client.get(f"{API}/comfy/plans/{plan_id}/rows")).json()
            assert len(rows) == 1
            row_id = rows[0]["id"]

            r = await env.client.patch(f"{API}/comfy/rows/{row_id}", json={"values": {"a": "2"}})
            assert r.status_code == 200, r.text

            # Rows are deleted by id list, not a REST DELETE.
            r = await env.client.post(f"{API}/comfy/plans/{plan_id}/rows/delete", json={"row_ids": [row_id]})
            assert r.status_code == 200, r.text
            assert r.json()["deleted"] == 1

            assert (await env.client.delete(f"{API}/comfy/plans/{plan_id}")).status_code == 204

    run(scenario())


def test_comfy_missing_plan_404(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            assert (await env.client.get(f"{API}/comfy/plans/nope")).status_code == 404

    run(scenario())


# --- detection (manual only — no ML) -------------------------------------


def test_detection_manual_crud(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("det-ds")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(64, 64)))

            r = await env.client.post(f"{API}/detection/manual", json={
                "image_id": img["id"], "label": "face", "bbox": [0.1, 0.1, 0.5, 0.5],
            })
            assert r.status_code == 200, r.text   # manual (no SAM) is synchronous → 200

            dets = (await env.client.get(f"{API}/detection/image/{img['id']}")).json()
            assert len(dets) == 1
            det_id = dets[0]["id"]

            r = await env.client.patch(f"{API}/detection/{det_id}", json={"label": "head"})
            assert r.status_code == 200, r.text
            assert r.json()["label"] == "head"

            assert (await env.client.delete(f"{API}/detection/{det_id}")).status_code == 204
            assert (await env.client.get(f"{API}/detection/image/{img['id']}")).json() == []

    run(scenario())


def test_detection_degenerate_bbox_400(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("det-bad")
            img = await upload_image(env, ds["id"], "a.png")

            # A zero-area box clamps to < 0.002 extent → rejected by _sanitize_bbox (400).
            r = await env.client.post(f"{API}/detection/manual", json={
                "image_id": img["id"], "label": "x", "bbox": [0.5, 0.5, 0.5, 0.5],
            })
            assert r.status_code == 400, r.text

    run(scenario())
