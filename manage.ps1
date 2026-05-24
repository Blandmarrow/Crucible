# manage.ps1 - Crucible launcher (Windows)
# Usage: .\manage.ps1 <setup|start|update|dev>

param(
    [Parameter(Mandatory = $false, Position = 0)]
    [ValidateSet("setup", "start", "update", "dev", "")]
    [string]$Command = ""
)

$ErrorActionPreference = "Stop"
$ROOT = $PSScriptRoot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Check-Deps {
    Write-Host "[1/2] Checking Python..." -ForegroundColor Yellow
    $pythonVersion = python --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Python not found. Please install Python 3.10+ and add it to PATH." -ForegroundColor Red
        exit 1
    }
    Write-Host "  Found: $pythonVersion" -ForegroundColor Green

    Write-Host "[2/2] Checking Node.js..." -ForegroundColor Yellow
    $nodeVersion = node --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Node.js not found. Please install Node.js 18+ and add it to PATH." -ForegroundColor Red
        exit 1
    }
    Write-Host "  Found: Node $nodeVersion" -ForegroundColor Green
}

function Activate-Venv {
    & "$ROOT\venv\Scripts\Activate.ps1"
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

    Check-Deps

    Write-Host "[3/5] Creating Python virtual environment..." -ForegroundColor Yellow
    if (Test-Path "$ROOT\venv") {
        Write-Host "  venv already exists, skipping creation." -ForegroundColor DarkGray
    } else {
        python -m venv --system-site-packages "$ROOT\venv"
        Write-Host "  venv created at $ROOT\venv (inherits system ML packages)" -ForegroundColor Green
    }

    Write-Host "[4/5] Installing Python dependencies..." -ForegroundColor Yellow
    & "$ROOT\venv\Scripts\pip.exe" install --upgrade pip --quiet
    & "$ROOT\venv\Scripts\pip.exe" install -r "$ROOT\backend\requirements.txt"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: pip install failed." -ForegroundColor Red
        exit 1
    }
    Write-Host "  Python dependencies installed." -ForegroundColor Green

    Write-Host "[5/5] Installing frontend dependencies and building..." -ForegroundColor Yellow
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
    python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
}

function Cmd-Update {
    Write-Host ""
    Write-Host "=== Crucible - Update ===" -ForegroundColor Cyan
    Write-Host ""

    Write-Host "[1/4] Pulling latest changes..." -ForegroundColor Yellow
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

    Write-Host "[2/4] Updating Python dependencies..." -ForegroundColor Yellow
    & "$ROOT\venv\Scripts\pip.exe" install --upgrade pip --quiet
    & "$ROOT\venv\Scripts\pip.exe" install -r "$ROOT\backend\requirements.txt"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: pip install failed." -ForegroundColor Red
        exit 1
    }
    Write-Host "  Done." -ForegroundColor Green

    Write-Host "[3/4] Updating frontend dependencies..." -ForegroundColor Yellow
    Push-Location "$ROOT\frontend"
    npm install
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: npm install failed." -ForegroundColor Red
        Pop-Location
        exit 1
    }
    Pop-Location
    Write-Host "  Done." -ForegroundColor Green

    Write-Host "[4/4] Building frontend..." -ForegroundColor Yellow
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
        & "$root\venv\Scripts\python.exe" -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir backend
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
