import asyncio, httpx
from backend.tests.conftest import API, api_env, run, wait_for_job

REAL = httpx.AsyncClient


class FakeComfy:
    """Minimal in-process ComfyUI: someone else's prompt is running, ours queues behind it."""
    def __init__(self):
        self.running = ["OTHER-CLIENT-PROMPT"]
        self.pending = []
        self.interrupted = []
        self.hosts = set()
        self.calls = []
    def handler(self, request: httpx.Request):
        self.hosts.add(request.url.host)
        p = request.url.path
        self.calls.append((request.method, p))
        if request.method == "POST" and p == "/prompt":
            pid = f"OURS-{len(self.pending)+1}"
            self.pending.append(pid)
            return httpx.Response(200, json={"prompt_id": pid, "number": 1})
        if request.method == "GET" and p.startswith("/history/"):
            return httpx.Response(200, json={})
        if request.method == "POST" and p == "/interrupt":
            # real ComfyUI: global flag, stops whatever is executing right now
            if self.running:
                self.interrupted.append(self.running.pop(0))
            return httpx.Response(200)
        if p == "/system_stats":
            return httpx.Response(200, json={})
        return httpx.Response(404)


async def _setup(env, monkeypatch, fake):
    import backend.routers.comfy as comfy_router
    r = await env.client.patch(f"{API}/settings/thresholds", json={"comfyui_url": "http://fakecomfy:8188"})
    assert r.status_code == 200, r.text
    ds = await env.create_dataset("c")
    wf = {"4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "x.safetensors"}},
          "9": {"class_type": "SaveImage", "inputs": {}}}
    r = await env.client.post(f"{API}/comfy/plans", json={"dataset_id": ds["id"], "name": "p", "workflow_json": wf, "output_node_ids": ["9"]})
    plan_id = r.json()["id"]
    for _ in range(2):
        r = await env.client.post(f"{API}/comfy/plans/{plan_id}/rows", json={"values": {}})
        assert r.status_code == 200
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: REAL(*a, **{**kw, "transport": httpx.MockTransport(fake.handler)}))
    return plan_id


def test_033_cancel_interrupts_other_clients_prompt(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            fake = FakeComfy()
            plan_id = await _setup(env, monkeypatch, fake)
            r = await env.client.post(f"{API}/comfy/run", json={"plan_id": plan_id})
            assert r.status_code == 200, r.text
            jid = r.json()["job_id"]
            for _ in range(100):
                if fake.pending: break
                await asyncio.sleep(0.05)
            assert fake.pending
            d = await env.client.delete(f"{API}/jobs/{jid}")
            job = await wait_for_job(env, jid)
            print("job:", job["status"], "| interrupted:", fake.interrupted, "| ours still pending in comfy:", fake.pending,
                  "| hosts:", fake.hosts)
            # POST /interrupt carries no prompt id
            assert fake.interrupted == ["OTHER-CLIENT-PROMPT"]
            assert fake.pending == ["OURS-1"]
    run(go())


def test_033_timeout_leaves_remote_prompt_and_stacks_next(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            import backend.routers.comfy as comfy_router
            monkeypatch.setattr(comfy_router, "PER_ROW_TIMEOUT_S", 1.5)
            fake = FakeComfy()
            plan_id = await _setup(env, monkeypatch, fake)
            r = await env.client.post(f"{API}/comfy/run", json={"plan_id": plan_id})
            jid = r.json()["job_id"]
            job = await wait_for_job(env, jid, timeout=30)
            rows = (await env.client.get(f"{API}/comfy/plans/{plan_id}/rows")).json()
            print("job:", job["status"], "| row statuses:", [x["status"] for x in rows], "| row errs:", [x["error_msg"] for x in rows],
                  "| prompts still pending in ComfyUI:", fake.pending, "| interrupts:", fake.interrupted,
                  "| calls to /queue or DELETE:", [c for c in fake.calls if c[1].startswith('/queue') or c[0]=='DELETE'])
            assert all(x["status"] == "failed" for x in rows)
            assert len(fake.pending) == 2  # both submitted prompts remain outstanding (stacked), never removed
    run(go())
