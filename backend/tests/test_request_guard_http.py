"""The Host/Origin guard refuses other websites and rebound names (GPT-007).

Driven through `backend.main.app`, so what is proven is the registered middleware
stack, not the class in isolation (bar the WebSocket case, which the app has no route
to reach). Requests the guard should
*let through* are checked by their normal answer (200/201), which also shows the
guard did not swallow them; requests it should refuse must 403 before any route runs.

The shutdown/restart cases never let the endpoint near the real process: `os.kill`
is replaced and both sentinels are pointed into `tmp_path` for every test in the
module, and a positive control shows that patching is what an un-guarded call hits.
"""
import os
import socket
import threading

import httpx
import pytest

import backend.main as main_mod
from backend.config import settings
from backend.request_guard import RequestGuardMiddleware, split_host_header
from backend.tests.conftest import API, api_env, run

DEV_ORIGIN = "http://localhost:5173"


@pytest.fixture(autouse=True)
def _no_real_shutdown(tmp_path, monkeypatch):
    """Every test here: a kill is recorded, never delivered; sentinels live in tmp."""
    killed = threading.Event()

    def fake_kill(pid, sig):
        killed.set()

    monkeypatch.setattr(os, "kill", fake_kill)
    monkeypatch.setattr(main_mod, "_SHUTDOWN_SENTINEL", tmp_path / ".shutdown")
    monkeypatch.setattr(main_mod, "_RESTART_SENTINEL", tmp_path / ".restart")
    return killed


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main_mod.app, raise_app_exceptions=False),
        base_url="http://localhost",
    )


async def _get(path: str, **headers) -> httpx.Response:
    async with _client() as c:
        return await c.get(path, headers=headers)


# --- Host (DNS rebinding) ---------------------------------------------------------

def test_a_rebound_hostname_is_refused_on_every_method():
    async def scenario():
        r = await _get(f"{API}/health", Host="evil.example")
        assert r.status_code == 403
        assert "evil.example" in r.json()["detail"]
        assert "CRUCIBLE_ALLOWED_HOSTS" in r.json()["detail"]
        # The SPA catch-all and unknown paths are behind it too: it is not router-scoped.
        assert (await _get("/datasets", Host="evil.example:8000")).status_code == 403
    run(scenario())


@pytest.mark.parametrize("host", [
    "localhost", "localhost:8000", "LOCALHOST.", "127.0.0.1:8000", "[::1]:8000",
    "192.168.1.20:8000", "app.localhost:8000",
])
def test_ip_literals_and_localhost_names_are_allowed(host):
    async def scenario():
        assert (await _get(f"{API}/health", Host=host)).status_code == 200
    run(scenario())


def test_this_machines_own_hostname_is_allowed():
    own = socket.gethostname()
    if not own:
        pytest.skip("no hostname")

    async def scenario():
        assert (await _get(f"{API}/health", Host=f"{own}:8000")).status_code == 200
        short = own.split(".", 1)[0]
        assert (await _get(f"{API}/health", Host=f"{short}.local:8000")).status_code == 200
    run(scenario())


def test_crucible_allowed_hosts_extends_the_list(monkeypatch):
    async def scenario():
        assert (await _get(f"{API}/health", Host="crucible.example")).status_code == 403
        monkeypatch.setattr(settings, "crucible_allowed_hosts", " Crucible.Example , nas.lan:8000")
        assert (await _get(f"{API}/health", Host="crucible.example:8000")).status_code == 200
        assert (await _get(f"{API}/health", Host="nas.lan")).status_code == 200
        assert (await _get(f"{API}/health", Host="evil.example")).status_code == 403
        # The parsed set follows the setting, not the first value it saw.
        monkeypatch.setattr(settings, "crucible_allowed_hosts", "")
        assert (await _get(f"{API}/health", Host="crucible.example")).status_code == 403
    run(scenario())


def test_split_host_header():
    assert split_host_header("[::1]:8000") == ("::1", 8000)
    assert split_host_header("::1") == ("::1", None)
    assert split_host_header("Example.COM.:80") == ("example.com", 80)
    assert split_host_header("localhost:abc") is None
    assert split_host_header("[::1") is None


# --- Origin (CSRF) ----------------------------------------------------------------

