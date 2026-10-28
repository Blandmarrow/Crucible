"""Host/Origin request guard: the API's defence against other websites (GPT-007).

The API is unauthenticated. Binding to loopback (the launchers' default) keeps other
*machines* out, but not other *websites*: any page open in the user's browser can send
requests to `http://localhost:8000`. Two attacks follow, and this middleware closes both.

- **Cross-site request forgery.** CORS only stops a foreign page *reading* a response;
  the request itself still runs. A `fetch("http://localhost:8000/api/v1/shutdown",
  {method: "POST"})` from any site shuts the server down. So an unsafe method
  (POST/PUT/PATCH/DELETE) carrying a foreign `Origin` is refused before it reaches a
  route. A request with no `Origin` at all is refused only when `Sec-Fetch-Site` says a
  browser sent it from another site — curl, scripts, httpx and Playwright's `request`
  fixture send neither header and pass.
- **DNS rebinding.** A page on `evil.example` re-points that name at 127.0.0.1, after
  which its requests are *same-origin* and it can read every response. The browser still
  sends `Host: evil.example`, so every request — any method, including the SPA and the
  SSE streams — must name a host this machine answers to: an IP literal, `localhost`,
  this machine's own hostname, or an entry in `CRUCIBLE_ALLOWED_HOSTS`.

Pure ASGI rather than `BaseHTTPMiddleware` on purpose: the latter runs the downstream
app in a separate task and pipes the body through a memory stream, which is known to
interfere with streaming responses — and the job progress bar is an SSE stream that
lives for the whole session. This class only inspects headers and either forwards the
untouched `receive`/`send` or answers 403 itself.

No DNS lookups, ever: the hostname set is `socket.gethostname()` plus string
derivations of it, so a slow resolver cannot stall a request.
"""
from __future__ import annotations

import ipaddress
import json
import socket
from urllib.parse import urlsplit

from backend.config import settings

# The browser origins the frontend is served from: the Vite dev server and the built
# app. One list, shared by `CORSMiddleware` (which may let these *read* responses) and
# this guard (which lets them *send* unsafe requests) — the two must never disagree.
CORS_ALLOWED_ORIGINS: tuple[str, ...] = ("http://localhost:5173", "http://localhost:8000")

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# `same-site` too: ports are not part of a site, so any other localhost port — another
# local dev server, a compromised tool's web UI — is "same-site" to Crucible.
_FOREIGN_FETCH_SITES = frozenset({"cross-site", "same-site"})
# A missing port, :80 and :443 compare equal. The check is scheme-agnostic, so the
# question is only "same host, same non-default port".
_DEFAULT_PORTS = frozenset({None, 80, 443})


def _norm_host(name: str) -> str:
    return name.strip().lower().rstrip(".")


def split_host_header(value: str) -> tuple[str, int | None] | None:
    """`Host` header -> (lowercased hostname, port or None); None when unparseable.

    Handles `[::1]:8000`, a bare `::1` (not valid HTTP, but unambiguous), ports, mixed
    case and a trailing dot.
    """
    value = value.strip()
    if not value:
        return None
    if value.startswith("["):
        end = value.find("]")
        if end == -1:
            return None
        host, rest = value[1:end], value[end + 1:]
        if rest and not rest.startswith(":"):
            return None
        port_text = rest[1:] if rest else ""
    elif value.count(":") > 1:
        host, port_text = value, ""          # bare IPv6 literal, no port possible
    else:
        host, _, port_text = value.partition(":")
    port: int | None = None
    if port_text:
        if not port_text.isdigit():
            return None
        port = int(port_text)
    host = _norm_host(host)
    if not host:
        return None
    return host, port


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _norm_port(port: int | None) -> int | None:
    return None if port in _DEFAULT_PORTS else port


def _norm_origin(origin: str) -> str:
    return origin.strip().lower().rstrip("/")


class RequestGuardMiddleware:
    """Reject requests whose Host is not this machine, or whose Origin is foreign."""

    def __init__(self, app, cors_origins: tuple[str, ...] = CORS_ALLOWED_ORIGINS) -> None:
        self.app = app
        self._cors_origins = frozenset(_norm_origin(o) for o in cors_origins)
        own = _norm_host(socket.gethostname() or "")
        names = {"localhost"}
        if own:
            short = own.split(".", 1)[0]
            names |= {own, f"{own}.local", short, f"{short}.local"}
        self._builtin_hosts = frozenset(n for n in names if n)
        # Recomputed only when the setting's text changes: one string compare per
        # request, and a test (or a future live-reload) that reassigns the setting is
        # honoured rather than silently cached away.
        self._configured_key: str | None = None
        self._configured: frozenset[str] = frozenset()

    def _configured_hosts(self) -> frozenset[str]:
        raw = settings.crucible_allowed_hosts or ""
        if raw != self._configured_key:
            hosts = set()
            for entry in raw.split(","):
                parsed = split_host_header(entry)
                if parsed is not None:
                    hosts.add(parsed[0])     # an entry written as host:port still counts
            self._configured = frozenset(hosts)
            self._configured_key = raw
        return self._configured

    def host_allowed(self, host: str) -> bool:
        return (
            _is_ip_literal(host)
            or host in self._builtin_hosts
            or host.endswith(".localhost")
            or host in self._configured_hosts()
        )

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers: dict[str, str] = {}
        for raw_name, raw_value in scope.get("headers") or ():
            # First occurrence wins; both headers are single-valued.
            headers.setdefault(raw_name.decode("latin-1").lower(), raw_value.decode("latin-1"))

        host_header = headers.get("host")
        parsed_host = None
        if host_header is not None and host_header.strip():
            parsed_host = split_host_header(host_header)
            if parsed_host is None or not self.host_allowed(parsed_host[0]):
                await _forbid(send, (
                    f"Host '{host_header.strip()}' is not allowed. Crucible only answers to "
                    "this machine's IP addresses, localhost and its own hostname; to reach "
                    "it under another name, add that name to CRUCIBLE_ALLOWED_HOSTS in .env "
                    "and restart."
                ))
                return

        if scope.get("method", "GET").upper() in _UNSAFE_METHODS:
            reason = self._origin_rejection(headers, parsed_host)
            if reason is not None:
                await _forbid(send, reason)
                return

        await self.app(scope, receive, send)

    def _origin_rejection(
        self, headers: dict[str, str], parsed_host: tuple[str, int | None] | None
    ) -> str | None:
        origin = headers.get("origin")
        if origin is None:
            site = headers.get("sec-fetch-site", "").strip().lower()
            if site in _FOREIGN_FETCH_SITES:
                return (
                    f"Cross-site request refused (Sec-Fetch-Site: {site}). Crucible accepts "
                    "changes only from its own pages."
                )
            return None
        origin = origin.strip()
        if origin.lower() == "null":
            return "Request from an opaque origin (Origin: null) refused."
        if _norm_origin(origin) in self._cors_origins:
            return None
        try:
            parts = urlsplit(origin)
            o_host, o_port = parts.hostname, parts.port
        except ValueError:
            o_host = None
        if o_host and parsed_host is not None:
            if (_norm_host(o_host), _norm_port(o_port)) == (parsed_host[0], _norm_port(parsed_host[1])):
                return None
        return (
            f"Cross-origin request from '{origin}' refused. Crucible accepts changes only "
            "from pages it served itself."
        )


async def _forbid(send, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": 403,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ],
    })
    await send({"type": "http.response.body", "body": body})
