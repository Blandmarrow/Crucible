"""A cancelled or failed ComfyUI row must take *its own* prompt out of ComfyUI (GPT-033).

The run used to post a body-less `POST /interrupt` on cancel — a global flag in
ComfyUI that stops whatever is executing, which with the user generating from the
ComfyUI canvas against the same server is *their* prompt, while ours stayed queued —
and to do nothing at all on a per-row timeout, so the next row was stacked on top of
a prompt still waiting in ComfyUI's queue.

`FakeComfy` is an in-process ComfyUI behind `httpx.MockTransport`: a queue with a
running and a pending list, a pending-only `POST /queue {"delete"}`, and a
body-aware `POST /interrupt` that stops only the named prompt — and records a
body-less (global) interrupt as a violation, mimicking an old server by killing
whatever runs.
"""
import asyncio
import functools
import json

import httpx

import backend.services.comfy_service as comfy_service
from backend.services.comfy_service import ComfyClient
from backend.tests.conftest import API, api_env, png_bytes, run, wait_for_job

CANVAS_CLIENT = "canvas-browser-tab"
CANVAS_PROMPT = "CANVAS-1"


class FakeComfy:
    """Minimal ComfyUI. Queue entries are [prompt_id, client_id] pairs."""

    def __init__(
        self,
        *,
        canvas_running: bool = True,
        ours_start_running: bool = False,
        honour_cleanup: bool = True,
        unreachable: bool = False,
        queue_status: int = 200,
        complete: bool = False,
        down_after_submits: int | None = None,
    ):
        self.complete = complete
        # Goes unreachable for every request once this many prompts were accepted.
        self.down_after_submits = down_after_submits
        self.running: list[list] = [[CANVAS_PROMPT, CANVAS_CLIENT]] if canvas_running else []
        self.pending: list[list] = []
        self.history: dict[str, dict] = {}
        self.ours_start_running = ours_start_running
        self.honour_cleanup = honour_cleanup
        self.unreachable = unreachable
        self.queue_status = queue_status
        self.submitted: list[str] = []
        # How many prompts with the submitting client_id were still in ComfyUI's
        # queue at each submit — anything above 0 is a row stacked on a leftover.
        self.ours_outstanding_at_submit: list[int] = []
        self.delete_requests: list[list] = []
        self.deleted: list[str] = []
        self.interrupts: list[str] = []
        self.violations: list[str] = []
        self.calls: list[tuple[str, str]] = []

    def queue_ids(self, client_id: str | None = None) -> list[str]:
        return [pid for pid, cid in self.running + self.pending if client_id is None or cid == client_id]

    def handler(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        self.calls.append((method, path))
        if self.unreachable:
            raise httpx.ConnectError("connection refused", request=request)
        body = json.loads(request.content) if request.content else None

        if method == "POST" and path == "/prompt":
            cid = body.get("client_id")
            self.ours_outstanding_at_submit.append(len(self.queue_ids(cid)))
            pid = f"OURS-{len(self.submitted) + 1}"
            self.submitted.append(pid)
            if self.complete:  # finished instantly: straight into history
                self.history[pid] = {"outputs": {"9": {"images": [
                    {"filename": "out.png", "subfolder": "", "type": "output"}
                ]}}}
            elif self.ours_start_running and not self.running:
                self.running.append([pid, cid])
            else:
                self.pending.append([pid, cid])
            if self.down_after_submits is not None and len(self.submitted) >= self.down_after_submits:
                self.unreachable = True
            return httpx.Response(200, json={"prompt_id": pid, "number": len(self.submitted)})

        if method == "GET" and path.startswith("/history/"):
            pid = path.rsplit("/", 1)[1]
            return httpx.Response(200, json={pid: self.history[pid]} if pid in self.history else {})

        if method == "GET" and path == "/queue":
            if self.queue_status != 200:
                return httpx.Response(self.queue_status, text="boom")

            def items(entries):
                return [[n, pid, {}, {"client_id": cid}, []] for n, (pid, cid) in enumerate(entries)]

            return httpx.Response(200, json={
                "queue_running": items(self.running),
                "queue_pending": items(self.pending),
            })

        if method == "POST" and path == "/queue":
            if not isinstance(body, dict) or "clear" in body:
                self.violations.append(f"/queue body {body!r}")
                return httpx.Response(200)
            ids = list(body.get("delete") or [])
            self.delete_requests.append(ids)
            if self.honour_cleanup:
                for entry in [e for e in self.pending if e[0] in ids]:
                    self.pending.remove(entry)
                    self.deleted.append(entry[0])
            return httpx.Response(200)

        if method == "POST" and path == "/interrupt":
            pid = (body or {}).get("prompt_id") if isinstance(body, dict) else None
            if not pid:
                # What an old ComfyUI does with any interrupt — and a new one with a
                # body-less one: stop whatever is executing, whoever submitted it.
                self.violations.append("global /interrupt")
                if self.running:
                    self.interrupts.append(self.running.pop(0)[0])
                return httpx.Response(200)
            if self.honour_cleanup and self.running and self.running[0][0] == pid:
                self.interrupts.append(self.running.pop(0)[0])
            return httpx.Response(200)

        if method == "GET" and path == "/view":
            return httpx.Response(200, content=png_bytes())
        if path == "/system_stats":
            return httpx.Response(200, json={})
        return httpx.Response(404)


def _client(fake: FakeComfy) -> ComfyClient:
    return ComfyClient("http://fakecomfy:8188", transport=httpx.MockTransport(fake.handler))


def _shrink_confirm_window(monkeypatch, window: float = 0.3, interval: float = 0.05) -> None:
    monkeypatch.setattr(comfy_service, "CANCEL_CONFIRM_WINDOW_S", window)
    monkeypatch.setattr(comfy_service, "CANCEL_CONFIRM_INTERVAL_S", interval)


# ── ComfyClient.cancel_prompt, directly ───────────────────────────────────────


def test_cancel_prompt_not_found_touches_nothing(monkeypatch):
    _shrink_confirm_window(monkeypatch)
    fake = FakeComfy()
    assert run(_client(fake).cancel_prompt("OURS-9", "job-1")) == "not_found"
    assert fake.running == [[CANVAS_PROMPT, CANVAS_CLIENT]]
    assert fake.interrupts == [] and fake.violations == []
    # The blind delete of our own id is harmless (pending-only, idempotent).
    assert fake.delete_requests == [["OURS-9"]]


def test_cancel_prompt_removes_our_pending_prompt(monkeypatch):
    _shrink_confirm_window(monkeypatch)
    fake = FakeComfy()
    fake.pending = [["OURS-1", "job-1"], ["CANVAS-2", CANVAS_CLIENT]]
    assert run(_client(fake).cancel_prompt("OURS-1", "job-1")) == "removed"
    assert fake.deleted == ["OURS-1"]
    assert fake.pending == [["CANVAS-2", CANVAS_CLIENT]]
    assert fake.running == [[CANVAS_PROMPT, CANVAS_CLIENT]]
    assert fake.interrupts == [] and fake.violations == []


def test_cancel_prompt_interrupts_only_our_running_prompt(monkeypatch):
    _shrink_confirm_window(monkeypatch)
    fake = FakeComfy(canvas_running=False)
    fake.running = [["OURS-1", "job-1"]]
    fake.pending = [["CANVAS-2", CANVAS_CLIENT]]
    assert run(_client(fake).cancel_prompt("OURS-1", "job-1")) == "interrupted"
    assert fake.interrupts == ["OURS-1"]
    assert fake.pending == [["CANVAS-2", CANVAS_CLIENT]]
    assert fake.violations == []


def test_cancel_prompt_reports_still_running_when_nothing_takes(monkeypatch):
    _shrink_confirm_window(monkeypatch)
    fake = FakeComfy(canvas_running=False, honour_cleanup=False)
    fake.running = [["OURS-1", "job-1"]]
    fake.pending = [["OURS-2", "job-1"]]
    assert run(_client(fake).cancel_prompt("OURS-1", "job-1")) == "still_running"
    assert fake.queue_ids("job-1") == ["OURS-1", "OURS-2"]
    # Each running prompt of ours is interrupted once, by id — never globally.
    assert fake.violations == []
    assert sum(1 for c in fake.calls if c == ("POST", "/interrupt")) == 1


def test_cancel_prompt_unreadable_queue_is_still_running(monkeypatch):
    """A /queue that errors cannot confirm anything — unconfirmed is not gone."""
    _shrink_confirm_window(monkeypatch)
    fake = FakeComfy(queue_status=500)
    assert run(_client(fake).cancel_prompt("OURS-1", "job-1")) == "still_running"
    assert fake.violations == []


def test_cancel_prompt_unreachable(monkeypatch):
    _shrink_confirm_window(monkeypatch)
    fake = FakeComfy(unreachable=True)
    assert run(_client(fake).cancel_prompt("OURS-1", "job-1")) == "unreachable"


def test_cancel_prompt_sweeps_by_client_id_without_a_prompt_id(monkeypatch):
    """A cancel during submit has no prompt id yet: our client_id finds the prompt."""
    _shrink_confirm_window(monkeypatch)
    fake = FakeComfy(canvas_running=False)
    fake.running = [["OURS-1", "job-1"]]
    fake.pending = [["CANVAS-2", CANVAS_CLIENT], ["OURS-2", "job-1"]]
    assert run(_client(fake).cancel_prompt(None, "job-1")) == "interrupted"
    assert fake.interrupts == ["OURS-1"]
    assert fake.deleted == ["OURS-2"]
    assert fake.pending == [["CANVAS-2", CANVAS_CLIENT]]
    assert fake.violations == []


def test_cancel_prompt_without_any_handle_does_nothing():
    fake = FakeComfy()
    assert run(_client(fake).cancel_prompt(None, None)) == "not_found"
    assert fake.calls == []


def test_queue_state_parses_items():
    fake = FakeComfy()
    fake.pending = [["OURS-1", "job-1"]]
    state = run(_client(fake).queue_state())
    assert [(i.prompt_id, i.client_id) for i in state.running] == [(CANVAS_PROMPT, CANVAS_CLIENT)]
    assert [(i.prompt_id, i.client_id) for i in state.pending] == [("OURS-1", "job-1")]


def test_cancel_prompt_reinterrupts_a_prompt_that_started_meanwhile(monkeypatch):
    """Ours was pending at the first read and started before the delete landed."""
    _shrink_confirm_window(monkeypatch)
    fake = FakeComfy(canvas_running=False)
    fake.pending = [["OURS-1", "job-1"]]
    orig = fake.handler

    def racing(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/queue" and fake.pending:
            fake.running.append(fake.pending.pop(0))  # ComfyUI picked it up first
        return orig(request)

    client = ComfyClient("http://fakecomfy:8188", transport=httpx.MockTransport(racing))
    assert run(client.cancel_prompt("OURS-1", "job-1")) == "interrupted"
    assert fake.interrupts == ["OURS-1"]
    assert fake.violations == []


# ── The run, request-level ────────────────────────────────────────────────────


async def _setup_run(env, monkeypatch, fake: FakeComfy, n_rows: int) -> str:
    """A plan with `n_rows` rows, the router's ComfyClient wired to `fake`."""
    import backend.routers.comfy as comfy_router

    monkeypatch.setattr(
        comfy_router, "ComfyClient",
        functools.partial(ComfyClient, transport=httpx.MockTransport(fake.handler)),
    )
    r = await env.client.patch(f"{API}/settings/thresholds", json={"comfyui_url": "http://fakecomfy:8188"})
    assert r.status_code == 200, r.text
    ds = await env.create_dataset("comfy-queue-ds")
    workflow = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "x.safetensors"}},
        "9": {"class_type": "SaveImage", "inputs": {}},
    }
    r = await env.client.post(f"{API}/comfy/plans", json={
        "dataset_id": ds["id"], "name": "p", "workflow_json": workflow, "output_node_ids": ["9"],
    })
    assert r.status_code == 200, r.text
    plan_id = r.json()["id"]
    for _ in range(n_rows):
        r = await env.client.post(f"{API}/comfy/plans/{plan_id}/rows", json={"values": {}})
        assert r.status_code == 200, r.text
    return plan_id


