# PM-035: GPU work spread across the default thread pool stranded ~3 GB of VRAM

### Symptom

After an automated run's Score stage, the production server kept about 2.7 GB of
VRAM although `GET /quality/models` reported every scoring model `loaded: false`
and "Auto-unload after scoring" was on. It showed as Crucible's hardware meter
reading ~5.2 GB on an idle card, and in Windows' per-process counter
(`\GPU Process Memory(*)\Dedicated Usage`) as 2,693 MiB held by the uvicorn
process. Found on 2026-11-09 while measuring Strata and ComfyUI sharing a 32 GB card.

### Root cause

Every GPU call was dispatched with `loop.run_in_executor(None, fn, …)`, asyncio's
default `ThreadPoolExecutor` (`min(32, cpu + 4)` threads, 32 on the rig).
Sequential per-image awaits do not reuse one thread: the next submit often lands
before the previous worker has marked itself idle, so the pool grows to its cap
over a long run. Each thread that runs CUDA math gets its own cuBLAS, cuBLASLt and
cuDNN handles and a cuBLAS workspace (32 MiB on this card), and torch frees them
only when that thread exits. The default pool's threads never exit.

Measured with the real `POST /quality/score` job (technical + Aesthetic V2.5,
auto-unload on) through `api_env`, on production's code and torch 2.13:

| | Process VRAM after the job |
|---|---|
| CUDA context only | 547 MiB |
| 5 images | 741 MiB (2 threads) |
| 625 real images, default pool | 3,671 MiB — `torch.cuda.memory_allocated()` 1,024 MiB (32 × 32 MiB workspaces, no live tensors) plus ~2.1 GB outside the allocator |
| 625 images, one-thread pool | 643 MiB |

Unloading the models and `empty_cache()` both worked; neither can reach per-thread
library state. Windows' WDDM pages idle allocations out under pressure, which hid
it on the rig. Linux does not, so on a 16 GB card the memory stays until restart.

### Generalizable rule

Flag any CUDA work dispatched to a thread pool with more than one thread
(`run_in_executor(None, …)`, `asyncio.to_thread`, a new `ThreadPoolExecutor`).
Per-thread GPU library state costs memory per thread for the thread's lifetime, so
"the model was unloaded" does not mean "the VRAM came back". When measuring a leak,
compare the process's own VRAM against `memory_allocated`/`memory_reserved`: memory
outside the allocator points at the CUDA context, libraries or threads, not tensors.

### Why it wasn't caught the first time

The earlier ~3.5 GB leak (commit `c7e6841`, `_release_to_cpu`) had the same
symptom, and its fix was verified against the registry and the allocator, which
were both clean afterwards. Nothing measured the process's VRAM from outside torch,
and a short test run touches only a thread or two, so the cost scaled with the
length of a real run and never showed in a test. CI has no GPU.

### Fix

`backend/ml/device.py` gains `gpu_executor()` (one `crucible-gpu` thread) and
`run_on_gpu(fn, *args)`; every load and inference call goes through it, while CPU
work stays on the default pool. `services/image_edit_job.py`'s `EditSpec` gains a
`gpu` flag, since its callable is opaque to static analysis.
`backend/tests/test_gpu_executor_guard.py` fails CI on a GPU call sent to the
default pool (`docs/dev/ml-models.md` § One GPU thread); `test_gpu_executor.py`
pins the one-thread behaviour. Branch `fix/gpu-single-thread`.

### Status & date

MITIGATED — the guard follows names, dotted imports, partials, lambdas, local
functions, local assignments and model entries, not arbitrary data flow. An
attribute of a parameter (`spec.process`), a helper in another non-`ml` module
that calls GPU code, and a callable passed through a container are invisible to it.
Last reviewed for staleness: 2026-11-09.
