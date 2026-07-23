"""
Central device-selection and device-aware utilities for GPU/CPU inference.

Detection priority:
  cuda  — NVIDIA or AMD ROCm (identical torch.cuda.* API on both)
  mps   — Apple Silicon Metal Performance Shaders
  cpu   — fallback

All ML modules import from here. Nothing outside this file should call
torch.cuda.* directly (except torch.cuda.OutOfMemoryError in OOM catch clauses).

**`torch` is imported lazily, inside each function that needs it.** This module is
the only one in the router import chain that touches torch at all
(`routers/captioning.py` → `ml/model_manager.py` → here), so a module-level import
made `from backend.main import app` require a ~2 GB dependency. That is what the
HTTP test harness does, and CI deliberately installs no torch-sized packages — so
importing it here took down collection of the *whole* backend suite. The per-call
cost after the first import is a `sys.modules` dict lookup. Same pattern as the
lazy `cv2` in `technical_scorer` and the lazy `tiktoken` in `utils`.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # annotations only — `from __future__ import annotations` defers them
    import torch

_DEVICE: str | None = None


def get_device() -> str:
    """Return 'cuda', 'mps', or 'cpu'. Result is cached after first call."""
    import torch

    global _DEVICE
    if _DEVICE is None:
        if torch.cuda.is_available():
            _DEVICE = "cuda"
        elif (
            hasattr(torch.backends, "mps")
            and torch.backends.mps.is_available()
            and torch.backends.mps.is_built()
        ):
            _DEVICE = "mps"
        else:
            _DEVICE = "cpu"
    return _DEVICE


def is_gpu_available() -> bool:
    """True when get_device() returns 'cuda' or 'mps'."""
    return get_device() in ("cuda", "mps")


def memory_allocated_bytes() -> int:
    """
    Bytes currently allocated on the active accelerator.
      cuda → torch.cuda.memory_allocated()
      mps  → torch.mps.current_allocated_memory() (PyTorch >= 2.0), else 0
      cpu  → 0
    """
    import torch

    dev = get_device()
    try:
        if dev == "cuda":
            return torch.cuda.memory_allocated()
        if dev == "mps" and hasattr(torch.mps, "current_allocated_memory"):
            return torch.mps.current_allocated_memory()
    except Exception:
        pass
    return 0


def memory_reserved_mb() -> int:
    """
    Reserved (cached) VRAM in MiB — used for SSE progress events.
      cuda → torch.cuda.memory_reserved() // 1024 // 1024
      mps  → torch.mps.current_allocated_memory() // 1024 // 1024
             (MPS has no 'reserved' concept; allocated is the best proxy)
      cpu  → 0
    """
    import torch

    dev = get_device()
    try:
        if dev == "cuda":
            return torch.cuda.memory_reserved() // (1024 * 1024)
        if dev == "mps" and hasattr(torch.mps, "current_allocated_memory"):
            return torch.mps.current_allocated_memory() // (1024 * 1024)
    except Exception:
        pass
    return 0


def empty_cache() -> None:
    """
    Release cached allocations on the active accelerator.
      cuda → torch.cuda.empty_cache()
      mps  → torch.mps.empty_cache()
      cpu  → no-op
    """
    import torch

    dev = get_device()
    try:
        if dev == "cuda":
            torch.cuda.empty_cache()
        elif dev == "mps" and hasattr(torch.mps, "empty_cache"):
            torch.mps.empty_cache()
    except Exception:
        pass


@contextlib.contextmanager
def autocast_ctx(dtype: torch.dtype | None = None):
    """
    Context manager for mixed-precision inference.
      cuda/mps → torch.autocast(device_type, dtype=dtype)
      cpu      → torch.autocast('cpu', dtype=torch.bfloat16)
                 (bfloat16 autocast is supported on CPU since PyTorch 1.10)
    Passing dtype=None uses the framework default for the device.
    """
    import torch

    dev = get_device()
    if dev in ("cuda", "mps"):
        kwargs: dict = {}
        if dtype is not None:
            kwargs["dtype"] = dtype
        with torch.autocast(dev, **kwargs):
            yield
    else:
        with torch.autocast("cpu", dtype=torch.bfloat16):
            yield


def safe_dtype_for_device(requested: torch.dtype) -> torch.dtype:
    """
    Return a dtype that is safe to use explicitly on the current device.
      cuda → pass-through (any dtype)
      mps  → pass-through (bfloat16 and float16 both supported on MPS >= PyTorch 2.0)
      cpu  → torch.float32 (explicit half-precision without autocast has no benefit on CPU)
    """
    import torch

    dev = get_device()
    if dev == "cpu" and requested in (torch.float16, torch.bfloat16):
        return torch.float32
    return requested


def is_oom_error(exc: BaseException) -> bool:
    """
    Return True when the exception represents a GPU out-of-memory condition.

    NVIDIA/ROCm raise torch.cuda.OutOfMemoryError (a subclass of RuntimeError).
    MPS raises a plain RuntimeError with 'out of memory' in the message.

    Recommended catch pattern:
        except (torch.cuda.OutOfMemoryError, RuntimeError) as _e:
            if not device.is_oom_error(_e):
                raise
            device.empty_cache()
            raise RuntimeError("GPU out of memory during ...")
    """
    import torch

    return isinstance(exc, torch.cuda.OutOfMemoryError) or (
        isinstance(exc, RuntimeError) and "out of memory" in str(exc).lower()
    )
