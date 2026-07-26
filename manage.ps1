# manage.ps1 - Crucible launcher (Windows)
# Usage: .\manage.ps1 <setup|start|update|dev>

param(
    [Parameter(Mandatory = $false, Position = 0)]
    [ValidateSet("setup", "start", "update", "dev")]
    [string]$Command
)

$ErrorActionPreference = "Stop"
$ROOT = $PSScriptRoot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Install-Deps {
    # --- Python ---
    Write-Host "[1/2] Checking Python..." -ForegroundColor Yellow
    $pythonOk = $false

    foreach ($cmd in @("python", "python3", "py")) {
        if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { continue }
        try { $ver = & $cmd --version 2>&1 } catch { continue }
        if ($LASTEXITCODE -eq 0 -and "$ver" -match "Python (\d+)\.(\d+)") {
            $maj = [int]$Matches[1]; $min = [int]$Matches[2]
            if ($maj -gt 3 -or ($maj -eq 3 -and $min -ge 12)) {
                if ("$ver" -match "(a|b|rc)\d+") {
                    # Pre-release versions have no wheels for scipy/torch/numpy yet
                    Write-Host "  Found $ver - pre-release Python is not supported; use a stable 3.12+ release." -ForegroundColor Yellow
                } else {
                    Write-Host "  Found: $ver" -ForegroundColor Green
                    $script:PythonExe = $cmd
                    $pythonOk = $true
                    break
                }
            } else {
                Write-Host "  Found $ver - 3.12+ is required." -ForegroundColor Yellow
            }
        }
    }

    if (-not $pythonOk) {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Write-Host "  Python 3.12+ is required but not found." -ForegroundColor Yellow
            Write-Host "  Source: https://www.python.org/ (via winget - Python 3.12)" -ForegroundColor DarkGray
            if ([System.Console]::IsInputRedirected) { $reply = "" } else {
                $reply = Read-Host "  Install Python 3.12? [Y/n]"
            }
            if ($reply -ne "" -and $reply -notmatch "^[Yy]") {
                Write-Host "  Install Python 3.12+ manually from: https://www.python.org/downloads/" -ForegroundColor Cyan
                exit 1
            }
            Write-Host "  Installing Python 3.12 via winget (user scope, no admin needed)..." -ForegroundColor Yellow
            winget install --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
            if ($LASTEXITCODE -eq 0) {
                $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "User") + ";" + [System.Environment]::GetEnvironmentVariable("PATH", "Machine")
                # Use py -3.12 to resolve the exact executable that was just installed,
                # bypassing any pre-release Python that may be first in PATH.
                $pyExe = $null
                if (Get-Command py -ErrorAction SilentlyContinue) {
                    $pyPath = & py -3.12 -c "import sys; print(sys.executable)" 2>&1
                    if ($LASTEXITCODE -eq 0 -and (Test-Path "$pyPath")) { $pyExe = "$pyPath" }
                }
                if (-not $pyExe) {
                    $pyCmd = Get-Command python -ErrorAction SilentlyContinue
                    $pyPath = if ($pyCmd) { $pyCmd.Source } else { $null }
                    if ($pyPath) { $pyExe = $pyPath }
                }
                if ($pyExe) {
                    $script:PythonExe = $pyExe
                    $ver = & $pyExe --version 2>&1
                    Write-Host "  Installed: $ver" -ForegroundColor Green
                } else {
                    Write-Host "  Python installed. Please restart your terminal and re-run setup." -ForegroundColor Yellow
                    exit 1
                }
            } else {
                Write-Host "ERROR: winget install failed. Install Python 3.12+ from:" -ForegroundColor Red
                Write-Host "  https://www.python.org/downloads/" -ForegroundColor Cyan
                exit 1
            }
        } else {
            Write-Host "ERROR: Python 3.12+ not found and winget is unavailable." -ForegroundColor Red
            Write-Host "  Install Python 3.12+ from: https://www.python.org/downloads/" -ForegroundColor Cyan
            exit 1
        }
    }

    # --- Node.js ---
    Write-Host "[2/2] Checking Node.js..." -ForegroundColor Yellow
    $nodeOk = $false

    if (Get-Command node -ErrorAction SilentlyContinue) {
        $nodeVer = node --version 2>&1
        if ($LASTEXITCODE -eq 0 -and "$nodeVer" -match "v(\d+)") {
            if ([int]$Matches[1] -ge 18) {
                Write-Host "  Found: Node $nodeVer" -ForegroundColor Green
                $nodeOk = $true
            } else {
                Write-Host "  Found Node $nodeVer - 18+ is required." -ForegroundColor Yellow
            }
        }
    }

    if (-not $nodeOk) {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Write-Host "  Node.js 18+ is required but not found." -ForegroundColor Yellow
            Write-Host "  Source: https://nodejs.org/ (via winget - Node.js LTS)" -ForegroundColor DarkGray
            if ([System.Console]::IsInputRedirected) { $reply = "" } else {
                $reply = Read-Host "  Install Node.js LTS? [Y/n]"
            }
            if ($reply -ne "" -and $reply -notmatch "^[Yy]") {
                Write-Host "  Install Node.js 18+ manually from: https://nodejs.org/" -ForegroundColor Cyan
                exit 1
            }
            Write-Host "  Installing Node.js LTS via winget (user scope, no admin needed)..." -ForegroundColor Yellow
            winget install --id OpenJS.NodeJS.LTS --scope user --silent --accept-package-agreements --accept-source-agreements
            if ($LASTEXITCODE -eq 0) {
                $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "User") + ";" + [System.Environment]::GetEnvironmentVariable("PATH", "Machine")
                $nodeVer = & node --version 2>&1
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "  Installed: Node $nodeVer" -ForegroundColor Green
                } else {
                    Write-Host "  Node.js installed. Please restart your terminal and re-run setup." -ForegroundColor Yellow
                    exit 1
                }
            } else {
                Write-Host "ERROR: winget install failed. Install Node.js 18+ from:" -ForegroundColor Red
                Write-Host "  https://nodejs.org/" -ForegroundColor Cyan
                exit 1
            }
        } else {
            Write-Host "ERROR: Node.js 18+ not found and winget is unavailable." -ForegroundColor Red
            Write-Host "  Install Node.js 18+ from: https://nodejs.org/" -ForegroundColor Cyan
            exit 1
        }
    }
}