def test_cross_site_and_same_origin_posts(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            url = f"{API}/datasets/"
            r = await env.client.post(url, json={"name": "a"}, headers={"Origin": "https://evil.example"})
            assert r.status_code == 403
            assert "evil.example" in r.json()["detail"]

            r = await env.client.post(url, json={"name": "b"}, headers={"Origin": "null"})
            assert r.status_code == 403

            r = await env.client.post(url, json={"name": "c"}, headers={"Sec-Fetch-Site": "cross-site"})
            assert r.status_code == 403

            # Same origin (scheme-agnostic, default ports normalised): the built app.
            r = await env.client.post(url, json={"name": "d"}, headers={"Origin": "http://localhost"})
            assert r.status_code == 201, r.text
            r = await env.client.post(
                url, json={"name": "e"},
                headers={"Host": "192.168.1.20:8000", "Origin": "http://192.168.1.20:8000"},
            )
            assert r.status_code == 201, r.text
            # Same host, different port is a different origin.
            r = await env.client.post(
                url, json={"name": "f"},
                headers={"Host": "192.168.1.20:8000", "Origin": "http://192.168.1.20:9000"},
            )
            assert r.status_code == 403

            # The Vite dev server: its proxy forwards the browser's own Host
            # (changeOrigin: false), so Host and Origin agree and it is same-origin.
            r = await env.client.post(
                url, json={"name": "g"}, headers={"Host": "localhost:5173", "Origin": DEV_ORIGIN},
            )
            assert r.status_code == 201, r.text
            # ...and :5173 earns nothing beyond that. Whatever else listens there (any
            # other project's Vite server, by default) is a foreign origin to :8000.
            r = await env.client.post(
                url, json={"name": "x"}, headers={"Host": "localhost:8000", "Origin": DEV_ORIGIN},
            )
            assert r.status_code == 403

            # No browser headers at all (curl, scripts, Playwright's request fixture).
            r = await env.client.post(url, json={"name": "h"})
            assert r.status_code == 201, r.text
            r = await env.client.post(url, json={"name": "i"}, headers={"Sec-Fetch-Site": "same-origin"})
            assert r.status_code == 201, r.text

            # Reads are not guarded by Origin: with no CORS headers, the browser's
            # same-origin policy already stops a foreign page reading them.
            r = await env.client.get(url, headers={"Origin": "https://evil.example"})
            assert r.status_code == 200

            names = {d["name"] for d in (await env.client.get(url)).json()}
            assert names == {"d", "e", "g", "h", "i"}
    run(scenario())


def test_no_origin_is_granted_cross_origin_reads():
    """No CORS: no origin, the old dev allowlist included, may read a response."""
    async def scenario():
        async with _client() as c:
            for origin in (DEV_ORIGIN, "https://evil.example"):
                r = await c.get(f"{API}/health", headers={"Host": "localhost:8000", "Origin": origin})
                assert r.status_code == 200
                assert "access-control-allow-origin" not in r.headers
                r = await c.options(f"{API}/datasets/", headers={
                    "Host": "localhost:8000",
                    "Origin": origin,
                    "Access-Control-Request-Method": "POST",
                })
                assert "access-control-allow-origin" not in r.headers
    run(scenario())


def _ws_handshake(headers: dict[str, str]):
    """Run one WebSocket handshake through the guard; -> (reached_app, messages sent)."""
    reached: list[bool] = []
    sent: list[dict] = []

    async def inner(scope, receive, send):
        reached.append(True)

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "websocket",
        "path": "/ws",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }
    run(RequestGuardMiddleware(inner)(scope, receive, send))
    return bool(reached), sent


def test_websocket_handshakes_get_both_checks():
    """CORS never covered WebSockets, so the guard must: a refused handshake is closed
    before accept (the server turns that into a 403), never handed to the app."""
    for headers in (
        {"Host": "evil.example"},
        {"Host": "localhost:8000", "Origin": "https://evil.example"},
        {"Host": "localhost:8000", "Origin": DEV_ORIGIN},
        {"Host": "localhost:8000", "Origin": "null"},
    ):
        reached, sent = _ws_handshake(headers)
        assert not reached, headers
        assert sent and sent[0]["type"] == "websocket.close" and sent[0]["code"] == 1008, headers

    for headers in (
        {"Host": "localhost:8000", "Origin": "http://localhost:8000"},
        {"Host": "localhost:8000"},                     # a non-browser client
    ):
        reached, sent = _ws_handshake(headers)
        assert reached and not sent, headers


def test_shutdown_and_restart_from_a_foreign_origin_do_nothing(_no_real_shutdown, tmp_path):
    killed = _no_real_shutdown

    async def scenario():
        async with _client() as c:
            for path in ("shutdown", "restart"):
                r = await c.post(f"{API}/{path}", headers={"Origin": "https://evil.example"})
                assert r.status_code == 403
                r = await c.post(f"{API}/{path}", headers={"Sec-Fetch-Site": "cross-site"})
                assert r.status_code == 403
    run(scenario())
    assert not killed.wait(0.5)
    assert not (tmp_path / ".shutdown").exists()
    assert not (tmp_path / ".restart").exists()


def test_positive_control_an_unguarded_shutdown_hits_the_patched_kill(_no_real_shutdown, tmp_path):
    """Proves the test above could fail: the same call without a foreign Origin goes through."""
    killed = _no_real_shutdown

    async def scenario():
        async with _client() as c:
            r = await c.post(f"{API}/shutdown", headers={"Origin": "http://localhost"})
            assert r.status_code == 204
    run(scenario())
    assert killed.wait(5)
    assert (tmp_path / ".shutdown").exists()
