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
import json

import httpx

import backend.services.comfy_service as comfy_service
from backend.services.comfy_service import ComfyClient
from backend.tests.conftest import png_bytes, run

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
    ):
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
            if self.ours_start_running and not self.running:
                self.running.append([pid, cid])
            else:
                self.pending.append([pid, cid])
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