async def _start(env, plan_id: str) -> str:
    r = await env.client.post(f"{API}/comfy/run", json={"plan_id": plan_id})
    assert r.status_code == 200, r.text
    return r.json()["job_id"]


async def _rows(env, plan_id: str) -> list[dict]:
    r = await env.client.get(f"{API}/comfy/plans/{plan_id}/rows")
    assert r.status_code == 200, r.text
    return r.json()


async def _wait_for_submit(fake: FakeComfy, n: int = 1) -> None:
    for _ in range(200):
        if len(fake.submitted) >= n:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"only {len(fake.submitted)} prompt(s) submitted")


def test_cancel_spares_the_canvas_prompt_and_deletes_ours(tmp_path, monkeypatch):
    """The canvas prompt runs, ours waits behind it: cancel must delete ours, not
    interrupt theirs (the old body-less /interrupt did exactly the opposite)."""
    _shrink_confirm_window(monkeypatch)

    async def go():
        async with api_env(tmp_path) as env:
            fake = FakeComfy()
            plan_id = await _setup_run(env, monkeypatch, fake, n_rows=2)
            jid = await _start(env, plan_id)
            await _wait_for_submit(fake)
            r = await env.client.delete(f"{API}/jobs/{jid}")
            assert r.status_code == 204, r.text
            job = await wait_for_job(env, jid)

            assert job["status"] == "cancelled", job
            assert fake.running == [[CANVAS_PROMPT, CANVAS_CLIENT]]
            assert fake.interrupts == []
            assert fake.violations == []
            assert fake.deleted == ["OURS-1"]
            assert fake.queue_ids(jid) == []
            assert [row["status"] for row in await _rows(env, plan_id)] == ["pending", "pending"]

    run(go())


