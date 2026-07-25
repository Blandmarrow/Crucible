import asyncio
import sys

import psutil
from fastapi import APIRouter

router = APIRouter(prefix="/system", tags=["system"])

_NULL = {"name": None, "used_mb": 0, "total_mb": 0, "utilization_pct": None}


async def _nvidia_stats() -> dict | None:
    """Query nvidia-smi for NVIDIA GPU stats. Returns None if unavailable."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "nvidia-smi",
            "--query-gpu=name,memory.used,memory.total",
            "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        if proc.returncode != 0:
            return None
        line = stdout.decode().strip().splitlines()[0]
        name, used_str, total_str = [s.strip() for s in line.split(",")]
        used_mb = int(used_str)
        total_mb = int(total_str)
        utilization_pct = round(used_mb / total_mb * 100, 1) if total_mb else None
        return {"name": name, "used_mb": used_mb, "total_mb": total_mb, "utilization_pct": utilization_pct}
    except Exception:
        return None


async def _rocm_stats() -> dict | None:
    """
    Query rocm-smi for AMD GPU stats (Linux only).
    ROCm 6.x CSV format:
      device,VRAM Total Memory (B),VRAM Total Used Memory (B)
      card0,<total_bytes>,<used_bytes>
    Returns None if rocm-smi is absent or parsing fails.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "rocm-smi",
            "--showmeminfo", "vram",
            "--csv",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        if proc.returncode != 0:
            return None
        lines = stdout.decode().strip().splitlines()
        # Skip header line; parse first data row
        for line in lines[1:]:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                total_bytes = int(parts[1])
                used_bytes = int(parts[2])
                total_mb = total_bytes // (1024 * 1024)
                used_mb = used_bytes // (1024 * 1024)
                utilization_pct = round(used_mb / total_mb * 100, 1) if total_mb else None
                # Use device id as name; full GPU name requires a separate rocm-smi call
                name = parts[0]
                return {"name": name, "used_mb": used_mb, "total_mb": total_mb,
                        "utilization_pct": utilization_pct}
        return None
    except Exception:
        return None


async def _mps_stats() -> dict | None:
    """
    Apple Silicon unified memory stats via torch.mps.
    Unified memory has no fixed GPU partition, so total_mb is driver-allocated
    (best approximation). utilization_pct is omitted.

    Two guards, both about the `import torch` below — a cold import costs ~14 s:

    - MPS only exists on macOS, so every other platform must return before the
      import. This probe is last in `gpu_stats`'s chain, i.e. it runs on exactly
      the machines with no nvidia-smi and no rocm-smi — where the import used to
      run inline and freeze the event loop (not just this request: the *whole
      app*, for the duration) on the first sidebar poll after every start.
    - On macOS the import still has to happen once, so it goes to an executor.
    """
    if sys.platform != "darwin":
        return None
    try:
        return await asyncio.get_running_loop().run_in_executor(None, _mps_stats_sync)
    except Exception:
        return None


def _mps_stats_sync() -> dict | None:
    """The blocking half of `_mps_stats`. Executor-only — never call inline."""
    try:
        import torch
        if not (
            hasattr(torch.backends, "mps")
            and torch.backends.mps.is_available()
            and torch.backends.mps.is_built()
        ):
            return None
        used_mb = 0
        total_mb = 0
        if hasattr(torch.mps, "current_allocated_memory"):
            used_mb = torch.mps.current_allocated_memory() // (1024 * 1024)
        if hasattr(torch.mps, "driver_allocated_memory"):
            total_mb = torch.mps.driver_allocated_memory() // (1024 * 1024)
        return {
            "name": "Apple Silicon (MPS)",
            "used_mb": used_mb,
            "total_mb": total_mb,
            "utilization_pct": None,
        }
    except Exception:
        return None


@router.get("/cpu-ram")
async def cpu_ram_stats():
    try:
        loop = asyncio.get_running_loop()
        cpu_pct, vm = await loop.run_in_executor(
            None, lambda: (psutil.cpu_percent(interval=0.1), psutil.virtual_memory())
        )
        return {
            "cpu_pct": round(cpu_pct, 1),
            "ram_used_mb": vm.used // (1024 * 1024),
            "ram_total_mb": vm.total // (1024 * 1024),
        }
    except Exception:
        return {"cpu_pct": 0.0, "ram_used_mb": 0, "ram_total_mb": 0}


@router.get("/gpu")
async def gpu_stats():
    result = await _nvidia_stats()
    if result:
        return result
    result = await _rocm_stats()
    if result:
        return result
    result = await _mps_stats()
    if result:
        return result
    return _NULL
