#!/usr/bin/env python3
"""Print the address the launchers bind uvicorn (and the splash) to (GPT-007).

Crucible's API has no authentication, so it listens on loopback unless the user
opts in to LAN access. The launchers call this before starting anything:

    python scripts/bind_host.py [--lan]             -> prints the bind host
    python scripts/bind_host.py [--lan] --urls PORT -> prints the http:// URL(s) other
                                                       devices can use, one per line;
                                                       nothing for a loopback host
    python scripts/bind_host.py [--lan] --describe  -> three lines: the bind host; the
                                                       host a browser on this machine
                                                       (and the Vite proxy) must use;
                                                       `loopback` or `lan`

The launchers read `--describe` and decide nothing themselves: every loopback test and
"where do I point the browser" rule lives here, so the two shells cannot drift from it
or from each other. The browse host is `localhost` for a loopback or wildcard bind, and
the bind address itself otherwise — a server bound to one LAN address does not answer on
localhost at all.

Precedence, first match wins:
    1. --lan                                  -> 0.0.0.0
    2. the CRUCIBLE_HOST environment variable
    3. a CRUCIBLE_HOST= line in the repo-root .env
    4. 127.0.0.1

An empty value at step 2 or 3 counts as unset. Stdlib only, and it must stay that way:
`setup` and `start` run it before (or regardless of whether) the backend's packages are
installed, and importing `backend.config` would drag in pydantic. The .env reader is
deliberately small — `KEY=value`, optional `export `, quotes, `#` comments — and the
backend's own `Settings.crucible_host` exists only so its unknown-key check stays quiet.

The LAN URL comes from the UDP-connect trick: connecting a datagram socket sends no
packet but makes the OS pick the outbound interface, whose address is then readable.
No DNS lookups. When it fails (no network) nothing is printed for that address.
"""
from __future__ import annotations

import argparse
import ipaddress
import os
import socket
import sys
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
LAN_HOST = "0.0.0.0"
ENV_KEY = "CRUCIBLE_HOST"
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def _clean(value: str) -> str:
    value = value.strip()
    if value[:1] in ("'", '"'):
        # A quoted value ends at its closing quote; anything after it (an inline
        # `# comment`) is not part of the value, as python-dotenv reads it.
        end = value.find(value[0], 1)
        if end != -1:
            return value[1:end].strip()
    # An unquoted value ends at an inline comment.
    return value.split(" #", 1)[0].split("\t#", 1)[0].strip()


def read_env_file(path: Path, key: str = ENV_KEY) -> str:
    """The last non-empty `key=` value in a .env file, or ''."""
    found = ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, _, value = line.partition("=")
        if name.strip().upper() == key:
            found = _clean(value)
    return found


def resolve_host(lan: bool, environ=os.environ, env_file: Path = ENV_FILE) -> str:
    if lan:
        return LAN_HOST
    value = _clean(environ.get(ENV_KEY, ""))
    if value:
        return value
    return read_env_file(env_file) or DEFAULT_HOST


def is_loopback(host: str) -> bool:
    if host.lower().rstrip(".") == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def _is_unspecified(host: str) -> bool:
    try:
        return ipaddress.ip_address(host.strip("[]")).is_unspecified
    except ValueError:
        return False  # a hostname


def _url_host(host: str) -> str:
    bare = host.strip("[]")
    return f"[{bare}]" if ":" in bare else bare


def browse_host(host: str) -> str:
    """Where a browser on this machine reaches a server bound to `host`."""
    if is_loopback(host) or _is_unspecified(host):
        return "localhost"
    return _url_host(host)


def _primary_ip() -> str | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Never reached: a UDP connect only selects a route.
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    return None if ip.startswith(("0.", "127.")) else ip


def lan_urls(host: str, port: int) -> list[str]:
    if is_loopback(host):
        return []
    if not _is_unspecified(host):
        return [f"http://{_url_host(host)}:{port}"]   # a specific address or hostname
    ip = _primary_ip()
    return [f"http://{ip}:{port}"] if ip else []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lan", action="store_true", help="bind every interface (0.0.0.0)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--urls", type=int, metavar="PORT",
                      help="print the LAN URL(s) for PORT instead of the host")
    mode.add_argument("--describe", action="store_true",
                      help="print the bind host, the browse host and loopback|lan")
    args = ap.parse_args(argv)
    host = resolve_host(args.lan)
    if args.describe:
        print(host)
        print(browse_host(host))
        print("loopback" if is_loopback(host) else "lan")
    elif args.urls is not None:
        for url in lan_urls(host, args.urls):
            print(url)
    else:
        print(host)
    return 0


if __name__ == "__main__":
    sys.exit(main())
