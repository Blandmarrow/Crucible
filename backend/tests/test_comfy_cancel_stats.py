"""A cancelled ComfyUI run must leave the dataset's image counters correct.

`Dataset.image_count` is a stored column that `GET /datasets/{id}` returns verbatim,
so it is only ever as fresh as the last `refresh_stats` call. The comfy worker used
to call it once, after the row loop — which cancellation and the connect-error abort
both raise straight past, while every completed row had already committed its images.
The sidebar counter then read low forever (no client refetch can fix a stale column),
and the frontend did not even refetch, because TopBar only invalidated on "completed".

Request-level for the same reason as `test_provenance_http.py`: the bug lives in the
job body of a router, where no service-level test reaches.
"""
from backend.tests.conftest import API, api_env, run, wait_for_job
from backend.tests.test_provenance_http import _png_bytes


class _CancellingComfyClient:
    """Fake ComfyClient that cancels its own job partway through the second row.

    `run_plan` passes the job id as `client_id=` to `submit`, which is how a stub with
    no other handle on the job can reach `job_queue.request_cancel`. Cancelling from
    `poll_history` exercises the cooperative in-poll path (interrupt → revert the row
    to "pending" → raise), i.e. what a user clicking Cancel mid-generation triggers.
    """

    def __init__(self, url: str):
        self.url = url
        self._job_id: str | None = None
        self._row = 0

    async def submit(self, workflow, client_id=None):
        self._job_id = client_id
        self._row += 1
        return f"prompt-{self._row}"

    async def poll_history(self, prompt_id):
        if self._row >= 2:
            from backend.workers.job_queue import job_queue

            job_queue.request_cancel(self._job_id)
            return None  # the loop re-checks the cancel flag before polling again
        return {"outputs": {"9": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}}}

    async def fetch_image(self, filename, subfolder="", type="output"):
        return _png_bytes()

    async def interrupt(self):
        return None


def test_cancelled_comfy_run_leaves_image_count_correct(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            import backend.routers.comfy as comfy_router

            monkeypatch.setattr(comfy_router, "ComfyClient", _CancellingComfyClient)
            r = await env.client.patch(f"{API}/settings/thresholds", json={"comfyui_url": "http://fake:8188"})
            assert r.status_code == 200, r.text

            ds = await env.create_dataset("comfy-cancel-ds")
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

            for _ in range(2):
                r = await env.client.post(f"{API}/comfy/plans/{plan_id}/rows", json={"values": {}})
                assert r.status_code == 200, r.text

            r = await env.client.post(f"{API}/comfy/run", json={"plan_id": plan_id})
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "cancelled", job

            # Row 1 imported; row 2 was cancelled before it produced anything.
            images = (await env.client.get(f"{API}/images/", params={"dataset_id": ds["id"]})).json()
            assert len(images) == 1, images

            detail = (await env.client.get(f"{API}/datasets/{ds['id']}")).json()
            assert detail["image_count"] == 1, detail

    run(scenario())


def test_completed_comfy_run_still_counts(tmp_path, monkeypatch):
    """The refresh moved per-row + into a finally; the success path must be unaffected."""
    from backend.tests.test_provenance_http import _FakeComfyClient

    async def scenario():
        async with api_env(tmp_path) as env:
            import backend.routers.comfy as comfy_router

            monkeypatch.setattr(comfy_router, "ComfyClient", _FakeComfyClient)
            await env.client.patch(f"{API}/settings/thresholds", json={"comfyui_url": "http://fake:8188"})

            ds = await env.create_dataset("comfy-ok-ds")
            r = await env.client.post(f"{API}/comfy/plans", json={
                "dataset_id": ds["id"], "name": "plan",
                "workflow_json": {"9": {"class_type": "SaveImage", "inputs": {}}},
                "output_node_ids": ["9"],
            })
            plan_id = r.json()["id"]
            for _ in range(2):
                await env.client.post(f"{API}/comfy/plans/{plan_id}/rows", json={"values": {}})

            r = await env.client.post(f"{API}/comfy/run", json={"plan_id": plan_id})
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

            detail = (await env.client.get(f"{API}/datasets/{ds['id']}")).json()
            assert detail["image_count"] == 2, detail

    run(scenario())
