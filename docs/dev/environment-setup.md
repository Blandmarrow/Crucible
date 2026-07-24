# Environment setup: venv, prerequisites & GPU wheels

This file covers everything `manage.ps1` / `manage.sh` do to build a working environment: the venv's ML packages, prerequisite auto-install, Python version discovery, PyTorch GPU auto-detection, the SAM2/SAM3 install steps, the update self-handoff, and the `manage.ps1` encoding constraint. Server lifecycle, the database and SSE live in `docs/dev/backend-infrastructure.md`; the models these packages serve are in `docs/dev/ml-models.md` and `docs/dev/detection.md`.

### Venv ML packages

`torch`, `transformers`, `open_clip_torch`, `accelerate`, `safetensors`, and `timm` are listed as real dependencies in `backend/requirements.txt`. The venv is created with `--system-site-packages`, so if any of these are already present in the system Python they are reused (no reinstall). `huggingface-hub` is pinned to `>=0.30,<1.0` to stay compatible with system-installed ML packages.

### Prerequisite auto-install

`Cmd-Setup` / `cmd_setup` call `Install-Deps` (PowerShell) / `_install_deps` (bash) as their first step, replacing the old `Check-Deps` / `_check_deps` functions that only checked and errored.

- Both check Python 3.12+ and Node.js 18+ by version number (not just existence) and prompt (`[Y/n]`, default Yes) before auto-installing if missing or outdated; declining either exits with code 1.
- PowerShell uses `winget install --scope user` (no elevation needed) and refreshes `$env:PATH` from the registry immediately after; bash uses `brew` on macOS and `apt`/`dnf`/`pacman` on Linux for Python, and NodeSource LTS + `nvm` fallback for Node.js on Linux.
- `pip install -r requirements.txt` is also prompted before running in both `Cmd-Setup`/`Cmd-Update` and `cmd_setup`/`cmd_update`; declining skips the install and setup continues without error (the app will not function without dependencies). In non-interactive mode (redirected stdin), all prompts default to Yes silently and the package listing is suppressed.
- The `$hasCuda` check in `Install-TorchIfNeeded` is wrapped in `try/catch` (catch is empty — swallows any exception): on a fresh venv before `requirements.txt` is installed, `import torch` fails and Python writes a traceback to stderr; PS5.1 converts that stderr output to a `NativeCommandError` and `$ErrorActionPreference = "Stop"` turns it into a terminating error — without the try/catch this aborts setup. Do not remove the try/catch.

### Python version discovery and pre-release rejection

Pre-release Python versions (alpha, beta, rc — e.g. `3.15.0b1`) are explicitly rejected even if their major.minor number meets the 3.12+ requirement. Packages like `scipy`, `numpy`, and `torch` have no pre-built wheels for pre-release interpreters, causing pip to attempt a source build that fails without C/Fortran compilers. Both scripts detect pre-release by matching `[0-9](a|b|rc)[0-9]` in the version string and print a clear message instead of "3.12+ is required".

- In `manage.sh`, `python3.12` is checked before the generic `python3`/`python` so a specifically-versioned stable interpreter is always preferred.
- In `manage.ps1`, after winget installs Python 3.12 the script uses `py -3.12 -c "import sys; print(sys.executable)"` to resolve the exact executable path via the Windows Python Launcher — this guarantees the venv is created with Python 3.12 regardless of which Python binary is first in PATH.
- The `update` command also checks the venv's Python for pre-release status and emits a specific error with remediation steps if found.
- The `setup` command also validates the existing venv's Python version before reusing it: if the venv was built with an unsupported or pre-release interpreter it is automatically deleted and recreated with the current system Python, so users who install a correct Python and re-run `setup` without manually deleting `venv/` are handled correctly.

### PyTorch GPU auto-detection

`manage.ps1 setup` / `manage.sh setup` (and `update`) run `Install-TorchIfNeeded` / `_install_torch_if_needed` **before** `pip install -r requirements.txt`. On Linux/macOS the helper checks three GPU backends in order:

1. **NVIDIA** — skips if `torch.cuda.is_available()` is already True; otherwise checks for `nvidia-smi`, parses the `CUDA Version: X.Y` line (the driver's *maximum* supported CUDA runtime — CUDA drivers are backward-compatible, so a 13.2-capable driver runs any older `cuXXX` wheel), shows the wheel index URL, and prompts (`[Y/n]`) before downloading `torch>=2.7` (~2.5 GB) from the chosen PyTorch wheel index. Declining skips the GPU wheel; CPU-only torch installs later via `requirements.txt`.
2. **AMD ROCm** (Linux only) — detected via `rocm-smi`; ROCm version determined via `rocminfo`, `/opt/rocm-*` dirname, or `/opt/rocm/VERSION` in that order; maps to wheel tag `rocm6.3` (the PyTorch 2.7 floor). ROCm < 6.3 is **rejected with an error and `exit 1`** — no CPU fallback; the user must update the ROCm stack, delete `venv/`, and re-run setup. An undetectable ROCm version optimistically tries the `rocm6.3` wheel. Prompts before downloading, same as NVIDIA. `manage.ps1` is unchanged (ROCm has no Windows support).
3. **Apple Silicon MPS** (macOS) — no wheel change needed; standard CPU PyTorch already includes MPS support, so setup just prints a message and returns.

If none of the above are detected, CPU-only torch is installed as a fallback.

**NVIDIA wheel selection is dynamic**: `_query_cuda_tag` / `Get-BestCudaTag` fetch the live index at `https://download.pytorch.org/whl/`, parse the real `href="cuNNN/"` directory links (anchored on `href=` so unrelated substrings like `cudnn-cu13` are not mistaken for wheel tags), and pick the highest tag ≤ the driver's CUDA version and ≥ `cu126` (the PyTorch 2.7 floor). The `cuNNN` → version rule matches PyTorch's own naming — last digit is the minor, preceding digits the major (`cu128`→12.8, `cu130`→13.0, `cu132`→13.2) — so the ceiling tracks whatever PyTorch publishes without needing a script update. If the index is unreachable (curl/wget or `Invoke-WebRequest` fails), it falls back to a conservative built-in table (`cu128` for ≥12.8, else `cu126` for ≥12.6).

**Two non-obvious requirements in the NVIDIA path, both load-bearing — see `docs/dev/postmortems/PM-004-silent-cpu-torch-fallback.md`.**

- `torch`/`torchvision` are **uninstalled before** the indexed install: an already-present CPU build satisfies `torch>=2.7`, so a plain `pip install` is a silent no-op that still exits 0 and the CUDA wheel never lands (`torchvision` goes too — a `+cpu` torchvision against a CUDA torch fails at runtime). A version specifier can never move you between build variants. The trade-off is that a failed download leaves no torch at all; the later `requirements.txt` step reinstalls a CPU build, so the recovery path only breaks if the user also declines that prompt.
- Success is judged by **running `torch.cuda.is_available()` in the venv interpreter, never by pip's exit code**, which is 0 in several cases where no usable CUDA build was installed.

Do not "simplify" either back.

### SAM2 install step

After `pip install -r requirements.txt`, both `setup` (SAM2 is step `[5/7]` on both platforms) and `update` (step `[3/6]`) prompt (`[Y/n]`, default Yes) to `pip install git+https://github.com/facebookresearch/sam2.git pycocotools`. Declining or a failed install only disables Grounded SAM2 segmentation (`backend/ml/sam2_predictor.py`); all other features continue to work.

### SAM3 install step

The next step in both scripts (`setup` `[6/7]`, `update` `[4/6]`) prompts the same way to install SAM 3, run as `pip install git+https://github.com/facebookresearch/sam3.git --no-deps` followed by explicit deps `pip install iopath ftfy pycocotools "setuptools<81"` (`--no-deps` because sam3 pins `numpy<2` and `ftfy==6.1.1`, which conflict with `requirements.txt`).

`manage.ps1` then calls `Install-TritonWindows` (`pip install triton-windows`) — **Windows only, and not optional on GPU**: sam3 imports triton at module load and calls triton kernels on CUDA, and torch's Windows wheels never ship it (its Linux wheels pull it in transitively, so `manage.sh` needs no equivalent). `$sam3Ok` is captured *before* the triton install so a triton failure cannot flip the SAM3 status message, and `Install-TritonWindows` additionally resets `$LASTEXITCODE` to 0 so no later step observes it; see `docs/dev/detection.md` and `docs/dev/postmortems/PM-004-silent-cpu-torch-fallback.md`.

SAM3 additionally needs a checkpoint: `sam3.safetensors` downloaded manually from `https://huggingface.co/1038lab/sam3` into `models/sam3/` (safetensors only — no `.pt`, no gated HF download; see `docs/dev/detection.md`). Declining or a failed install only disables SAM 3 text-prompt segmentation (`backend/ml/sam3_predictor.py`).

Overall, `update` is **6 steps** and `setup` is **7 steps** on both platforms.

### Update self-handoff (`[1/6]`)

`update` pulls before it does anything else, so the pull can rewrite the very script that is running. Both scripts hash themselves (`Get-FileHash` / `cksum`) around `git pull` and, if the hash changed, hand off to the new version — PowerShell re-invokes through `(Get-Process -Id $PID).Path` and exits with the child's code; bash `exec`s `"${BASH:-bash}" "$0" update`. An exported `CRUCIBLE_SELF_UPDATED=1` guard permits **exactly one** handoff per run, so a file that keeps changing cannot loop. Without this, PowerShell (which parses the whole file to an AST up front) silently runs the *old* script for the rest of the update, forcing a second `update` run before new logic applies; bash is worse, as it reads by byte offset and can resume mid-line in the rewritten file and execute a fragment. **Keep the handoff first in `cmd_update`/`Cmd-Update`, immediately after the pull** — any step added between the pull and the hash comparison runs under the old script.

### Lockfile reset around the pull

`frontend/package-lock.json` is tracked, but `npm install` rewrites it on some machines — npm major versions reorder and reformat the file even when the resolved tree is identical. The working tree is then permanently dirty, and the first `git pull` that carries a real lockfile change aborts with *"Your local changes to the following files would be overwritten by merge"*, taking the whole update with it. `Reset-Lockfile` / `_reset_lockfile` restores the file: **before the pull** in `update` (rescuing a machine that is already dirty) and **after every `npm install`** in `update` and `setup` (the actual prevention, so the tree never goes dirty in the first place).

Two deliberate limits. It is scoped to that single path — any other locally-modified tracked file must still abort the pull rather than be silently discarded. And it acts only on an *unstaged* rewrite: `git status --porcelain` prints `XY path`, and the helper requires `Y == "M"`, so a staged lockfile edit is left alone and still blocks the pull, which is correct for a deliberate change. `git checkout --` restores from the index rather than `HEAD` for the same reason. Both helpers always return success (PowerShell resets `$LASTEXITCODE`, bash `return 0`) so a failed restore never aborts the caller under `$ErrorActionPreference = "Stop"` or `set -e`.

The pre-pull reset necessarily runs under the **old** script — the one already on disk. A machine whose lockfile is dirty when it pulls *this* fix in still needs one manual `git checkout -- frontend/package-lock.json` first; the automation only takes effect from the following update onward.

### `manage.ps1` encoding constraint

PowerShell 5.1 reads `.ps1` files using Windows-1252 by default (no BOM = legacy encoding). Non-ASCII characters in string literals are misread — the UTF-8 byte sequence for an em dash (`E2 80 94`) decodes as `a`, Euro sign, `"` in Windows-1252, and that stray `"` silently terminates the string, corrupting the parser state for the rest of the file. **Never use non-ASCII characters (em dashes, curly quotes, ellipses, etc.) anywhere in `manage.ps1`.** Use plain ASCII equivalents: ` - ` instead of ` — `, `...` instead of `…`, etc. This constraint does not apply to `manage.sh` (bash reads UTF-8 natively) or to any `.md`/`.py`/`.ts` files.
