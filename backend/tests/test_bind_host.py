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
    full_env = {k: v for k, v in os.environ.items() if k != "CRUCIBLE_HOST"}
    full_env.update(env)
    out = subprocess.run(
        [sys.executable, str(SCRIPT), *args], env=full_env, capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def test_cli():
    assert _run("--lan") == "0.0.0.0"
    assert _run(CRUCIBLE_HOST="10.1.2.3") == "10.1.2.3"
    assert _run("--lan", CRUCIBLE_HOST="127.0.0.1") == "0.0.0.0"
    assert _run("--urls", "8000", CRUCIBLE_HOST="127.0.0.1") == ""
    assert _run("--urls", "8000", CRUCIBLE_HOST="10.1.2.3") == "http://10.1.2.3:8000"