function Activate-Venv {
    & "$ROOT\venv\Scripts\Activate.ps1"
}

function Get-BestCudaTag {
    # Returns the newest PyTorch CUDA wheel tag (e.g. "cu130") that the driver's
    # CUDA version supports, by querying the live wheel index at
    # download.pytorch.org. Returns "" if the index was reached but nothing
    # suitable <= driver exists, and $null if the index could not be fetched
    # (caller should fall back to the built-in table).
    #
    # Wheel-tag convention (matches PyTorch's own naming): the last digit is the
    # CUDA minor, the preceding digits the major - cu128 -> 12.8, cu130 -> 13.0.
    # PyTorch 2.7+ requires CUDA 12.6+, so tags below cu126 are ignored.
    param([int]$DriverMaj, [int]$DriverMin)
    $driverVer = $DriverMaj * 100 + $DriverMin
    $html = $null
    try {
        $html = (Invoke-WebRequest -Uri "https://download.pytorch.org/whl/" `
                 -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop).Content
    } catch { return $null }
    if (-not $html) { return $null }

    # Anchor on real directory links (href="cuNNN/") so unrelated substrings
    # like cudnn-cu13 / nvidia-nccl-cu12 don't get mistaken for wheel tags.
    $best = 0; $bestTag = ""
    foreach ($m in [regex]::Matches($html, 'href="cu(\d+)/"')) {
        $num = $m.Groups[1].Value
        if ($num.Length -lt 2) { continue }
        $tmin = [int]$num.Substring($num.Length - 1, 1)
        $tmaj = [int]$num.Substring(0, $num.Length - 1)
        $ver = $tmaj * 100 + $tmin
        if ($ver -le $driverVer -and $ver -ge 1206 -and $ver -gt $best) {
            $best = $ver; $bestTag = "cu$num"
        }
    }
    return $bestTag
}

function Install-TritonWindows {
    # triton is a REAL runtime dependency of sam3 on CUDA, not just an import
    # artifact: sam3/perflib/nms.py and connected_components.py both lazily import
    # triton kernels inside their `.is_cuda` branch, and nms_masks is called on the
    # image inference path. sam3 also imports it unconditionally at module load
    # (sam3/model/edt.py), so without it SAM3 cannot even be imported.
    #
    # torch's Windows wheels never ship triton; torch's Linux wheels pull it in
    # transitively, which is why this only ever bites on Windows. triton-windows
    # is the Windows port. Non-fatal: SAM3 still imports and runs on CPU-only
    # hosts, which never reach a triton code path.
    Write-Host "  Installing triton-windows (required by SAM3 on GPU)..." -ForegroundColor DarkGray
    & "$ROOT\venv\Scripts\pip.exe" install triton-windows --quiet
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  triton-windows installed." -ForegroundColor Green
    } else {
        Write-Host "  WARNING: triton-windows install failed - SAM 3 will fail to load." -ForegroundColor Yellow
        Write-Host "  To retry: .\venv\Scripts\pip.exe install triton-windows" -ForegroundColor DarkGray
    }
    # Don't let a triton failure flip the SAM3 status message below.
    $global:LASTEXITCODE = 0
}

function Warn-IfCpuOnlyTorch {
    # Printed at the very end of setup/update so a CPU-only fallback cannot
    # scroll past unnoticed - it costs an order of magnitude in ML speed and
    # every symptom of it (slow jobs, autocast warnings from sam3) is indirect.
    $cuda = $null
    try {
        $cuda = & "$ROOT\venv\Scripts\python.exe" -c `
            "import torch; print(torch.cuda.is_available())" 2>$null
    } catch { return }
    if ($cuda -eq "True") { return }
    if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) { return }
    Write-Host ""
    Write-Host "  !! PyTorch is CPU-only, but an NVIDIA GPU is present." -ForegroundColor Yellow
    Write-Host "  !! Captioning, scoring and detection will be extremely slow." -ForegroundColor Yellow
    Write-Host "  !! Re-run this script and answer Y to the GPU PyTorch prompt." -ForegroundColor Yellow
}

