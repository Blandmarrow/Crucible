"""Request-level tests for `/system`, the sidebar hardware meters.

`GET /system/gpu` tries NVIDIA, then ROCm, then MPS, and falls back to a null
row. Each probe shells out (`nvidia-smi`, `rocm-smi`) or imports torch, so on
any given machine at most one branch is reachable and the untaken ones are dead
code as far as the suite is concerned — which is how a probe order can silently
invert, or a fallback stop returning the shape the sidebar destructures.

The three probes are patched on the router module, so no subprocess is spawned
and no torch import happens. `_NULL` is asserted by value, not by identity:
the frontend reads `used_mb`/`total_mb` as numbers and treats
`utilization_pct: null` as "unknown", so the keys are the contract.

`GET /system/cpu-ram` runs against real psutil — it is cheap, always available,
and its `except` branch would hide a genuine breakage behind zeros, so the test
asserts the live values are sane rather than merely well-shaped.
"""
import pytest

from backend.tests.conftest import API, api_env, run

NVIDIA = {"name": "NVIDIA GeForce RTX 4090", "used_mb": 2048, "total_mb": 24564, "utilization_pct": 8.3}
ROCM = {"name": "card0", "used_mb": 512, "total_mb": 16384, "utilization_pct": 3.1}
MPS = {"name": "Apple Silicon (MPS)", "used_mb": 128, "total_mb": 4096, "utilization_pct": None}


def _probes(monkeypatch, nvidia=None, rocm=None, mps=None):
    """Patch all three GPU probes; return the list recording which ones ran."""
    import backend.routers.system as system_router

    order: list[str] = []

    def _stub(name, value):
        async def fn():
            order.append(name)
            return value
        return fn

    monkeypatch.setattr(system_router, "_nvidia_stats", _stub("nvidia", nvidia))
    monkeypatch.setattr(system_router, "_rocm_stats", _stub("rocm", rocm))
    monkeypatch.setattr(system_router, "_mps_stats", _stub("mps", mps))
    return order


async def _gpu(env) -> dict:
    r = await env.client.get(f"{API}/system/gpu")
    assert r.status_code == 200, r.text
    return r.json()


def test_gpu_prefers_nvidia_and_stops_probing(tmp_path, monkeypatch):
    order = _probes(monkeypatch, nvidia=NVIDIA, rocm=ROCM, mps=MPS)

    async def scenario():
        async with api_env(tmp_path) as env:
            assert await _gpu(env) == NVIDIA

    run(scenario())
    assert order == ["nvidia"]  # a hit must not spawn the later probes


def test_gpu_falls_back_to_rocm_then_mps(tmp_path, monkeypatch):
    order = _probes(monkeypatch, nvidia=None, rocm=ROCM, mps=MPS)

    async def scenario():
        async with api_env(tmp_path) as env:
            assert await _gpu(env) == ROCM

    run(scenario())
    assert order == ["nvidia", "rocm"]

    order = _probes(monkeypatch, nvidia=None, rocm=None, mps=MPS)

    async def mps_scenario():
        async with api_env(tmp_path) as env:
            assert await _gpu(env) == MPS

    run(mps_scenario())
    assert order == ["nvidia", "rocm", "mps"]


def test_gpu_with_no_accelerator_returns_the_null_row(tmp_path, monkeypatch):
    order = _probes(monkeypatch)  # every probe returns None

    async def scenario():
        async with api_env(tmp_path) as env:
            body = await _gpu(env)
            assert body == {"name": None, "used_mb": 0, "total_mb": 0, "utilization_pct": None}

    run(scenario())
    assert order == ["nvidia", "rocm", "mps"]


def test_mps_probe_never_imports_torch_off_macos(monkeypatch):
    """The probe that used to freeze the app for ~14 s on every non-NVIDIA start.

    `_mps_stats` is the last link in the chain, so it runs on exactly the
    machines with neither nvidia-smi nor rocm-smi — and `import torch` there is a
    cold, multi-second, GIL-holding import that blocked the whole event loop, not
    just its own request. A `sys.modules` entry that explodes on attribute access
    is the assertion: reaching the import at all fails the test.

    The landmine raises a *BaseException* on purpose. The probe body is wrapped
    in `except Exception`, so an ordinary error would be swallowed into the same
    `None` the guard returns and this test would pass with the guard deleted.
    """
    import asyncio
    import sys as _sys

    import backend.routers.system as system_router

    class _TorchTouched(BaseException):
        pass

    class _Landmine:
        def __getattr__(self, name):
            raise _TorchTouched(f"torch was imported off macOS (accessed .{name})")

    monkeypatch.setitem(_sys.modules, "torch", _Landmine())

    for platform in ("linux", "win32"):
        monkeypatch.setattr(_sys, "platform", platform)
        assert asyncio.run(system_router._mps_stats()) is None

    # On macOS the import does happen (in an executor, off the loop) — proving
    # the landmine is armed, and that the two cases above returned early rather
    # than merely failing quietly.
    monkeypatch.setattr(_sys, "platform", "darwin")
    with pytest.raises(_TorchTouched):
        asyncio.run(system_router._mps_stats())


def test_cpu_ram_reports_live_numbers(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.get(f"{API}/system/cpu-ram")
            assert r.status_code == 200, r.text
            body = r.json()
            assert set(body) == {"cpu_pct", "ram_used_mb", "ram_total_mb"}
            assert 0.0 <= body["cpu_pct"] <= 100.0
            # A zeroed total is the `except` branch, i.e. psutil failed.
            assert body["ram_total_mb"] > 0
            assert 0 < body["ram_used_mb"] <= body["ram_total_mb"]

    run(scenario())
