"""scripts/bind_host.py: the launchers' bind address and its precedence (GPT-007).

`--lan` > `CRUCIBLE_HOST` in the environment > a `CRUCIBLE_HOST=` line in .env >
127.0.0.1. The function-level tests point the .env lookup at `tmp_path`; the
subprocess tests run the script the way the launchers do, with the repo's real .env
path, so they only assert on cases the environment variable or `--lan` decides.
"""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "bind_host.py"
_spec = importlib.util.spec_from_file_location("bind_host", SCRIPT)
bind_host = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bind_host)


def _env_file(tmp_path: Path, text: str) -> Path:
    p = tmp_path / ".env"
    p.write_text(text, encoding="utf-8")
    return p


def test_default_is_loopback(tmp_path):
    assert bind_host.resolve_host(False, {}, tmp_path / "missing.env") == "127.0.0.1"


def test_env_file_line_is_read(tmp_path):
    f = _env_file(tmp_path, "# CRUCIBLE_HOST=1.1.1.1\nHF_TOKEN=x\n\nCRUCIBLE_HOST=\"0.0.0.0\"  \n")
    assert bind_host.resolve_host(False, {}, f) == "0.0.0.0"
    f = _env_file(tmp_path, "export crucible_host=192.168.1.5 # my NIC\n")
    assert bind_host.resolve_host(False, {}, f) == "192.168.1.5"
    # An empty line is "unset", not "bind to nothing".
    f = _env_file(tmp_path, "CRUCIBLE_HOST=\n")
    assert bind_host.resolve_host(False, {}, f) == "127.0.0.1"


def test_environment_beats_env_file_and_lan_beats_both(tmp_path):
    f = _env_file(tmp_path, "CRUCIBLE_HOST=10.0.0.2\n")
    assert bind_host.resolve_host(False, {"CRUCIBLE_HOST": "10.0.0.3"}, f) == "10.0.0.3"
    assert bind_host.resolve_host(False, {"CRUCIBLE_HOST": " "}, f) == "10.0.0.2"
    assert bind_host.resolve_host(True, {"CRUCIBLE_HOST": "127.0.0.1"}, f) == "0.0.0.0"


def test_lan_urls():
    assert bind_host.lan_urls("127.0.0.1", 8000) == []
    assert bind_host.lan_urls("::1", 8000) == []
    assert bind_host.lan_urls("localhost", 8000) == []
    assert bind_host.lan_urls("192.168.1.5", 8000) == ["http://192.168.1.5:8000"]
    urls = bind_host.lan_urls("0.0.0.0", 5173)   # depends on the machine's network
    assert all(u.startswith("http://") and u.endswith(":5173") for u in urls)
    assert not any("127." in u or "0.0.0.0" in u for u in urls)


def _run(*args, **env):
    full_env = {k: v for k, v in os.environ.items() if k not in ("CRUCIBLE_HOST", "CRUCIBLE_PORT", "CRUCIBLE_VITE_PORT")}
    full_env.update(env)
    out = subprocess.run(
        [sys.executable, str(SCRIPT), *args], env=full_env, capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def _run_raw(*args, **env):
    full_env = {k: v for k, v in os.environ.items()
                if k not in ("CRUCIBLE_HOST", "CRUCIBLE_PORT", "CRUCIBLE_VITE_PORT")}
    full_env.update(env)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], env=full_env, capture_output=True, text=True,
    )


def test_cli():
    assert _run("--lan") == "0.0.0.0"
    assert _run(CRUCIBLE_HOST="10.1.2.3") == "10.1.2.3"
    assert _run("--lan", CRUCIBLE_HOST="127.0.0.1") == "0.0.0.0"
    assert _run("--urls", "8000", CRUCIBLE_HOST="127.0.0.1") == ""
    assert _run("--urls", "8000", CRUCIBLE_HOST="10.1.2.3") == "http://10.1.2.3:8000"


def test_a_quoted_value_ends_at_its_closing_quote(tmp_path):
    # python-dotenv reads this as 0.0.0.0; keeping the quotes made uvicorn's bind fail.
    f = _env_file(tmp_path, 'CRUCIBLE_HOST="0.0.0.0" # lan\n')
    assert bind_host.resolve_host(False, {}, f) == "0.0.0.0"
    f = _env_file(tmp_path, "CRUCIBLE_HOST='10.0.0.7'   # this NIC\n")
    assert bind_host.resolve_host(False, {}, f) == "10.0.0.7"