def test_cancel_interrupts_our_running_prompt_by_id(tmp_path, monkeypatch):
    _shrink_confirm_window(monkeypatch)

    async def go():
        async with api_env(tmp_path) as env:
            fake = FakeComfy(canvas_running=False, ours_start_running=True)
            plan_id = await _setup_run(env, monkeypatch, fake, n_rows=2)
            jid = await _start(env, plan_id)
            await _wait_for_submit(fake)
            await env.client.delete(f"{API}/jobs/{jid}")
            job = await wait_for_job(env, jid)

            assert job["status"] == "cancelled", job
            assert fake.interrupts == ["OURS-1"]
            assert fake.violations == []
            assert fake.queue_ids() == []
            assert [row["status"] for row in await _rows(env, plan_id)] == ["pending", "pending"]

    run(go())


def test_timeout_removes_our_prompt_before_the_next_submit(tmp_path, monkeypatch):
    """A timed-out row's prompt used to stay in ComfyUI's queue and the next row was
    stacked on top of it. Now no submit ever finds a prompt of ours outstanding."""
    import backend.routers.comfy as comfy_router

    monkeypatch.setattr(comfy_router, "PER_ROW_TIMEOUT_S", 0.3)
    _shrink_confirm_window(monkeypatch)

    async def go():
        async with api_env(tmp_path) as env:
            fake = FakeComfy()
            plan_id = await _setup_run(env, monkeypatch, fake, n_rows=2)
            jid = await _start(env, plan_id)
            job = await wait_for_job(env, jid, timeout=30)

            assert job["status"] == "completed", job
            assert fake.submitted == ["OURS-1", "OURS-2"]
            assert fake.ours_outstanding_at_submit == [0, 0]
            assert fake.deleted == ["OURS-1", "OURS-2"]
            assert fake.queue_ids(jid) == []
            assert fake.running == [[CANVAS_PROMPT, CANVAS_CLIENT]]
            assert fake.interrupts == [] and fake.violations == []
            rows = await _rows(env, plan_id)
            assert [row["status"] for row in rows] == ["failed", "failed"]
            for row in rows:
                assert "timed out" in row["error_msg"]
                assert "cleanup" not in row["error_msg"]  # a clean removal adds no note

    run(go())


