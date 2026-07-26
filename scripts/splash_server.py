#!/usr/bin/env python3
"""Startup splash server: holds :8000 while `manage start` boots the real one.

`start` has a long silent stretch before uvicorn answers — Alembic migrations,
an occasional frontend rebuild, then the `backend.main` import chain (torch and
transformers). This serves the animated Crucible mark on the app's own port for
that stretch, so the launcher can open a browser immediately on the real URL:

    splash binds 0.0.0.0:8000    ->  browser opens http://localhost:8000
    migrations + frontend build      (splash answers /api/v1/health with 503)
    splash stops, uvicorn binds  ->  page sees 200 and loads the app

`scripts/splash.html` polls `/api/v1/health` and swaps itself for the app on the
first 200. Only the real backend answers 200 — this server always answers 503 —
so that response *is* the handover signal, and the poll is same-origin, which
sidesteps CORS entirely.

Stdlib only, and it must stay that way: importing anything from `backend/` would
drag in the very import chain this exists to paper over. Run it as a child of
the launcher:

    python scripts/splash_server.py --parent-pid $$

Exit codes: 0 normal shutdown, 3 the port is already taken (the launcher then
skips the splash and lets uvicorn report the conflict itself).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "scripts" / "splash.html"
# The design source for the animated mark. The splash embeds this file's markup
# verbatim rather than transcribing the grid again — see scripts/check_mark.py,
# which keeps every copy of the mark in step and asserts this one still lands.
ANIM_HTML = ROOT / "docs" / "images" / "Crucible Logo Animated.html"
FAVICON = ROOT / "frontend" / "public" / "favicon.svg"

HEALTH_PATH = "/api/v1/health"
FAVICON_PATH = "/favicon.svg"

# Served when splash.html itself is missing. Deliberately minimal — it only has
# to keep polling, so a broken checkout still lands on the app.
FALLBACK_PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>Starting Crucible&#8230;</title></head>
<body style="background:#07090b;color:#e6edec;font-family:system-ui,sans-serif;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
<div>Starting Crucible&#8230;</div>
<script>
(function p(){fetch("/api/v1/health",{cache:"no-store"})
.then(function(r){if(r.ok)location.replace("/");else setTimeout(p,700)})
.catch(function(){setTimeout(p,700)})})();
</script></body></html>
"""


def _extract_keyframes(text: str) -> list[str]:
    """Every `@keyframes ... { ... }` block, brace-matched (they nest one level)."""
    blocks, i = [], 0
    while True:
        start = text.find("@keyframes", i)
        if start == -1:
            return blocks
        open_brace = text.find("{", start)
        if open_brace == -1:
            return blocks
        depth, k = 0, open_brace
        while k < len(text):
            if text[k] == "{":
                depth += 1
            elif text[k] == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        if k >= len(text):  # unbalanced — ignore the trailing fragment
            return blocks
        blocks.append(text[start:k + 1])
        i = k + 1


def build_page() -> str:
    """The splash HTML, with the animated mark lifted from the design source.

    Degrades rather than raising: a missing mark leaves the copy and the poll
    intact, and a missing template falls back to FALLBACK_PAGE. Neither should
    happen in a real checkout — check_mark.py fails the build if it does.
    """
    try:
        template = TEMPLATE.read_text(encoding="utf-8")
    except OSError:
        return FALLBACK_PAGE

    keyframes, mark = "", ""
    try:
        ref = ANIM_HTML.read_text(encoding="utf-8")
        keyframes = "\n".join(_extract_keyframes(ref))
        svg = re.search(r"<svg\b.*?</svg>", ref, re.S)
        mark = svg.group(0) if svg else ""
    except OSError:
        pass

    return template.replace("{{KEYFRAMES}}", keyframes).replace("{{MARK}}", mark)


class SplashHandler(BaseHTTPRequestHandler):
    # Set by main(); rendered once so every request is a memcpy.
    page: bytes = b""
    favicon: bytes | None = None

    server_version = "CrucibleSplash/1.0"

    def _send(self, status: int, body: bytes, ctype: str, *, head_only: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # Without this the browser can cache the splash against the app's own
        # URLs and serve it back later, long after the real server took over.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _route(self, *, head_only: bool) -> None:
        path = self.path.split("?", 1)[0]
        if path == HEALTH_PATH:
            body = json.dumps({"status": "starting"}).encode()
            self._send(503, body, "application/json", head_only=head_only)
        elif path == FAVICON_PATH and self.favicon is not None:
            self._send(200, self.favicon, "image/svg+xml", head_only=head_only)
        else:
            self._send(200, self.page, "text/html; charset=utf-8", head_only=head_only)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's naming
        self._route(head_only=False)

    def do_HEAD(self) -> None:  # noqa: N802
        self._route(head_only=True)

    def log_message(self, *args) -> None:
        """Silence the per-request log — the launcher's console belongs to setup."""


class SplashServer(ThreadingHTTPServer):
    # Windows' SO_REUSEADDR lets a socket bind a port another socket is already
    # *actively* bound to, so with the stdlib default the splash would silently
    # shadow a running Crucible instead of stepping aside. POSIX's SO_REUSEADDR
    # only waives TIME_WAIT, which is what we want there: a relaunch seconds
    # after the last run must still get its splash.
    allow_reuse_address = os.name != "nt"
    daemon_threads = True


def _parent_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        SYNCHRONIZE = 0x00100000
        WAIT_OBJECT_0 = 0x0
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False
        try:
            # The handle signals once the process exits.
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) != WAIT_OBJECT_0
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def _watchdog(server: ThreadingHTTPServer, parent_pid: int | None, timeout: float) -> None:
    """Free the port even if the launcher never gets to stop us.

    A hard kill of the launcher (PowerShell 5.1 skips `finally` on Ctrl+C) would
    otherwise leave this holding :8000 and break the next launch — so watch the
    parent, and keep an absolute deadline as a second backstop.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if parent_pid is not None and not _parent_alive(parent_pid):
            break
        time.sleep(2)
    server.shutdown()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # Deliberately the same address uvicorn binds. A splash on 127.0.0.1 and an
    # app on 0.0.0.0 look like two different listeners to anything forwarding
    # the port (Docker publishing, a dev container, WSL), so the handover can
    # strand a browser that reached the splash through the forward. Same address
    # in, same address out. It exposes nothing the app does not already: a
    # static page, for seconds, on a port that then serves the whole API.
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--parent-pid", type=int, default=None,
                    help="exit when this process goes away")
    ap.add_argument("--timeout", type=float, default=1800.0,
                    help="exit after this many seconds no matter what")
    args = ap.parse_args(argv)

    SplashHandler.page = build_page().encode("utf-8")
    try:
        SplashHandler.favicon = FAVICON.read_bytes()
    except OSError:
        SplashHandler.favicon = None

    try:
        server = SplashServer((args.host, args.port), SplashHandler)
    except OSError as exc:
        print(f"splash: cannot bind {args.host}:{args.port} ({exc})", file=sys.stderr)
        return 3

    threading.Thread(
        target=_watchdog, args=(server, args.parent_pid, args.timeout), daemon=True
    ).start()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