function Install-TorchIfNeeded {
    # If a CUDA-capable torch is already reachable (e.g. via --system-site-packages
    # from a prior CUDA install) there is nothing to do.
    $hasCuda = $null
    try {
        $hasCuda = & "$ROOT\venv\Scripts\python.exe" -c `
            "import torch; print(torch.cuda.is_available())" 2>$null
    } catch { }
    if ($hasCuda -eq "True") {
        Write-Host "  CUDA-enabled PyTorch already available - skipping." -ForegroundColor Green
        return
    }

    # Try to determine the driver's maximum supported CUDA version via nvidia-smi,
    # which ships with every NVIDIA driver (no CUDA toolkit required).
    if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
        Write-Host "  No NVIDIA GPU detected - PyTorch will be CPU-only." -ForegroundColor DarkGray
        Write-Host "  ML features (captioning, scoring) require an NVIDIA GPU." -ForegroundColor DarkGray
        return
    }

    $nvOut = (& nvidia-smi) | Out-String
    if ($nvOut -match "CUDA Version:\s*(\d+)\.(\d+)") {
        $maj = [int]$Matches[1]; $min = [int]$Matches[2]
        Write-Host "  NVIDIA GPU detected - driver supports CUDA $maj.$min." -ForegroundColor Green

        # Pick the newest wheel the driver supports by querying the live wheel
        # index; fall back to a built-in table if it's unreachable.
        # PyTorch 2.7+ requires CUDA 12.6+ (driver 560.94+ on Windows).
        $tag = Get-BestCudaTag -DriverMaj $maj -DriverMin $min
        if ($null -eq $tag) {
            Write-Host "  Could not reach PyTorch wheel index - using built-in version table." -ForegroundColor DarkGray
            if ($maj -gt 12 -or ($maj -eq 12 -and $min -ge 8)) {
                $tag = "cu128"
            } elseif ($maj -eq 12 -and $min -ge 6) {
                $tag = "cu126"
            } else {
                $tag = $null
            }
        } elseif ($tag -eq "") {
            $tag = $null
        }

        if ($tag) {
            $indexUrl = "https://download.pytorch.org/whl/$tag"
            Write-Host "  PyTorch in this venv is currently CPU-only (or not yet installed)." -ForegroundColor Yellow
            Write-Host "  This installs the CUDA-enabled PyTorch build INTO THE VENV. It is not" -ForegroundColor Yellow
            Write-Host "  the system CUDA toolkit, and an existing CUDA install does not provide" -ForegroundColor Yellow
            Write-Host "  it. Answering N leaves all ML features running on CPU (very slow)." -ForegroundColor Yellow
            Write-Host "  Source: $indexUrl (~2.5 GB)" -ForegroundColor DarkGray
            if ([System.Console]::IsInputRedirected) { $reply = "" } else {
                $reply = Read-Host "  Install GPU-accelerated PyTorch ($tag)? [Y/n]"
            }
            if ($reply -ne "" -and $reply -notmatch "^[Yy]") {
                Write-Host "  Skipping GPU PyTorch - CPU-only will be installed via requirements.txt." -ForegroundColor DarkGray
                return
            }
            Write-Host "  Installing PyTorch ($tag) from PyTorch wheel index..." -ForegroundColor Yellow
            # A CPU-only build already in the venv (e.g. 2.12.1+cpu) SATISFIES
            # "torch>=2.7", so a plain install is a silent no-op that still exits 0
            # and the CUDA wheel never lands - the user answers Y, sees a success
            # message, and stays on CPU. Uninstall first so the install is real.
            # torchvision goes too: a +cpu torchvision against a CUDA torch fails
            # at runtime, and sam3/spandrel/timm all pull it in.
            & "$ROOT\venv\Scripts\pip.exe" uninstall -y torch torchvision --quiet 2>$null
            & "$ROOT\venv\Scripts\pip.exe" install "torch>=2.7" torchvision --index-url $indexUrl --quiet
            # Verify against the interpreter, not $LASTEXITCODE: pip exits 0 in
            # several cases where no usable CUDA build ended up installed.
            $cudaNow = $null
            try {
                $cudaNow = & "$ROOT\venv\Scripts\python.exe" -c `
                    "import torch; print(torch.cuda.is_available())" 2>$null
            } catch { }
            if ($cudaNow -eq "True") {
                Write-Host "  CUDA-enabled PyTorch ($tag) installed." -ForegroundColor Green
            } else {
                Write-Host "  WARNING: PyTorch is still CPU-only - ML features will be very slow." -ForegroundColor Yellow
                Write-Host "  Retry manually: .\venv\Scripts\pip.exe uninstall -y torch torchvision" -ForegroundColor DarkGray
                Write-Host "                  .\venv\Scripts\pip.exe install torch torchvision --index-url $indexUrl" -ForegroundColor DarkGray
            }
        } else {
            Write-Host "  CUDA $maj.$min is older than 12.6 - GPU-accelerated PyTorch is not available." -ForegroundColor Yellow
            Write-Host "  CPU-only PyTorch will be installed via requirements.txt." -ForegroundColor DarkGray
            Write-Host "  To enable GPU support, update your NVIDIA driver (560.94+) and re-run setup." -ForegroundColor DarkGray
        }
    } else {
        Write-Host "  Could not parse CUDA version from nvidia-smi - CPU-only PyTorch will be used." -ForegroundColor DarkGray
    }
}

