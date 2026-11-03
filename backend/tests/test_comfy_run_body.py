"""The `comfy_generate` run body, end to end against `FakeComfy` over HTTP.

The other run tests replace `ComfyClient` wholesale (`test_provenance_http`'s
`_FakeComfyClient`) or cover only the queue paths (`test_comfy_queue_cleanup`).
These drive the real client through `httpx.MockTransport`, so the history-entry
shapes, `/view` downloads and PNG chunks are ComfyUI's, not a stub's: import and
generation metadata, multi-output rows, a PreviewImage source, an execution
error, a rejected graph, the per-row rollback and §1's folders. The last test
runs the same fake on a real socket, which is how the e2e suite serves it.
"""
import functools
import io
import threading

import httpx
from PIL import Image as PilImage

from backend.services.comfy_service import ComfyClient
from backend.tests.conftest import API, api_env, run, wait_for_job
from backend.tests.fake_comfy import (
    CORRUPT_MARKER,
    FAIL_MARKER,
    FAIL_MESSAGE,
    REJECT_MARKER,
    REJECT_MESSAGE,
    FakeComfy,
    serve,
    transport,
)

PROMPT = "prompt"
CHECKPOINT = "sdxl.safetensors"


def _workflow(*outputs: tuple, link_to: str = "8") -> dict:
    """Checkpoint → text encode → an image node, then the given output nodes:
    `(node_id, class_type)` or `(node_id, class_type, filename_prefix)`. The
    fake 400s on a link to a missing node, so `link_to` can break the graph."""
    wf = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CHECKPOINT}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "template text", "clip": ["4", 1]}},
        "8": {"class_type": "EmptyImage", "inputs": {"width": 64, "height": 64, "batch_size": 1, "color": 0}},
    }
    for node_id, cls, *prefix in outputs:
        inputs: dict = {"images": [link_to, 0]}
        if prefix:
            inputs["filename_prefix"] = prefix[0]
        wf[node_id] = {"class_type": cls, "inputs": inputs}
    return wf


async def _setup(env, monkeypatch, fake: FakeComfy, workflow: dict, output_node_ids: list[str]):
    """A dataset and a plan whose prompt pin is node 6's text, the router wired to `fake`."""
    import backend.routers.comfy as comfy_router

    monkeypatch.setattr(comfy_router, "ComfyClient", functools.partial(ComfyClient, transport=transport(fake)))
    r = await env.client.patch(f"{API}/settings/thresholds", json={"comfyui_url": "http://fakecomfy:8188"})
    assert r.status_code == 200, r.text
    ds = await env.create_dataset("comfy-run-ds")
    r = await env.client.post(f"{API}/comfy/plans", json={
        "dataset_id": ds["id"], "name": "plan", "workflow_json": workflow,
        "pinned_params": [{"node_id": "6", "input": "text", "alias": PROMPT, "is_prompt": True}],
        "output_node_ids": output_node_ids,
    })
    assert r.status_code == 200, r.text
    return ds, r.json()["id"]


async def _add_row(env, plan_id: str, prompt: str, subfolder: str = "") -> dict:
    r = await env.client.post(f"{API}/comfy/plans/{plan_id}/rows", json={"values": {PROMPT: prompt}})
    assert r.status_code == 200, r.text
    row = r.json()
    if subfolder:
        r = await env.client.patch(f"{API}/comfy/rows/{row['id']}", json={"subfolder": subfolder})
        assert r.status_code == 200, r.text
        row = r.json()
    return row


async def _run(env, plan_id: str, **body) -> dict:
    r = await env.client.post(f"{API}/comfy/run", json={"plan_id": plan_id, **body})
    assert r.status_code == 200, r.text
    return await wait_for_job(env, r.json()["job_id"])


async def _rows(env, plan_id: str) -> list[dict]:
    r = await env.client.get(f"{API}/comfy/plans/{plan_id}/rows")
    assert r.status_code == 200, r.text
    return r.json()


async def _images(env, ds_id: str) -> list[dict]:
    r = await env.client.get(f"{API}/images/", params={"dataset_id": ds_id})
    assert r.status_code == 200, r.text
    return r.json()


def _files(env, folder: str) -> list[str]:
    ds_dir = next(env.datasets_dir.iterdir())
    return sorted(p.name for p in (ds_dir / folder).iterdir() if p.is_file())


