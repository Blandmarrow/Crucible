# PM-004: Setup reported "CUDA-enabled PyTorch installed" while leaving a CPU-only build

### Symptom

On native Windows, SAM 3 detection first failed outright with
`ModuleNotFoundError: No module named 'triton'` (raised six frames deep inside
`sam3/model/edt.py`, via `sam3_predictor._load_sam3_sync`). After installing
`triton-windows` the job ran, but every SAM3 load logged:

```
UserWarning: CUDA is not available or torch_xla is imported. Disabling autocast.
```

`python -c "import torch; print(torch.__version__, torch.cuda.is_available())"` returned
`2.12.1+cpu False` — **even after re-running `manage.ps1 update` and answering Y to the
"Install GPU-accelerated PyTorch (cuNNN)?" prompt**, which printed
`CUDA-enabled PyTorch (cu130) installed.` on every run. All ML features had been running
on CPU with no error anywhere.

### Root cause

Two independent defects that masked each other.

1. **The GPU install was a silent no-op.** `Install-TorchIfNeeded` ran
   `pip install "torch>=2.7" --index-url <cuda-index>` with no `--upgrade` or
   `--force-reinstall`. An already-installed `2.12.1+cpu` *satisfies* `torch>=2.7`, so pip
   printed "Requirement already satisfied", installed nothing, and **exited 0**. The code
   branched on `$LASTEXITCODE -eq 0` and therefore reported success. The only route to CUDA
   torch on Windows is the `download.pytorch.org` index (PyPI's Windows torch wheels are
   CPU-only, unlike Linux wheels which bundle CUDA), so the no-op left the user permanently
   on CPU with a success message.

2. **`triton` was never declared anywhere.** It is a real runtime dependency of sam3 on
   CUDA — `sam3/perflib/nms.py` and `perflib/connected_components.py` both lazily
   `import` triton kernels inside their `.is_cuda` branch, and `nms_masks` is on the image
   inference path — plus sam3 imports it unconditionally at module load. On Linux, torch's
   wheels pull triton in transitively, so it was invisible in dev; Windows torch wheels
   never ship it.

The interaction is what made this hard: because the venv had fallen back to CPU torch, every
`.is_cuda` branch was skipped, so installing `triton-windows` made SAM3 "work" and hid the
fact that the GPU was never being used.

### Generalizable rule

- **Never treat a package manager's exit code as proof that a package's *capability* is
  present.** Flag any install step that branches on `$LASTEXITCODE` / `if pip install` and
  then claims a capability. Verify the capability itself — here,
  `python -c "import torch; assert torch.cuda.is_available()"` — and report based on that.
- **A version specifier is not a build specifier.** `pip install "pkg>=X" --index-url ...`
  does nothing when any satisfying build is already present. When the *build variant*
  matters (CUDA vs CPU vs ROCm, local version tags like `+cpu`/`+cu130`), uninstall first
  or pass `--force-reinstall`; `>=` alone can never move you between variants.
- **A dependency that arrives transitively on the dev platform is still an undeclared
  dependency.** When adding a package, check whether it is reachable only because another
  platform's wheels happen to vendor it.
- **Do not silence a warning that is a proxy for a real misconfiguration.** The sam3
  autocast warning was the only user-visible signal of the CPU fallback.

### Why it wasn't caught the first time

Development and CI run in a Linux dev container where PyPI's torch wheels bundle CUDA and
pull in triton, so both defects are unreachable there — the Windows-only path had no
coverage and no verification step that asserted the *outcome* of the GPU install rather
than pip's exit status. The prompt wording ("Install GPU-accelerated PyTorch?") also let a
user reasonably decline on the belief that a system CUDA toolkit already satisfied it; the
venv/toolkit distinction was never stated at the point of decision.

### Fix

- `manage.ps1` / `manage.sh`: uninstall `torch`/`torchvision` before installing from the
  CUDA/ROCm index, then verify via `torch.cuda.is_available()` instead of the exit code.
- `manage.ps1`: prompt now states that the venv holds CPU-only torch and that this is not
  the system CUDA toolkit; new `Warn-IfCpuOnlyTorch` prints a loud summary at the end of
  setup/update when a GPU is present but torch is CPU-only.
- `manage.ps1`: `Install-TritonWindows` installs `triton-windows` alongside SAM3.
- `sam3_predictor._load_sam3_sync`: converts a `triton` `ModuleNotFoundError` into an
  actionable message naming the install command.
- `backend/main.py`: filters the cosmetic timm `FutureWarning`, and documents that the
  autocast warning is deliberately left unfiltered.

### Status & date

MITIGATED — the install paths now verify capability rather than exit code, but nothing
prevents a future step from reintroducing an exit-code-based capability claim.
Last reviewed for staleness: 2026-07-19.