def test_unremovable_prompt_aborts_the_run(tmp_path, monkeypatch):
    """If our timed-out prompt cannot be confirmed gone, the next row must not be
    stacked on it: the run aborts and the remaining rows stay pending."""
    import backend.routers.comfy as comfy_router

    monkeypatch.setattr(comfy_router, "PER_ROW_TIMEOUT_S", 0.3)
    _shrink_confirm_window(monkeypatch)

    async def go():
        async with api_env(tmp_path) as env:
            fake = FakeComfy(honour_cleanup=False)
            plan_id = await _setup_run(env, monkeypatch, fake, n_rows=3)
            jid = await _start(env, plan_id)
            job = await wait_for_job(env, jid, timeout=30)

            assert job["status"] == "failed", job
            assert "Could not remove ComfyUI prompt OURS-1" in job["error_msg"]
            assert fake.submitted == ["OURS-1"]
            assert fake.violations == []
            rows = await _rows(env, plan_id)
            assert [row["status"] for row in rows] == ["failed", "pending", "pending"]
            assert "timed out" in rows[0]["error_msg"]
            assert "could not be removed" in rows[0]["error_msg"]

    run(go())


def test_connect_errors_keep_the_three_strike_breaker(tmp_path, monkeypatch):
    """ComfyUI goes away after accepting row 1's prompt. That prompt cannot be cleaned
    up ("unreachable") — which must not abort the run on its own; the
    consecutive-connect-error breaker still decides, three strikes as before."""
    _shrink_confirm_window(monkeypatch)

    async def go():
        async with api_env(tmp_path) as env:
            fake = FakeComfy(down_after_submits=1)
            plan_id = await _setup_run(env, monkeypatch, fake, n_rows=4)
            jid = await _start(env, plan_id)
            job = await wait_for_job(env, jid, timeout=30)

            assert job["status"] == "failed", job
            assert "consecutive connection failures" in job["error_msg"]
            assert fake.calls.count(("POST", "/prompt")) == comfy_service.MAX_CONSECUTIVE_CONNECT_ERRORS
            rows = await _rows(env, plan_id)
            assert [row["status"] for row in rows] == ["failed", "failed", "failed", "pending"]
            # Row 1's prompt was accepted, then its poll failed: the cleanup was
            # tried and its outcome recorded. Rows 2-3 never reached ComfyUI, so
            # there was nothing to clean and no note.
            assert "ComfyUI unreachable" in rows[0]["error_msg"]
            assert all("cleanup" not in row["error_msg"] for row in rows[1:3])

    run(go())