def test_two_save_nodes_import_both_with_their_generation_metadata(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            fake = FakeComfy(canvas_running=False, complete=True)
            # Node 10 saves into a ComfyUI subfolder, which `/view` must be asked for.
            ds, plan_id = await _setup(env, monkeypatch, fake, _workflow(
                ("9", "SaveImage"), ("10", "SaveImage", "crucible/run")), ["9", "10"])
            await _add_row(env, plan_id, "a knight in the rain")
            job = await _run(env, plan_id)
            assert job["status"] == "completed", job

            [row] = await _rows(env, plan_id)
            assert row["status"] == "completed", row
            assert len(row["image_ids"]) == 2
            assert row["image_id"] == row["image_ids"][0]

            for image_id in row["image_ids"]:
                detail = (await env.client.get(f"{API}/images/{image_id}")).json()
                meta = detail["generation_metadata"]
                assert meta["source"] == "comfyui"
                # Read from the PNG's `prompt` chunk: the graph actually submitted,
                # with the row's text patched in, not the plan's template.
                assert meta["prompt"] == "a knight in the rain"
                # SaveImage writes `workflow` only from extra_pnginfo, which the run
                # never sends — so there is no editor graph to show.
                assert "comfyui_workflow" not in meta
                prov = detail["provenance"]
                assert prov["license"] == "synthetic"
                assert prov["source_meta"]["checkpoint"] == CHECKPOINT
                assert prov["source_meta"]["row_id"] == row["id"]
            assert fake.violations == []

    run(go())


def test_preview_only_workflow_imports_the_temp_image(tmp_path, monkeypatch):
    """A PreviewImage writes `type: "temp"`, which the default import skips; the
    plan's output-node selection is what makes it an import source."""
    async def go():
        async with api_env(tmp_path) as env:
            fake = FakeComfy(canvas_running=False, complete=True)
            ds, plan_id = await _setup(env, monkeypatch, fake, _workflow(("12", "PreviewImage")), ["12"])
            await _add_row(env, plan_id, "preview me")
            job = await _run(env, plan_id)
            assert job["status"] == "completed", job

            [row] = await _rows(env, plan_id)
            assert row["status"] == "completed", row
            assert len(await _images(env, ds["id"])) == 1

    run(go())


def test_execution_error_fails_the_row_and_the_run_moves_on(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            fake = FakeComfy(canvas_running=False, complete=True)
            ds, plan_id = await _setup(env, monkeypatch, fake, _workflow(("9", "SaveImage")), ["9"])
            await _add_row(env, plan_id, f"this one breaks {FAIL_MARKER}")
            await _add_row(env, plan_id, "this one works")
            job = await _run(env, plan_id)
            assert job["status"] == "completed", job

            failed, done = await _rows(env, plan_id)
            assert failed["status"] == "failed"
            assert FAIL_MESSAGE in failed["error_msg"]
            assert failed["image_ids"] in (None, [])
            assert done["status"] == "completed"
            images = await _images(env, ds["id"])
            assert [i["id"] for i in images] == done["image_ids"]
            # A prompt that reached /history has nothing left in ComfyUI to clean.
            assert not any(path in ("/queue", "/interrupt") for _, path in fake.calls)

    run(go())


def test_rejected_graph_fails_with_its_node_errors(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            fake = FakeComfy(canvas_running=False, complete=True)
            ds, plan_id = await _setup(env, monkeypatch, fake, _workflow(("9", "SaveImage")), ["9"])
            await _add_row(env, plan_id, f"no such model {REJECT_MARKER}")
            job = await _run(env, plan_id)
            assert job["status"] == "completed", job

            [row] = await _rows(env, plan_id)
            assert row["status"] == "failed"
            assert "Prompt outputs failed validation" in row["error_msg"]
            assert f"node 4: {REJECT_MESSAGE}" in row["error_msg"]
            assert fake.submitted == []
            assert not any(path in ("/queue", "/interrupt") for _, path in fake.calls)
            assert await _images(env, ds["id"]) == []

    run(go())


def test_a_graph_comfyui_would_refuse_fails_the_row(tmp_path, monkeypatch):
    """A dangling link, and a graph with no output node, are both 400s."""
    async def go():
        async with api_env(tmp_path) as env:
            fake = FakeComfy(canvas_running=False, complete=True)
            ds, plan_id = await _setup(
                env, monkeypatch, fake, _workflow(("9", "SaveImage"), link_to="77"), ["9"])
            await _add_row(env, plan_id, "dangling")
            job = await _run(env, plan_id)
            assert job["status"] == "completed", job
            [row] = await _rows(env, plan_id)
            assert row["status"] == "failed"
            assert "node 9: Linked node not found" in row["error_msg"]

            r = await env.client.patch(f"{API}/comfy/plans/{plan_id}", json={
                "workflow_json": _workflow(), "output_node_ids": []})
            assert r.status_code == 200, r.text
            r = await env.client.post(f"{API}/comfy/plans/{plan_id}/rows/reset", json={})
            assert r.status_code == 200, r.text
            await _run(env, plan_id)
            [row] = await _rows(env, plan_id)
            assert row["status"] == "failed"
            assert "Prompt has no outputs" in row["error_msg"]
            assert fake.submitted == []
            assert await _images(env, ds["id"]) == []

    run(go())


def test_a_corrupt_second_output_rolls_back_the_whole_row(tmp_path, monkeypatch):
    """The first image is written and registered before the second fails to
    decode. Per-row atomicity: neither file, thumbnail nor `Image` row survives."""
    async def go():
        async with api_env(tmp_path) as env:
            fake = FakeComfy(canvas_running=False, complete=True)
            ds, plan_id = await _setup(
                env, monkeypatch, fake, _workflow(("9", "SaveImage"), ("10", "SaveImage")), ["9", "10"])
            await _add_row(env, plan_id, f"half a row {CORRUPT_MARKER}")
            job = await _run(env, plan_id)
            assert job["status"] == "completed", job

            [row] = await _rows(env, plan_id)
            assert row["status"] == "failed", row
            assert await _images(env, ds["id"]) == []
            assert _files(env, "images") == []
            assert _files(env, "thumbnails") == []

    run(go())


def test_row_folders_file_and_name_each_output(tmp_path, monkeypatch):
    """§1 over real history entries: `{base}/{row folder}` as the subfolder, the
    row's own leaf as the stem, and the whole tree declared."""
    async def go():
        async with api_env(tmp_path) as env:
            fake = FakeComfy(canvas_running=False, complete=True)
            ds, plan_id = await _setup(env, monkeypatch, fake, _workflow(("9", "SaveImage")), ["9"])
            await _add_row(env, plan_id, "close up", subfolder="knight/close")
            await _add_row(env, plan_id, "wide shot", subfolder="knight/wide")
            job = await _run(env, plan_id, subfolder="set")
            assert job["status"] == "completed", job

            images = sorted(await _images(env, ds["id"]), key=lambda i: i["subfolder"])
            assert [i["subfolder"] for i in images] == ["set/knight/close", "set/knight/wide"]
            assert images[0]["filename"].startswith("close")
            assert images[1]["filename"].startswith("wide")

            r = await env.client.get(f"{API}/datasets/{ds['id']}/subfolders")
            assert r.status_code == 200, r.text
            listed = {s["path"] for s in r.json()}
            assert {"set", "set/knight", "set/knight/close", "set/knight/wide"} <= listed

    run(go())


def test_the_fake_serves_the_same_behaviour_over_a_real_socket():
    """The e2e suite's adapter: `serve()` must answer exactly as the transport does."""
    fake = FakeComfy(canvas_running=False, complete=True, polls_before_done=1)
    server = serve(fake, port=0, quiet=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    client = ComfyClient(url)

    async def go():
        assert (await client.ping())["system"]["comfyui_version"] == "fake"
        pid = await client.submit(_workflow(("9", "SaveImage")), client_id="job-1")
        # In flight for one poll: running in the queue, nothing in history yet.
        state = await client.queue_state()
        assert [(i.prompt_id, i.client_id) for i in state.running] == [(pid, "job-1")]
        assert await client.poll_history(pid) is None
        entry = await client.poll_history(pid)
        assert entry["status"]["status_str"] == "success"
        [ref] = entry["outputs"]["9"]["images"]
        data = await client.fetch_image(ref["filename"], ref["subfolder"], ref["type"])
        assert data.startswith(b"\x89PNG")
        with PilImage.open(io.BytesIO(data)) as img:
            assert set(img.info) == {"prompt"}  # no extra_pnginfo, so no `workflow` chunk
        assert (await client.queue_state()).running == []

        async with httpx.AsyncClient() as http:
            r = await http.post(f"{url}/free", json={"unload_models": True, "free_memory": True})
            assert r.status_code == 200
        assert fake.frees == [{"unload_models": True, "free_memory": True}]

    try:
        run(go())
    finally:
        server.shutdown()
        server.server_close()
