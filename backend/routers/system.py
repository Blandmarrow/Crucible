import asyncio
from fastapi import APIRouter

router = APIRouter(prefix="/system", tags=["system"])

_NULL = {"name": None, "used_mb": 0, "total_mb": 0, "utilization_pct": None}


@router.get("/gpu")
async def gpu_stats():
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
            return _NULL
        line = stdout.decode().strip().splitlines()[0]
        name, used_str, total_str = [s.strip() for s in line.split(",")]
        used_mb = int(used_str)
        total_mb = int(total_str)
        utilization_pct = round(used_mb / total_mb * 100, 1) if total_mb else None
        return {"name": name, "used_mb": used_mb, "total_mb": total_mb, "utilization_pct": utilization_pct}
    except Exception:
        return _NULL