function Build-Frontend {
    Write-Host "Building frontend..." -ForegroundColor Yellow
    Push-Location "$ROOT\frontend"
    npm run build
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Frontend build failed." -ForegroundColor Red
        Pop-Location
        exit 1
    }
    Pop-Location
    Write-Host "  Frontend built." -ForegroundColor Green
}

# frontend/package-lock.json is tracked, but `npm install` rewrites it on some
# machines (npm major-version differences reorder or reformat the file even when
# the resolved tree is identical). That leaves the working tree permanently
# dirty, and the next `git pull` carrying a real lockfile change aborts with
# "Your local changes would be overwritten by merge". The lock is a generated
# artifact here - update/setup reinstall from it - so discard the local rewrite.
# Scoped to this one path on purpose: any other locally-modified tracked file
# must still stop the pull rather than be silently thrown away.
#
# This helper is best-effort by design: it runs inside the launcher, so a
# failure here must never be worse than the problem it fixes. The whole body is
# wrapped, and $ErrorActionPreference is dropped to Continue for the duration -
# the file-scope "Stop" turns native stderr into a terminating error on
# PowerShell 5.1, and git writes routine warnings there ("unable to find all
# commit-graph files"), which would abort the update over nothing.
function Reset-Lockfile {
    param([switch]$Quiet)

    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { return }
    if (-not (Test-Path "$ROOT\.git")) { return }

    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        # --porcelain prints "XY path", X = index column, Y = worktree column,
        # and nothing at all when the path is clean. Only an unstaged rewrite
        # (Y = M) is npm's; a staged lockfile edit is deliberate, so leave it be
        # - it still blocks the pull, which is right for a real change.
        # 2>&1 turns stderr lines into ErrorRecords; keep only real stdout.
        $out = @(git -C "$ROOT" status --porcelain -- frontend/package-lock.json 2>&1 |
                 Where-Object { $_ -is [string] })
        if ($out.Count -eq 0) { return }
        $status = [string]$out[0]
        if ($status.Length -lt 2 -or $status[1] -ne 'M') { return }

        git -C "$ROOT" checkout -- frontend/package-lock.json 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0 -and -not $Quiet) {
            Write-Host "  Discarded local npm rewrite of frontend/package-lock.json." -ForegroundColor DarkGray
        }
    } catch {
        # Nothing to do - the pull below will report the real problem.
    } finally {
        $ErrorActionPreference = $prevEap
        # Never leak a non-zero exit code into a caller's $LASTEXITCODE check.
        $global:LASTEXITCODE = 0
    }
}

function Run-Migrations {
    Write-Host "Running database migrations..." -ForegroundColor Yellow
    Push-Location "$ROOT\backend"
    python -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Database migration failed." -ForegroundColor Red
        Pop-Location
        exit 1
    }
    Pop-Location
    Write-Host "  Migrations applied." -ForegroundColor Green
}

# --- Startup splash --------------------------------------------------------
# `start` is silent for a long stretch before uvicorn answers: migrations, an
# occasional frontend rebuild, then the torch/transformers import chain. A
# stdlib-only placeholder server holds :8000 for that stretch and serves the
# animated mark, so the browser can be opened straight away on the real URL; it
# is stopped before uvicorn binds, and the page swaps itself for the app on the
# first healthy response. See scripts\splash_server.py.
# Set CRUCIBLE_NO_BROWSER=1 to skip the whole thing.
$script:SplashProcess = $null