def test_rejected_workflow_does_no_cleanup(tmp_path, monkeypatch):
    """A 400 from /prompt queued nothing, so a broken /queue cannot abort the run."""
    async def go():
        async with api_env(tmp_path) as env:
            fake = FakeComfy(queue_status=500)
            orig = fake.handler

            def rejecting(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/prompt":
                    fake.calls.append((request.method, request.url.path))
                    return httpx.Response(400, json={"error": {"message": "bad graph"}, "node_errors": {}})
                return orig(request)

            fake.handler = rejecting
            plan_id = await _setup_run(env, monkeypatch, fake, n_rows=2)
            jid = await _start(env, plan_id)
            job = await wait_for_job(env, jid)

            assert job["status"] == "completed", job
            rows = await _rows(env, plan_id)
            assert [row["status"] for row in rows] == ["failed", "failed"]
            assert all("bad graph" in row["error_msg"] for row in rows)
            assert not any(path in ("/queue", "/interrupt") for _, path in fake.calls)

    run(go())


def test_completed_rows_never_touch_the_queue(tmp_path, monkeypatch):
    """A row whose prompt reached /history has nothing left in ComfyUI to clean."""
    async def go():
        async with api_env(tmp_path) as env:
            fake = FakeComfy(complete=True)
            plan_id = await _setup_run(env, monkeypatch, fake, n_rows=2)
            jid = await _start(env, plan_id)
            job = await wait_for_job(env, jid)

            assert job["status"] == "completed", job
            assert [row["status"] for row in await _rows(env, plan_id)] == ["completed", "completed"]
            assert not any(path in ("/queue", "/interrupt") for _, path in fake.calls)

    run(go())
