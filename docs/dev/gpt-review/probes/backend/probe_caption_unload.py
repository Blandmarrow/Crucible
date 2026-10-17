import asyncio, threading
import torch
from backend.ml.model_manager import model_manager, ModelEntry
from backend.tests.conftest import API, api_env, run, upload_image, wait_for_job


class FakeFlorence:
    def __init__(self):
        self.dev = "cuda"
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self.p = torch.nn.Parameter(torch.zeros(1))
    def parameters(self):
        return iter([self.p])
    def cpu(self):
        self.dev = "cpu"; return self
    def generate(self, **kw):
        self.calls += 1
        if self.calls == 1:
            self.entered.set()
            assert self.release.wait(10)
        if self.dev != "cuda":
            raise RuntimeError("Expected all tensors to be on the same device (weights on %s, inputs on cuda)" % self.dev)
        return torch.zeros(1, 1)


class FakeProc:
    def __call__(self, text, images, return_tensors):
        return {"input_ids": torch.zeros(1, 1, dtype=torch.long), "pixel_values": torch.zeros(1, 3, 2, 2)}
    def batch_decode(self, ids, skip_special_tokens=False):
        return ["x"]
    def post_process_generation(self, text, task, image_size):
        return {task: "a cat"}


def test_032_unload_during_inference(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            for n in ("a.png", "b.png"):
                await upload_image(env, ds["id"], n)
            fm = FakeFlorence()
            entry = ModelEntry(fm, FakeProc(), 100)
            monkeypatch.setitem(model_manager._registry, "florence2_large", entry)
            r = await env.client.post(f"{API}/captioning/run", json={"dataset_id": ds["id"], "model": "florence2_large"})
            assert r.status_code == 200, r.text
            jid = r.json()["job_id"]
            for _ in range(200):
                if fm.entered.is_set(): break
                await asyncio.sleep(0.05)
            assert fm.entered.is_set()
            print("weights dev before unload:", fm.dev)
            u = await env.client.delete(f"{API}/captioning/model/florence2_large/unload")
            print("unload status:", u.status_code, "weights dev after unload:", fm.dev,
                  "entry has .model:", hasattr(entry, "model"), "registry has:", "florence2_large" in model_manager._registry)
            assert u.status_code == 204 and fm.dev == "cpu"
            fm.release.set()
            job = await wait_for_job(env, jid)
            print("job", job["status"], job["result_data"], job["error_msg"])
            assert job["result_data"].get("failed_count", 0) >= 1
    run(go())
