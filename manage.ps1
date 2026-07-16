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
            Write-Host "  Source: $indexUrl (~2.5 GB)" -ForegroundColor DarkGray
            if ([System.Console]::IsInputRedirected) { $reply = "" } else {
                $reply = Read-Host "  Install GPU-accelerated PyTorch ($tag)? [Y/n]"
            }
            if ($reply -ne "" -and $reply -notmatch "^[Yy]") {
                Write-Host "  Skipping GPU PyTorch - CPU-only will be installed via requirements.txt." -ForegroundColor DarkGray
                return
            }
            Write-Host "  Installing PyTorch ($tag) from PyTorch wheel index..." -ForegroundColor Yellow
            & "$ROOT\venv\Scripts\pip.exe" install "torch>=2.7" --index-url $indexUrl --quiet
            if ($LASTEXITCODE -eq 0) {
                Write-Host "  CUDA-enabled PyTorch ($tag) installed." -ForegroundColor Green
            } else {
                Write-Host "  WARNING: CUDA torch install failed - CPU-only fallback will be used." -ForegroundColor Yellow
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

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

function Cmd-Setup {
    Write-Host ""
    Write-Host "=== Crucible - First-Time Setup ===" -ForegroundColor Cyan
    Write-Host ""

    Install-Deps

    Write-Host "[3/6] Creating Python virtual environment..." -ForegroundColor Yellow
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

    Write-Host "[4/6] Installing Python dependencies..." -ForegroundColor Yellow
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

    Write-Host "[5/6] Installing SAM2 (Segment Anything Model 2)..." -ForegroundColor Yellow
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

    Write-Host "[6/6] Installing frontend dependencies and building..." -ForegroundColor Yellow
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
    Write-Host "  Frontend built." -ForegroundColor Green

    if (-not (Test-Path "$ROOT\.env")) {
        Copy-Item "$ROOT\.env.example" "$ROOT\.env"
        Write-Host ""
        Write-Host "  Created .env from .env.example. Edit it to add your HF_TOKEN if you plan to use PaliGemma-2." -ForegroundColor Yellow
    }

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

    Write-Host "[1/5] Pulling latest changes..." -ForegroundColor Yellow
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Host "  git not found - skipping pull. Update the files manually if needed." -ForegroundColor DarkGray
    } else {
        git -C "$ROOT" pull
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERROR: git pull failed. Resolve any conflicts and try again." -ForegroundColor Red
            exit 1
        }
        Write-Host "  Done." -ForegroundColor Green
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

    Write-Host "[2/5] Updating Python dependencies..." -ForegroundColor Yellow
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

    Write-Host "[3/5] Installing/updating SAM2..." -ForegroundColor Yellow
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

    Write-Host "[4/5] Updating frontend dependencies..." -ForegroundColor Yellow
    Push-Location "$ROOT\frontend"
    npm install
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: npm install failed." -ForegroundColor Red
        Pop-Location
        exit 1
    }
    Pop-Location
    Write-Host "  Done." -ForegroundColor Green

    Write-Host "[5/5] Building frontend..." -ForegroundColor Yellow
    Build-Frontend

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