function Start-Splash {
    if ($env:CRUCIBLE_NO_BROWSER -eq "1") { return }
    $splash = "$ROOT\scripts\splash_server.py"
    if (-not (Test-Path $splash)) { return }

    $py = "$ROOT\venv\Scripts\python.exe"
    if (-not (Test-Path $py)) { $py = "python" }

    # Clear first: a sentinel left by a crash would read as "ready" instantly.
    $ready = "$ROOT\.splash-ready"
    if (Test-Path $ready) { Remove-Item $ready -Force -ErrorAction SilentlyContinue }

    try {
        $script:SplashProcess = Start-Process -FilePath $py `
            -ArgumentList @("`"$splash`"", "--parent-pid", $PID, "--ready-file", "`"$ready`"") `
            -WindowStyle Hidden -PassThru
    } catch {
        $script:SplashProcess = $null
        return
    }

    # Wait for the sentinel, not for a fixed delay: the server writes it from
    # inside its accept loop, so it is proof that requests are actually being
    # answered. A bound socket is not a serving one - anything slow between the
    # two (the stdlib's reverse-DNS lookup at bind time was one, and it is far
    # slower on Windows) would leave the browser holding an accepted connection
    # that never gets a reply: a tab that spins forever on a blank page.
    $waited = 0
    while ((-not (Test-Path $ready)) -and ($waited -lt 100)) {
        if ($script:SplashProcess.HasExited) {
            # Gone already - the port was taken. Stay quiet and leave the browser
            # closed: uvicorn reports that conflict itself further down, and
            # opening a browser onto someone else's server would only muddy it.
            $script:SplashProcess = $null
            return
        }
        Start-Sleep -Milliseconds 100
        $waited++
    }

    if (-not (Test-Path $ready)) {
        # Alive but not serving after 10s. Stop it rather than hand uvicorn a
        # busy port, and start with no splash at all.
        Write-Host "  (splash did not come up - starting without it; open the URL below once ready)" -ForegroundColor DarkGray
        Stop-Splash
        return
    }

    try { Start-Process "http://localhost:8000" | Out-Null } catch {
        Write-Host "  (could not open a browser - open http://localhost:8000 yourself)" -ForegroundColor DarkGray
    }
}

function Stop-Splash {
    if (-not $script:SplashProcess) { return }
    try {
        if (-not $script:SplashProcess.HasExited) {
            Stop-Process -Id $script:SplashProcess.Id -Force -ErrorAction SilentlyContinue
        }
    } catch { }
    $script:SplashProcess = $null
    $ready = "$ROOT\.splash-ready"
    if (Test-Path $ready) { Remove-Item $ready -Force -ErrorAction SilentlyContinue }
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

function Cmd-Setup {
    Write-Host ""
    Write-Host "=== Crucible - First-Time Setup ===" -ForegroundColor Cyan
    Write-Host ""

    Install-Deps

    Write-Host "[3/7] Creating Python virtual environment..." -ForegroundColor Yellow
    if (Test-Path "$ROOT\venv") {
        # Validate existing venv before reusing - recreate if Python is wrong version or pre-release.
        $venvVer = & "$ROOT\venv\Scripts\python.exe" --version 2>&1
        $venvOk = $false
        if ("$venvVer" -match "Python (\d+)\.(\d+)") {
            $vMaj = [int]$Matches[1]; $vMin = [int]$Matches[2]
            $venvOk = ($vMaj -gt 3 -or ($vMaj -eq 3 -and $vMin -ge 12)) -and
                      ("$venvVer" -notmatch "(a|b|rc)\d+")
        }
        if ($venvOk) {
            Write-Host "  venv already exists, skipping creation." -ForegroundColor DarkGray
        } else {
            Write-Host "  Existing venv uses $venvVer - recreating with stable Python 3.12+..." -ForegroundColor Yellow
            Remove-Item -Recurse -Force "$ROOT\venv"
            & $script:PythonExe -m venv --system-site-packages "$ROOT\venv"
            Write-Host "  venv created at $ROOT\venv (inherits system ML packages)" -ForegroundColor Green
        }
    } else {
        & $script:PythonExe -m venv --system-site-packages "$ROOT\venv"
        Write-Host "  venv created at $ROOT\venv (inherits system ML packages)" -ForegroundColor Green
    }

    Write-Host "[4/7] Installing Python dependencies..." -ForegroundColor Yellow
    & "$ROOT\venv\Scripts\pip.exe" install --upgrade pip --quiet
    # Pre-install a CUDA-enabled PyTorch before the rest of requirements so that
    # packages like open_clip_torch link against the GPU build, not the CPU fallback.
    Install-TorchIfNeeded
    if (-not [System.Console]::IsInputRedirected) {
        Write-Host "  Source: https://pypi.org/" -ForegroundColor DarkGray
        Write-Host "  Packages:" -ForegroundColor DarkGray
        Get-Content "$ROOT\backend\requirements.txt" | Where-Object { $_ -notmatch "^\s*#" -and $_.Trim() -ne "" } | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    }
    if ([System.Console]::IsInputRedirected) { $reply = "" } else {
        $reply = Read-Host "  Install Python backend dependencies from requirements.txt? [Y/n]"
    }
    if ($reply -ne "" -and $reply -notmatch "^[Yy]") {
        Write-Host "  Skipping Python dependencies." -ForegroundColor DarkGray
    } else {
        & "$ROOT\venv\Scripts\pip.exe" install -r "$ROOT\backend\requirements.txt"
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERROR: pip install failed." -ForegroundColor Red
            exit 1
        }
        Write-Host "  Python dependencies installed." -ForegroundColor Green
    }

    Write-Host "[5/7] Installing SAM2 (Segment Anything Model 2)..." -ForegroundColor Yellow
    if ([System.Console]::IsInputRedirected) { $reply = "" } else {
        $reply = Read-Host "  Download and install SAM2 from GitHub (~50 MB)? [Y/n]"
    }
    if ($reply -ne "" -and $reply -notmatch "^[Yy]") {
        Write-Host "  Skipping SAM2. Segmentation features will not be available." -ForegroundColor DarkGray
    } else {
        & "$ROOT\venv\Scripts\pip.exe" install "git+https://github.com/facebookresearch/sam2.git" pycocotools --quiet
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  SAM2 installed." -ForegroundColor Green
        } else {
            Write-Host "  WARNING: SAM2 install failed. Segmentation features will be unavailable." -ForegroundColor Yellow
            Write-Host "  To retry: .\venv\Scripts\pip.exe install git+https://github.com/facebookresearch/sam2.git pycocotools" -ForegroundColor DarkGray
        }
    }

    Write-Host "[6/7] Installing SAM3 (Segment Anything Model 3)..." -ForegroundColor Yellow
    if ([System.Console]::IsInputRedirected) { $reply = "" } else {
        $reply = Read-Host "  Download and install SAM3 from GitHub (~50 MB)? [Y/n]"
    }
    if ($reply -ne "" -and $reply -notmatch "^[Yy]") {
        Write-Host "  Skipping SAM3. SAM 3 text-prompt segmentation will not be available." -ForegroundColor DarkGray
    } else {
        # --no-deps: sam3 pins numpy<2 and ftfy==6.1.1, which conflict with
        # requirements.txt; its real runtime deps are installed explicitly.
        & "$ROOT\venv\Scripts\pip.exe" install "git+https://github.com/facebookresearch/sam3.git" --no-deps --quiet
        if ($LASTEXITCODE -eq 0) {
            & "$ROOT\venv\Scripts\pip.exe" install iopath ftfy pycocotools "setuptools<81" --quiet
        }
        $sam3Ok = ($LASTEXITCODE -eq 0)
        if ($sam3Ok) { Install-TritonWindows }
        if ($sam3Ok) {
            Write-Host "  SAM3 installed." -ForegroundColor Green
        } else {
            Write-Host "  WARNING: SAM3 install failed. SAM 3 text-prompt segmentation will be unavailable." -ForegroundColor Yellow
            Write-Host "  To retry: .\venv\Scripts\pip.exe install git+https://github.com/facebookresearch/sam3.git --no-deps; .\venv\Scripts\pip.exe install iopath ftfy pycocotools 'setuptools<81' triton-windows" -ForegroundColor DarkGray
        }
    }
    Write-Host "  NOTE: SAM3 also needs the checkpoint: download sam3.safetensors from https://huggingface.co/1038lab/sam3 into models\sam3\" -ForegroundColor DarkGray

    Write-Host "[7/7] Installing frontend dependencies and building..." -ForegroundColor Yellow
    Push-Location "$ROOT\frontend"
    npm install
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: npm install failed." -ForegroundColor Red
        Pop-Location
        exit 1
    }
    npm run build
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: npm run build failed." -ForegroundColor Red
        Pop-Location
        exit 1
    }
    Pop-Location
    Reset-Lockfile -Quiet
    Write-Host "  Frontend built." -ForegroundColor Green

    if (-not (Test-Path "$ROOT\.env")) {
        Copy-Item "$ROOT\.env.example" "$ROOT\.env"
        Write-Host ""
        Write-Host "  Created .env from .env.example. Edit it to add your HF_TOKEN if you plan to use PaliGemma-2." -ForegroundColor Yellow
    }

    Warn-IfCpuOnlyTorch
    Write-Host ""
    Write-Host "=== Setup complete! ===" -ForegroundColor Green
    Write-Host "Run .\manage.ps1 start to launch the app." -ForegroundColor Cyan
    Write-Host ""
}

function Cmd-Start {
    if (-not (Test-Path "$ROOT\venv\Scripts\Activate.ps1")) {
        Write-Host "Virtual environment not found. Running setup first..." -ForegroundColor Yellow
        Cmd-Setup
    }

    Activate-Venv

    Write-Host ""
    Write-Host "=== Crucible ===" -ForegroundColor Cyan

    Start-Splash
    try {
        Run-Migrations

        # Rebuild frontend only if source files are newer than the last build
        $distIndex = "$ROOT\frontend\dist\index.html"
        $needsBuild = -not (Test-Path $distIndex)

        if (-not $needsBuild) {
            $distTime = (Get-Item $distIndex).LastWriteTime
            $changed = Get-ChildItem "$ROOT\frontend" -Recurse -File |
                Where-Object { $_.FullName -notmatch '\\(node_modules|dist)\\' -and $_.LastWriteTime -gt $distTime }
            if ($changed) { $needsBuild = $true }
        }

        if ($needsBuild) { Build-Frontend }
    } finally {
        # Hand :8000 over. Has to happen before uvicorn binds, on every way out
        # of the block above - Run-Migrations exits the script on failure. The
        # restart loop below never brings the splash back: a restart is already
        # covered in-app by the TopBar overlay.
        Stop-Splash
    }

    Write-Host ""
    Write-Host "Starting server at http://localhost:8000" -ForegroundColor Green
    Write-Host "Press Ctrl+C to stop." -ForegroundColor DarkGray
    Write-Host ""

    Set-Location "$ROOT"

    # Clean up any stale sentinels from a previous crash
    if (Test-Path "$ROOT\.restart") { Remove-Item "$ROOT\.restart" -Force }
    if (Test-Path "$ROOT\.shutdown") { Remove-Item "$ROOT\.shutdown" -Force }

    :restart_loop while ($true) {
        python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
        if (Test-Path "$ROOT\.restart") {
            Remove-Item "$ROOT\.restart" -Force
            Write-Host ""
            Write-Host "Restarting server..." -ForegroundColor Yellow
            Write-Host ""
        } else {
            break restart_loop
        }
    }

    if (Test-Path "$ROOT\.shutdown") {
        Remove-Item "$ROOT\.shutdown" -Force
        exit 0
    }
}

function Cmd-Update {
    Write-Host ""
    Write-Host "=== Crucible - Update ===" -ForegroundColor Cyan
    Write-Host ""

    Write-Host "[1/6] Pulling latest changes..." -ForegroundColor Yellow
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Host "  git not found - skipping pull. Update the files manually if needed." -ForegroundColor DarkGray
    } else {
        # Clear npm's lockfile churn first, or the pull aborts on it.
        Reset-Lockfile

        # PowerShell parses this entire file into an AST before executing a single
        # line, so a pull that rewrites manage.ps1 has no effect on the run that
        # pulled it - the rest of the update silently executes the OLD script, and
        # the user has to run update twice for new logic to apply. Hash the script
        # around the pull and hand off to the new version if it changed.
        $selfHashBefore = (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash
        git -C "$ROOT" pull
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERROR: git pull failed. Resolve any conflicts and try again." -ForegroundColor Red
            exit 1
        }
        Write-Host "  Done." -ForegroundColor Green

        $selfHashAfter = (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash
        if ($selfHashBefore -ne $selfHashAfter) {
            if ($env:CRUCIBLE_SELF_UPDATED -eq "1") {
                # Already handed off once this update; never loop, even if the file
                # somehow keeps changing.
                Write-Host "  manage.ps1 changed again - continuing with the current version." -ForegroundColor DarkGray
            } else {
                Write-Host "  manage.ps1 was updated - restarting with the new version..." -ForegroundColor Yellow
                $env:CRUCIBLE_SELF_UPDATED = "1"
                # Re-invoke through the host that is actually running this script
                # (powershell.exe or pwsh.exe) instead of assuming either one.
                $psHost = (Get-Process -Id $PID).Path
                if (-not $psHost) { $psHost = "powershell" }
                & $psHost -ExecutionPolicy Bypass -NoProfile -File $PSCommandPath update
                exit $LASTEXITCODE
            }
        }
    }

    if (-not (Test-Path "$ROOT\venv\Scripts\Activate.ps1")) {
        Write-Host "Virtual environment not found. Running setup first..." -ForegroundColor Yellow
        Cmd-Setup
        Write-Host ""
        Write-Host "=== Update complete (full setup was run) ===" -ForegroundColor Green
        return
    }

    Activate-Venv

    # Verify the venv Python meets the 3.12+ requirement before continuing.
    $venvPyVer = & "$ROOT\venv\Scripts\python.exe" --version 2>&1
    if ($venvPyVer -match "Python (\d+)\.(\d+)") {
        $pvMaj = [int]$Matches[1]; $pvMin = [int]$Matches[2]
        $pvPreRelease = "$venvPyVer" -match "(a|b|rc)\d+"
        if ($pvPreRelease -or -not ($pvMaj -gt 3 -or ($pvMaj -eq 3 -and $pvMin -ge 12))) {
            Write-Host ""
            if ($pvPreRelease) {
                Write-Host "ERROR: Pre-release Python ($venvPyVer) is not supported - packages like scipy and torch have no wheels for it." -ForegroundColor Red
            } else {
                Write-Host "ERROR: Python 3.12+ is now required, but your venv uses $venvPyVer." -ForegroundColor Red
            }
            Write-Host "  To fix:" -ForegroundColor Yellow
            Write-Host "  1. Install a stable Python 3.12+ from https://www.python.org/downloads/" -ForegroundColor Cyan
            Write-Host "  2. Delete the venv/ directory: Remove-Item -Recurse -Force venv\" -ForegroundColor Cyan
            Write-Host "  3. Re-run: .\manage.ps1 setup" -ForegroundColor Cyan
            Write-Host ""
            exit 1
        }
    }

    Write-Host "[2/6] Updating Python dependencies..." -ForegroundColor Yellow
    & "$ROOT\venv\Scripts\pip.exe" install --upgrade pip --quiet
    Install-TorchIfNeeded
    if (-not [System.Console]::IsInputRedirected) {
        Write-Host "  Source: https://pypi.org/" -ForegroundColor DarkGray
        Write-Host "  Packages:" -ForegroundColor DarkGray
        Get-Content "$ROOT\backend\requirements.txt" | Where-Object { $_ -notmatch "^\s*#" -and $_.Trim() -ne "" } | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    }
    if ([System.Console]::IsInputRedirected) { $reply = "" } else {
        $reply = Read-Host "  Update Python backend dependencies from requirements.txt? [Y/n]"
    }
    if ($reply -ne "" -and $reply -notmatch "^[Yy]") {
        Write-Host "  Skipping Python dependencies." -ForegroundColor DarkGray
    } else {
        & "$ROOT\venv\Scripts\pip.exe" install -r "$ROOT\backend\requirements.txt"
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERROR: pip install failed." -ForegroundColor Red
            exit 1
        }
        Write-Host "  Done." -ForegroundColor Green
    }

    Write-Host "[3/6] Installing/updating SAM2..." -ForegroundColor Yellow
    if ([System.Console]::IsInputRedirected) { $reply = "" } else {
        $reply = Read-Host "  Install or update SAM2 from GitHub? [Y/n]"
    }
    if ($reply -ne "" -and $reply -notmatch "^[Yy]") {
        Write-Host "  Skipping SAM2." -ForegroundColor DarkGray
    } else {
        & "$ROOT\venv\Scripts\pip.exe" install "git+https://github.com/facebookresearch/sam2.git" pycocotools --quiet
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  SAM2 up to date." -ForegroundColor Green
        } else {
            Write-Host "  WARNING: SAM2 install failed." -ForegroundColor Yellow
        }
    }

    Write-Host "[4/6] Installing/updating SAM3..." -ForegroundColor Yellow
    if ([System.Console]::IsInputRedirected) { $reply = "" } else {
        $reply = Read-Host "  Install or update SAM3 from GitHub? [Y/n]"
    }
    if ($reply -ne "" -and $reply -notmatch "^[Yy]") {
        Write-Host "  Skipping SAM3." -ForegroundColor DarkGray
    } else {
        # --no-deps: sam3 pins numpy<2 and ftfy==6.1.1, which conflict with
        # requirements.txt; its real runtime deps are installed explicitly.
        & "$ROOT\venv\Scripts\pip.exe" install "git+https://github.com/facebookresearch/sam3.git" --no-deps --quiet
        if ($LASTEXITCODE -eq 0) {
            & "$ROOT\venv\Scripts\pip.exe" install iopath ftfy pycocotools "setuptools<81" --quiet
        }
        $sam3Ok = ($LASTEXITCODE -eq 0)
        if ($sam3Ok) { Install-TritonWindows }
        if ($sam3Ok) {
            Write-Host "  SAM3 up to date." -ForegroundColor Green
        } else {
            Write-Host "  WARNING: SAM3 install failed." -ForegroundColor Yellow
        }
    }
    Write-Host "  NOTE: SAM3 also needs the checkpoint: download sam3.safetensors from https://huggingface.co/1038lab/sam3 into models\sam3\" -ForegroundColor DarkGray

    Write-Host "[5/6] Updating frontend dependencies..." -ForegroundColor Yellow
    Push-Location "$ROOT\frontend"
    npm install
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: npm install failed." -ForegroundColor Red
        Pop-Location
        exit 1
    }
    Pop-Location
    # node_modules is already installed; drop any lockfile churn npm just made so
    # the tree is clean for the next pull. This is the prevention - the reset
    # before the pull above only rescues a machine that is already dirty.
    Reset-Lockfile -Quiet
    Write-Host "  Done." -ForegroundColor Green

    Write-Host "[6/6] Building frontend..." -ForegroundColor Yellow
    Build-Frontend

    Warn-IfCpuOnlyTorch
    Write-Host ""
    Write-Host "=== Update complete! ===" -ForegroundColor Green
    Write-Host "Database migrations will run automatically on next start." -ForegroundColor DarkGray
    Write-Host ""
}

function Cmd-Dev {
    if (-not (Test-Path "$ROOT\venv\Scripts\Activate.ps1")) {
        Write-Host "Virtual environment not found. Run .\manage.ps1 setup first." -ForegroundColor Red
        exit 1
    }

    Activate-Venv

    Push-Location "$ROOT\backend"
    python -m alembic upgrade head
    Pop-Location

    Write-Host "Starting backend on :8000 and frontend dev server on :5173..." -ForegroundColor Cyan
    Write-Host "Open http://localhost:5173 in your browser." -ForegroundColor Green
    Write-Host ""

    $backendJob = Start-Job -ScriptBlock {
        param($root)
        Set-Location $root

        if (Test-Path "$root\.restart") { Remove-Item "$root\.restart" -Force }

        :restart_loop while ($true) {
            & "$root\venv\Scripts\python.exe" -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir backend
            if (Test-Path "$root\.restart") {
                Remove-Item "$root\.restart" -Force
                Write-Host ""
                Write-Host "Restarting backend..." -ForegroundColor Yellow
                Write-Host ""
            } else {
                break restart_loop
            }
        }
    } -ArgumentList $ROOT

    Push-Location "$ROOT\frontend"
    npm run dev
    Pop-Location

    Stop-Job $backendJob -ErrorAction SilentlyContinue
    Remove-Job $backendJob -ErrorAction SilentlyContinue
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

if ($Command -eq "") {
    Write-Host ""
    Write-Host "=== Crucible ===" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  1) Setup   - First-time install (creates venv, installs deps, builds frontend)"
    Write-Host "  2) Start   - Launch the app at http://localhost:8000"
    Write-Host "  3) Update  - Pull latest changes and rebuild"
    Write-Host ""
    $choice = Read-Host "Enter choice [1-3]"
    switch ($choice) {
        "1" { $Command = "setup" }
        "2" { $Command = "start" }
        "3" { $Command = "update" }
        default {
            Write-Host "Invalid choice. Run .\manage.ps1 <setup|start|update|dev>" -ForegroundColor Red
            exit 1
        }
    }
}

switch ($Command) {
    "setup"  { Cmd-Setup }
    "start"  { Cmd-Start }
    "update" { Cmd-Update }
    "dev"    { Cmd-Dev }
}
