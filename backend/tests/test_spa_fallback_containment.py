"""`main.py`'s SPA catch-all is a `FileResponse` over raw client input.

The path-traversal invariant is usually discussed in terms of *stored* paths and
`settings.datasets_dir` (see `test_path_containment_http.py`). This is the other
kind: `spa_fallback` serves real files out of `frontend/dist` for a path the
client types, so it needs the same containment guard against a different tree.
It never had one, and the two vectors below both read arbitrary files —
`/etc/passwd` and the whole `dataset_manager.db` were retrievable from an
unauthenticated, `0.0.0.0`-bound server. See PM-015.

Why the obvious "there is no `..` in a real request" reasoning is wrong:

- **`%2e%2e`**. Well-behaved clients normalize literal `../` away before the
  request leaves — which is why this hid so long — but percent-encoded dots
  survive that normalization, and Starlette percent-decodes the `{full_path:path}`
  parameter *after* routing. The handler receives `..`.
- **A leading slash**. `Path.__truediv__` discards the left operand entirely when
  the right is absolute, so `frontend_dist / "/etc/passwd"` **is** `/etc/passwd`.
  Sent as `//etc/passwd`, that vector needs no dots at all.

Both must land on `index.html` — the SPA fallback is the right answer for a path
that names nothing inside dist, and it is also what a legitimate React Router
route gets, so the guard costs no real behaviour.

The real route is only registered when `frontend/dist` exists, and no CI job that
runs pytest builds it — so this module used to skip on every CI run, leaving the
PM-015 regression tests enforced nowhere (GPT-003, PM-028). It now drives
`main.mount_spa` against a throwaway `dist` under `tmp_path` instead, with a
`secret.md` beside it standing in for the file an escape would read. Nothing here
depends on a build, so it runs everywhere pytest does. Never reinstate a skip on
a built artifact for a security regression test.
"""

import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from backend.main import mount_spa

INDEX_HTML = b"<!doctype html><title>spa index</title>"
FAVICON = b"<svg>favicon</svg>"
SECRET = b"# secret outside dist"


@pytest.fixture
def spa(tmp_path: Path):
    """A throwaway built-SPA tree and an app serving it.

    `secret.md` sits *beside* `dist`, one `..` up — the same shape as the real
    repo, where `frontend/dist/../../CLAUDE.md` is the file an escape would read.
    """
    root = tmp_path / "root"
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_bytes(INDEX_HTML)
    (dist / "favicon.svg").write_bytes(FAVICON)
    secret = root / "secret.md"
    secret.write_bytes(SECRET)

    app = FastAPI()
    mount_spa(app, dist)
    return app, secret


def run(coro):
    return asyncio.run(coro)


async def _get(app: FastAPI, path: str) -> httpx.Response:
    """One GET against the app.

    httpx percent-decodes into ASGI `scope["path"]` exactly as uvicorn does, which
    is what makes it the right transport for the `%2e%2e` vector.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


async def _get_raw_path(app: FastAPI, path: str) -> tuple[int, bytes]:
    """Drive the ASGI app with `scope["path"]` set verbatim.

    Needed for the leading-slash vector specifically: httpx parses `//foo` as a
    URL *authority* and never sends it as a path, so routing it through
    `_get` silently tests nothing — it passed against the unguarded handler.
    uvicorn has no such reinterpretation; a raw `GET //etc/passwd HTTP/1.1`
    arrives here as `scope["path"] == "//etc/passwd"`, which is what this builds.

    It has to behave like a real server, not just a source of one request
    (GPT-005). Starlette ≥1.8's `FileResponse` runs a `while True: await
    receive()` disconnect listener alongside the body send; a `receive()` that
    hands back `http.request` on every call never yields to it, so the response
    starves and the test hangs instead of failing. So `receive()` delivers the
    request once, then blocks until the response is complete and reports the
    disconnect — what uvicorn does — and the scope declares the `spec_version`
    a real server sends. The `wait_for` makes any future protocol mismatch a
    failure rather than a hung CI job.
    """
    status: dict[str, int] = {}
    chunks: list[bytes] = []
    request_sent = False
    response_done = asyncio.Event()

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await response_done.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            status["code"] = message["status"]
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                response_done.set()

    await asyncio.wait_for(app(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"test")],
            "client": ("1.2.3.4", 9999),
            "server": ("test", 80),
        },
        receive,
        send,
    ), timeout=10)
    return status["code"], b"".join(chunks)


def test_percent_encoded_dots_do_not_escape_the_dist_directory(spa):
    """`/%2e%2e/secret.md` must not return the file beside dist."""
    app, secret = spa

    r = run(_get(app, "/%2e%2e/secret.md"))

    assert r.status_code == 200
    assert r.content == INDEX_HTML
    assert SECRET not in r.content


def test_an_absolute_path_does_not_replace_the_dist_root(spa):
    """The `Path("dist") / "/abs"` == `Path("/abs")` vector, which needs no dots.

    Goes through `_get_raw_path`, not the httpx client — see that helper for why
    the client cannot express this one. The request path is derived from the
    target's own absolute location rather than hardcoded, so it reads the same on
    Windows (`/C:/…`) as on POSIX (`//tmp/…`).
    """
    app, secret = spa

    status, body = run(_get_raw_path(app, f"/{secret.as_posix()}"))

    assert status == 200
    assert body == INDEX_HTML
    assert SECRET not in body


def test_a_real_file_inside_dist_is_still_served(spa):
    """The guard must not turn the catch-all into a blanket index.html.

    `main.py` serves the unhashed root files (`favicon.svg`, `apple-touch-icon.png`,
    …) through this handler; only the content-hashed `/assets/*` bundles go through
    the `StaticFiles` mount. Breaking this branch is a silent regression — every
    request still answers 200.
    """
    app, _ = spa

    r = run(_get(app, "/favicon.svg"))

    assert r.status_code == 200
    assert r.content == FAVICON


def test_an_unknown_route_still_falls_through_to_the_spa(spa):
    """React Router owns unmatched paths — the guard must not change that."""
    app, _ = spa

    r = run(_get(app, "/gallery"))

    assert r.status_code == 200
    assert r.content == INDEX_HTML
