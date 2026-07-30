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

The route is only registered when `frontend/dist` exists (`main.py` guards the
whole block on it), so this module skips on a backend-only checkout — the same
convention `needs_cv2` uses in `conftest.py`. `qa-smoke` builds the frontend
before pytest, so the sweep runs it.
"""

import asyncio
from pathlib import Path

import httpx
import pytest

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    not (FRONTEND_DIST / "index.html").is_file(),
    reason="frontend/dist is not built, so the SPA catch-all route is not registered",
)


def run(coro):
    return asyncio.run(coro)


async def _get(path: str) -> httpx.Response:
    """One GET against the real app. No DB and no job queue — the route needs neither.

    httpx percent-decodes into ASGI `scope["path"]` exactly as uvicorn does, which
    is what makes it the right transport for the `%2e%2e` vector.
    """
    from backend.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


async def _get_raw_path(path: str) -> tuple[int, bytes]:
    """Drive the ASGI app with `scope["path"]` set verbatim.

    Needed for the leading-slash vector specifically: httpx parses `//foo` as a
    URL *authority* and never sends it as a path, so routing it through
    `_get` silently tests nothing — it passed against the unguarded handler.
    uvicorn has no such reinterpretation; a raw `GET //etc/passwd HTTP/1.1`
    arrives here as `scope["path"] == "//etc/passwd"`, which is what this builds.
    """
    from backend.main import app

    status: dict[str, int] = {}
    chunks: list[bytes] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            status["code"] = message["status"]
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
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
    )
    return status["code"], b"".join(chunks)


def _index_bytes() -> bytes:
    return (FRONTEND_DIST / "index.html").read_bytes()


def test_percent_encoded_dots_do_not_escape_the_dist_directory():
    """`/%2e%2e/%2e%2e/CLAUDE.md` must not return CLAUDE.md.

    Two levels up from `frontend/dist` is the repo root, so the target is a real
    file that exists on every checkout — the read succeeds if the guard is gone.
    """
    target = REPO_ROOT / "CLAUDE.md"
    assert target.is_file(), "fixture assumption: CLAUDE.md sits at the repo root"

    r = run(_get("/%2e%2e/%2e%2e/CLAUDE.md"))

    assert r.status_code == 200
    assert r.content == _index_bytes()
    assert r.content != target.read_bytes()
    assert b"# CLAUDE.md" not in r.content


def test_an_absolute_path_does_not_replace_the_dist_root():
    """The `Path("dist") / "/abs"` == `Path("/abs")` vector, which needs no dots.

    Goes through `_get_raw_path`, not the httpx client — see that helper for why
    the client cannot express this one. The request path is derived from the
    target's own absolute location rather than hardcoded, so it reads the same on
    Windows (`/C:/…`) as on POSIX (`//workspaces/…`).
    """
    target = REPO_ROOT / "CLAUDE.md"

    status, body = run(_get_raw_path(f"/{target.as_posix()}"))

    assert status == 200
    assert body == _index_bytes()
    assert b"# CLAUDE.md" not in body


def test_a_real_file_inside_dist_is_still_served():
    """The guard must not turn the catch-all into a blanket index.html.

    `main.py` serves the unhashed root files (`favicon.svg`, `apple-touch-icon.png`,
    …) through this handler; only the content-hashed `/assets/*` bundles go through
    the `StaticFiles` mount. Breaking this branch is a silent regression — every
    request still answers 200.
    """
    candidates = [
        p for p in FRONTEND_DIST.iterdir()
        if p.is_file() and p.name != "index.html"
    ]
    if not candidates:
        pytest.skip("this build has no unhashed root file besides index.html")
    served = candidates[0]

    r = run(_get(f"/{served.name}"))

    assert r.status_code == 200
    assert r.content == served.read_bytes()


def test_an_unknown_route_still_falls_through_to_the_spa():
    """React Router owns unmatched paths — the guard must not change that."""
    r = run(_get("/gallery"))

    assert r.status_code == 200
    assert r.content == _index_bytes()