def test_browse_host_follows_the_bind():
    """A wildcard or loopback bind answers on localhost; one LAN address does not."""
    for host in ("127.0.0.1", "0.0.0.0", "::", "::1", "0:0:0:0:0:0:0:1", "localhost", "localhost."):
        assert bind_host.browse_host(host) == "localhost", host
    assert bind_host.browse_host("192.168.1.5") == "192.168.1.5"
    assert bind_host.browse_host("fe80::1") == "[fe80::1]"
    assert bind_host.browse_host("mybox") == "mybox"


def test_describe_is_the_launchers_one_answer():
    """The shells read these five lines and keep no loopback test of their own."""
    ports = {"CRUCIBLE_PORT": "8000", "CRUCIBLE_VITE_PORT": "5173"}
    assert _run("--describe", CRUCIBLE_HOST="127.0.0.1", **ports).splitlines() == [
        "127.0.0.1", "localhost", "loopback", "8000", "5173",
    ]
    assert _run("--describe", "--lan", **ports).splitlines() == [
        "0.0.0.0", "localhost", "lan", "8000", "5173",
    ]
    assert _run("--describe", CRUCIBLE_HOST="192.168.1.5", **ports).splitlines() == [
        "192.168.1.5", "192.168.1.5", "lan", "8000", "5173",
    ]
    # A loopback spelling the old shell patterns missed.
    assert _run("--describe", CRUCIBLE_HOST="0:0:0:0:0:0:0:1", **ports).splitlines()[2] == "loopback"
    # The ports come from the environment, last two lines.
    assert _run("--describe", CRUCIBLE_HOST="127.0.0.1", CRUCIBLE_PORT="8100",
                CRUCIBLE_VITE_PORT="5200").splitlines()[3:] == ["8100", "5200"]


def test_ports_default(tmp_path):
    missing = tmp_path / "missing.env"
    assert bind_host.resolve_port({}, missing) == 8000
    assert bind_host.resolve_vite_port({}, missing) == 5173


def test_port_precedence_env_beats_file_beats_default(tmp_path):
    f = _env_file(tmp_path, "CRUCIBLE_PORT=8100\nCRUCIBLE_VITE_PORT=5200\nCRUCIBLE_PORT=8101\n")
    assert bind_host.resolve_port({}, f) == 8101          # the last line wins
    assert bind_host.resolve_vite_port({}, f) == 5200
    assert bind_host.resolve_port({"CRUCIBLE_PORT": "9000"}, f) == 9000
    assert bind_host.resolve_vite_port({"CRUCIBLE_VITE_PORT": "9001"}, f) == 9001


def test_empty_port_counts_as_unset(tmp_path):
    f = _env_file(tmp_path, "CRUCIBLE_PORT=8100\nCRUCIBLE_VITE_PORT=\n")
    assert bind_host.resolve_port({"CRUCIBLE_PORT": "  "}, f) == 8100
    assert bind_host.resolve_vite_port({"CRUCIBLE_VITE_PORT": ""}, f) == 5173


def test_invalid_port_fails_loudly(tmp_path):
    missing = tmp_path / "missing.env"
    for bad in ("abc", "0", "65536", "-1", "80.5", "8000x"):
        with pytest.raises(ValueError, match="CRUCIBLE_PORT"):
            bind_host.resolve_port({"CRUCIBLE_PORT": bad}, missing)
        with pytest.raises(ValueError, match="CRUCIBLE_VITE_PORT"):
            bind_host.resolve_vite_port({"CRUCIBLE_VITE_PORT": bad}, missing)
    f = _env_file(tmp_path, "CRUCIBLE_PORT=nope\n")
    with pytest.raises(ValueError):
        bind_host.resolve_port({}, f)
    assert bind_host.resolve_port({"CRUCIBLE_PORT": "1"}, missing) == 1
    assert bind_host.resolve_port({"CRUCIBLE_PORT": "65535"}, missing) == 65535


def test_describe_rejects_a_bad_port():
    r = _run_raw("--describe", CRUCIBLE_PORT="eighty")
    assert r.returncode == 2 and r.stdout == ""
    assert "CRUCIBLE_PORT" in r.stderr
