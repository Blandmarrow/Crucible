#!/usr/bin/env bash
# manage.sh - Crucible launcher (Linux / macOS)
# Usage: ./manage.sh <setup|start|update|dev>

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_python_version_ok() {
    # Returns 0 if $1 is stable Python 3.12+ (pre-release versions are rejected)
    command -v "$1" &>/dev/null || return 1
    local fullver ver maj min
    fullver=$("$1" --version 2>&1 || true)
    # Reject alpha/beta/rc pre-release versions — no wheels exist yet for them
    echo "$fullver" | grep -qE "[0-9](a|b|rc)[0-9]" && return 1
    ver=$(echo "$fullver" | grep -oE "[0-9]+\.[0-9]+" | head -1 || true)
    [ -n "$ver" ] || return 1
    maj=$(echo "$ver" | cut -d. -f1)
    min=$(echo "$ver" | cut -d. -f2)
    [ "$maj" -gt 3 ] || { [ "$maj" -eq 3 ] && [ "$min" -ge 12 ]; }
}

_node_version_ok() {
    command -v node &>/dev/null || return 1
    local maj
    maj=$(node --version 2>&1 | grep -oE "^v[0-9]+" | grep -oE "[0-9]+" || true)
    [ -n "$maj" ] && [ "$maj" -ge 18 ]
}

_install_node_nvm() {
    local nvm_dir="$HOME/.nvm"
    echo "  Installing nvm (Node Version Manager)..."
    if [ ! -s "$nvm_dir/nvm.sh" ]; then
        curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash || true
    fi
    if [ ! -s "$nvm_dir/nvm.sh" ]; then
        echo "ERROR: nvm install failed. Install Node.js 18+ from https://nodejs.org/" >&2
        exit 1
    fi
    export NVM_DIR="$nvm_dir"
    # shellcheck source=/dev/null
    source "$NVM_DIR/nvm.sh"
    nvm install --lts
    nvm use --lts
}

_install_node_linux() {
    if command -v apt-get &>/dev/null; then
        if command -v curl &>/dev/null; then
            echo "  Fetching NodeSource LTS repository..."
            curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash - || true
        else
            sudo apt-get update -qq || true
        fi
        sudo apt-get install -y nodejs || true
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y nodejs || true
    elif command -v pacman &>/dev/null; then
        sudo pacman -Sy --noconfirm nodejs npm || true
    fi
    # Fall back to nvm if the package manager did not give us 18+
    if ! _node_version_ok; then
        _install_node_nvm
    fi
}

_install_deps() {
    local os
    os="$(uname -s)"

    # --- Python ---
    echo "[1/7] Checking Python..."
    PYTHON=""
    if _python_version_ok python3.12; then
        PYTHON=python3.12
        echo "  Found: $($PYTHON --version)"
    elif _python_version_ok python3; then
        PYTHON=python3
        echo "  Found: $($PYTHON --version)"
    elif _python_version_ok python; then
        PYTHON=python
        echo "  Found: $($PYTHON --version)"
    else
        for cmd in python3 python; do
            if command -v "$cmd" &>/dev/null; then
                local _v; _v="$($cmd --version 2>&1)"
                if echo "$_v" | grep -qE "[0-9](a|b|rc)[0-9]"; then
                    echo "  Found $_v - pre-release Python is not supported; use a stable 3.12+ release."
                else
                    echo "  Found $_v - 3.12+ is required."
                fi
            fi
        done
        echo "  Python 3.12+ is required but not found."
        echo "  Source: https://www.python.org/ (via package manager)"
        printf "  Install now? [Y/n] "
        read -r _reply || true
        case "$_reply" in
            [Nn]*) echo "  Install Python 3.12+ from: https://www.python.org/downloads/"; exit 1 ;;
        esac
        echo "  Installing Python 3.12+..."
        if [ "$os" = "Darwin" ]; then
            if command -v brew &>/dev/null; then
                brew install python@3.12
                export PATH="$(brew --prefix)/bin:$PATH"
            else
                echo "ERROR: Homebrew not found. Install from https://brew.sh/ then re-run setup." >&2
                exit 1
            fi
        elif [ "$os" = "Linux" ]; then
            if command -v apt-get &>/dev/null; then
                sudo apt-get update -qq
                sudo apt-get install -y python3 python3-venv python3-pip
            elif command -v dnf &>/dev/null; then
                sudo dnf install -y python3 python3-pip
            elif command -v pacman &>/dev/null; then
                sudo pacman -Sy --noconfirm python python-pip
            else
                echo "ERROR: No supported package manager (apt, dnf, pacman) found." >&2
                echo "  Install Python 3.12+ from https://www.python.org/downloads/" >&2
                exit 1
            fi
        else
            echo "ERROR: Unsupported OS." >&2
            echo "  Install Python 3.12+ from https://www.python.org/downloads/" >&2
            exit 1
        fi
        if _python_version_ok python3.12; then
            PYTHON=python3.12
        elif _python_version_ok python3; then
            PYTHON=python3
        elif _python_version_ok python; then
            PYTHON=python
        else
            echo "ERROR: Python 3.12+ install failed. Please install manually." >&2
            exit 1
        fi
        echo "  Installed: $($PYTHON --version)"
    fi

    # --- Node.js ---
    echo "[2/7] Checking Node.js..."
    if _node_version_ok; then
        echo "  Found: Node $(node --version)"
    else
        if command -v node &>/dev/null; then
            echo "  Found Node $(node --version) - 18+ is required."
        fi
        echo "  Node.js 18+ is required but not found."
        echo "  Source: https://nodejs.org/ (via package manager)"
        printf "  Install now? [Y/n] "
        read -r _reply || true
        case "$_reply" in
            [Nn]*) echo "  Install Node.js 18+ from: https://nodejs.org/"; exit 1 ;;
        esac
        echo "  Installing Node.js 18+..."
        if [ "$os" = "Darwin" ]; then
            if command -v brew &>/dev/null; then
                brew install node
            else
                echo "ERROR: Homebrew not found. Install from https://brew.sh/ then re-run setup." >&2
                exit 1
            fi
        elif [ "$os" = "Linux" ]; then
            _install_node_linux
        else
            echo "ERROR: Unsupported OS." >&2
            echo "  Install Node.js 18+ from https://nodejs.org/" >&2
            exit 1
        fi
        if ! _node_version_ok; then
            echo "ERROR: Node.js 18+ install failed. Install manually from https://nodejs.org/" >&2
            exit 1
        fi
        echo "  Installed: Node $(node --version)"
    fi
}

_activate() {
    # shellcheck source=/dev/null
    source "$ROOT/venv/bin/activate"
}

_build_frontend() {
    echo "Building frontend..."
    cd "$ROOT/frontend"
    npm run build
    cd "$ROOT"
    echo "  Frontend built."
}

_migrate() {
    echo "Running database migrations..."
    cd "$ROOT/backend"
    python -m alembic upgrade head
    cd "$ROOT"
    echo "  Migrations applied."
}

# Echo the newest PyTorch CUDA wheel tag (e.g. cu130) that the driver's CUDA
# version supports, by querying the live wheel index at download.pytorch.org.
# Args: driver CUDA major, minor. Prints the chosen "cuNNN" tag (empty if the
# index was reached but nothing suitable ≤ driver exists) and returns 0.
# Returns 1 without printing if the index could not be fetched, so the caller
# can fall back to a built-in version table.
#
# Wheel-tag convention (matches PyTorch's own naming): the last digit is the
# CUDA minor, the preceding digits are the major — cu128 → 12.8, cu130 → 13.0.
# PyTorch 2.7+ requires CUDA 12.6+, so tags below cu126 are ignored.
_query_cuda_tag() {
    local dmaj="$1" dmin="$2" idx="https://download.pytorch.org/whl/"
    local html driver_ver best_ver=0 best_tag="" tag num tmaj tmin tver
    driver_ver=$((dmaj * 100 + dmin))

    if command -v curl &>/dev/null; then
        html=$(curl -fsSL --max-time 10 "$idx" 2>/dev/null) || html=""
    elif command -v wget &>/dev/null; then
        html=$(wget -qO- --timeout=10 "$idx" 2>/dev/null) || html=""
    fi
    [ -z "$html" ] && return 1

    # Anchor on real directory links (href="cuNNN/") so unrelated substrings
    # like cudnn-cu13 / nvidia-nccl-cu12 don't get mistaken for wheel tags.
    for tag in $(echo "$html" | grep -oE 'href="cu[0-9]+/"' | grep -oE 'cu[0-9]+' | sort -u); do
        num="${tag#cu}"
        [ "${#num}" -lt 2 ] && continue
        tmin="${num: -1}"
        tmaj="${num:0:${#num}-1}"
        tver=$((10#$tmaj * 100 + 10#$tmin))
        if [ "$tver" -le "$driver_ver" ] && [ "$tver" -ge 1206 ] && [ "$tver" -gt "$best_ver" ]; then
            best_ver=$tver
            best_tag=$tag
        fi
    done
    echo "$best_tag"
    return 0
}

_install_torch_if_needed() {
    # Fast-path: skip if a GPU-capable torch is already present.
    # Covers both CUDA (NVIDIA/ROCm) and MPS (Apple Silicon).
    if "$ROOT/venv/bin/python" -c \
        "import torch; assert torch.cuda.is_available() or (hasattr(torch.backends,'mps') and torch.backends.mps.is_available())" \
        2>/dev/null; then
        echo "  GPU-enabled PyTorch already available — skipping."
        return
    fi

    # --- NVIDIA detection ---
    # nvidia-smi ships with every NVIDIA driver; no CUDA toolkit required.
    if command -v nvidia-smi &>/dev/null; then
        local nv_out cuda_ver maj min tag=""
        nv_out=$(nvidia-smi 2>/dev/null || true)
        cuda_ver=$(echo "$nv_out" | grep -oE "CUDA Version: [0-9]+\.[0-9]+" \
                   | grep -oE "[0-9]+\.[0-9]+" | head -1)

        if [ -n "$cuda_ver" ]; then
            maj=$(echo "$cuda_ver" | cut -d. -f1)
            min=$(echo "$cuda_ver" | cut -d. -f2)
            echo "  NVIDIA GPU detected — driver supports CUDA $maj.$min."

            # Pick the newest wheel the driver supports by querying the live
            # wheel index; fall back to a built-in table if it's unreachable.
            # PyTorch 2.7+ requires CUDA 12.6+ (driver 560.94+ on Linux).
            if ! tag=$(_query_cuda_tag "$maj" "$min"); then
                echo "  Could not reach PyTorch wheel index — using built-in version table."
                if   [ "$maj" -gt 12 ] || { [ "$maj" -eq 12 ] && [ "$min" -ge 8 ]; }; then tag="cu128"
                elif [ "$maj" -eq 12 ] && [ "$min" -ge 6 ]; then tag="cu126"
                fi
            fi

            if [ -n "$tag" ]; then
                local index_url="https://download.pytorch.org/whl/$tag"
                echo "  Source: $index_url (~2.5 GB)"
                printf "  Install GPU-accelerated PyTorch (%s)? [Y/n] " "$tag"
                read -r _reply || true
                case "$_reply" in
                    [Nn]*) echo "  Skipping GPU PyTorch - CPU-only will be installed via requirements.txt."; return ;;
                esac
                echo "  Installing PyTorch ($tag) from PyTorch wheel index..."
                if "$ROOT/venv/bin/pip" install "torch>=2.7" --index-url "$index_url" --quiet; then
                    echo "  CUDA-enabled PyTorch ($tag) installed."
                else
                    echo "  WARNING: CUDA torch install failed — CPU-only fallback will be used."
                fi
                return
            else
                echo "  CUDA $maj.$min is older than 12.6 — GPU-accelerated PyTorch is not available."
                echo "  CPU-only PyTorch will be installed via requirements.txt."
                echo "  To enable GPU support, update your NVIDIA driver (560.94+) and re-run setup."
            fi
        fi
    fi

    # --- AMD ROCm detection (Linux only) ---
    # rocm-smi is present when the ROCm userspace stack is installed.
    if [ "$(uname -s)" = "Linux" ] && command -v rocm-smi &>/dev/null; then
        # Detect ROCm version via three fallbacks in order.
        local rocm_ver=""
        if command -v rocminfo &>/dev/null; then
            rocm_ver=$(rocminfo 2>/dev/null | grep -oE "ROCm[- ][0-9]+\.[0-9]+" \
                       | grep -oE "[0-9]+\.[0-9]+" | head -1)
        fi
        if [ -z "$rocm_ver" ]; then
            rocm_ver=$(ls -d /opt/rocm-* 2>/dev/null \
                       | grep -oE "[0-9]+\.[0-9]+" | sort -V | tail -1)
        fi
        if [ -z "$rocm_ver" ] && [ -f /opt/rocm/VERSION ]; then
            rocm_ver=$(cat /opt/rocm/VERSION 2>/dev/null | grep -oE "^[0-9]+\.[0-9]+")
        fi

        local rocm_tag=""
        if [ -n "$rocm_ver" ]; then
            local rmaj rmin
            rmaj=$(echo "$rocm_ver" | cut -d. -f1)
            rmin=$(echo "$rocm_ver" | cut -d. -f2)
            echo "  AMD GPU detected — ROCm $rmaj.$rmin."

            # PyTorch 2.7+ requires ROCm 6.3+.
            if [ "$rmaj" -gt 6 ] || { [ "$rmaj" -eq 6 ] && [ "$rmin" -ge 3 ]; }; then
                rocm_tag="rocm6.3"
            else
                echo "  ERROR: ROCm $rmaj.$rmin is too old for PyTorch 2.7 (requires ROCm 6.3+)." >&2
                echo "  Please update your ROCm stack, then delete venv/ and re-run setup." >&2
                exit 1
            fi
        else
            echo "  AMD GPU detected (ROCm version unknown) — trying rocm6.3 wheel."
            rocm_tag="rocm6.3"
        fi

        local index_url="https://download.pytorch.org/whl/$rocm_tag"
        echo "  Source: $index_url (~2.5 GB)"
        printf "  Install GPU-accelerated PyTorch (%s)? [Y/n] " "$rocm_tag"
        read -r _reply || true
        case "$_reply" in
            [Nn]*) echo "  Skipping GPU PyTorch - CPU-only will be installed via requirements.txt."; return ;;
        esac
        echo "  Installing PyTorch ($rocm_tag) from PyTorch wheel index..."
        if "$ROOT/venv/bin/pip" install "torch>=2.7" --index-url "$index_url" --quiet; then
            echo "  ROCm-enabled PyTorch ($rocm_tag) installed."
        else
            echo "  WARNING: ROCm torch install failed — CPU-only fallback will be used."
        fi
        return
    fi

    # --- macOS (all architectures) ---
    # Standard CPU PyTorch includes MPS support for Apple Silicon.
    # No special wheel is needed; requirements.txt handles the install.
    if [ "$(uname -s)" = "Darwin" ]; then
        echo "  macOS detected — standard PyTorch (includes MPS for Apple Silicon) will be installed via requirements.txt."
        return
    fi

    # --- CPU-only fallback ---
    echo "  No supported GPU detected — PyTorch will be CPU-only."
    echo "  ML features (captioning, scoring) run significantly faster with a supported GPU."
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

cmd_setup() {
    echo ""
    echo "=== Crucible - First-Time Setup ==="
    echo ""

    _install_deps

    echo "[3/7] Creating Python virtual environment..."
    _do_create_venv=true
    if [ -d "$ROOT/venv" ]; then
        _venv_py="$ROOT/venv/bin/python"
        if [ -f "$_venv_py" ] && _python_version_ok "$_venv_py"; then
            echo "  venv already exists, skipping creation."
            _do_create_venv=false
        else
            _venv_ver=$("$_venv_py" --version 2>&1 || echo "unknown")
            echo "  Existing venv uses $_venv_ver -- recreating with $($PYTHON --version)..."
            rm -rf "$ROOT/venv"
        fi
    fi
    if [ "$_do_create_venv" = true ]; then
        $PYTHON -m venv --system-site-packages "$ROOT/venv"
        echo "  venv created at $ROOT/venv (inherits system site-packages)"
    fi

    echo "[4/7] Installing Python dependencies..."
    "$ROOT/venv/bin/pip" install --upgrade pip --quiet
    # Pre-install a CUDA-enabled PyTorch before the rest of requirements so that
    # packages like open_clip_torch link against the GPU build, not the CPU fallback.
    _install_torch_if_needed
    if [ ! -f "$ROOT/backend/requirements.txt" ]; then
        echo "ERROR: requirements.txt not found at $ROOT/backend/requirements.txt" >&2
        exit 1
    fi
    if [ -t 0 ]; then
        echo "  Source: https://pypi.org/"
        echo "  Packages:"
        grep -v '^[[:space:]]*#' "$ROOT/backend/requirements.txt" | grep -v '^[[:space:]]*$' | while IFS= read -r _pkg; do echo "    $_pkg"; done || true
    fi
    printf "  Install Python backend dependencies from requirements.txt? [Y/n] "
    read -r _reply || true
    case "$_reply" in
        [Nn]*) echo "  Skipping Python dependencies." ;;
        *)
            "$ROOT/venv/bin/pip" install -r "$ROOT/backend/requirements.txt"
            echo "  Python dependencies installed."
            ;;
    esac

    echo "[5/7] Installing SAM2 (Segment Anything Model 2)..."
    printf "  Download and install SAM2 from GitHub (~50 MB)? [Y/n] "
    read -r _reply || true
    case "$_reply" in
        [Nn]*) echo "  Skipping SAM2. Segmentation features will not be available." ;;
        *)
            if "$ROOT/venv/bin/pip" install "git+https://github.com/facebookresearch/sam2.git" pycocotools --quiet; then
                echo "  SAM2 installed."
            else
                echo "  WARNING: SAM2 install failed. Segmentation features will be unavailable."
                echo "  To retry: ./venv/bin/pip install git+https://github.com/facebookresearch/sam2.git pycocotools"
            fi
            ;;
    esac

    echo "[6/7] Installing SAM3 (Segment Anything Model 3)..."
    printf "  Download and install SAM3 from GitHub (~50 MB)? [Y/n] "
    read -r _reply || true
    case "$_reply" in
        [Nn]*) echo "  Skipping SAM3. SAM 3 text-prompt segmentation will not be available." ;;
        *)
            # --no-deps: sam3 pins numpy<2 and ftfy==6.1.1, which conflict with
            # requirements.txt; its real runtime deps are installed explicitly.
            if "$ROOT/venv/bin/pip" install "git+https://github.com/facebookresearch/sam3.git" --no-deps --quiet \
                && "$ROOT/venv/bin/pip" install iopath ftfy pycocotools "setuptools<81" --quiet; then
                echo "  SAM3 installed."
            else
                echo "  WARNING: SAM3 install failed. SAM 3 text-prompt segmentation will be unavailable."
                echo "  To retry: ./venv/bin/pip install git+https://github.com/facebookresearch/sam3.git --no-deps && ./venv/bin/pip install iopath ftfy pycocotools \"setuptools<81\""
            fi
            ;;
    esac
    echo "  NOTE: SAM3 also needs the checkpoint: download sam3.safetensors from https://huggingface.co/1038lab/sam3 into models/sam3/"

    echo "[7/7] Installing frontend dependencies and building..."
    cd "$ROOT/frontend"
    npm install
    npm run build
    cd "$ROOT"
    echo "  Frontend built."

    if [ ! -f "$ROOT/.env" ]; then
        cp "$ROOT/.env.example" "$ROOT/.env"
        echo ""
        echo "  Created .env from .env.example."
        echo "  Edit it to add your HF_TOKEN if you plan to use PaliGemma-2."
    fi

    echo ""
    echo "=== Setup complete! ==="
    echo "Run ./manage.sh start to launch the app."
    echo ""
}

cmd_start() {
    if [ ! -f "$ROOT/venv/bin/activate" ]; then
        echo "Virtual environment not found. Running setup first..."
        cmd_setup
    fi

    _activate

    echo ""
    echo "=== Crucible ==="

    _migrate

    # Rebuild frontend only if source files are newer than the last build
    local dist_index="$ROOT/frontend/dist/index.html"
    local needs_build=false

    if [ ! -f "$dist_index" ]; then
        needs_build=true
    elif find "$ROOT/frontend" \( -path "*/node_modules" -o -path "*/dist" \) -prune \
            -o -newer "$dist_index" -print | grep -q .; then
        needs_build=true
    fi

    if [ "$needs_build" = true ]; then
        _build_frontend
    fi

    echo ""
    echo "Starting server at http://localhost:8000"
    echo "Press Ctrl+C to stop."
    echo ""

    cd "$ROOT"

    # Clean up any stale sentinels from a previous crash
    rm -f "$ROOT/.restart"
    rm -f "$ROOT/.shutdown"

    while true; do
        python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 || true
        if [ -f "$ROOT/.restart" ]; then
            rm -f "$ROOT/.restart"
            echo ""
            echo "Restarting server..."
            echo ""
        else
            break
        fi
    done
}

cmd_update() {
    echo ""
    echo "=== Crucible - Update ==="
    echo ""

    echo "[1/5] Pulling latest changes..."
    if ! command -v git &>/dev/null; then
        echo "  git not found - skipping pull. Update the files manually if needed."
    else
        git -C "$ROOT" pull
        echo "  Done."
    fi

    if [ ! -f "$ROOT/venv/bin/activate" ]; then
        echo "Virtual environment not found. Running setup first..."
        cmd_setup
        echo ""
        echo "=== Update complete (full setup was run) ==="
        return
    fi

    _activate

    # Verify the venv Python meets the 3.12+ requirement before continuing.
    local venv_py_ver
    venv_py_ver=$("$ROOT/venv/bin/python" --version 2>&1 | grep -oE "[0-9]+\.[0-9]+" | head -1 || true)
    if [ -n "$venv_py_ver" ]; then
        local pv_maj pv_min
        pv_maj=$(echo "$venv_py_ver" | cut -d. -f1)
        pv_min=$(echo "$venv_py_ver" | cut -d. -f2)
        if ! { [ "$pv_maj" -gt 3 ] || { [ "$pv_maj" -eq 3 ] && [ "$pv_min" -ge 12 ]; }; }; then
            echo ""
            echo "ERROR: Python 3.12+ is now required, but your venv uses Python $venv_py_ver." >&2
            echo "  To upgrade:" >&2
            echo "  1. Install Python 3.12+ (https://www.python.org/downloads/)" >&2
            echo "  2. Delete the venv/ directory: rm -rf venv/" >&2
            echo "  3. Re-run: ./manage.sh setup" >&2
            echo ""
            exit 1
        fi
    fi

    echo "[2/5] Updating Python dependencies..."
    "$ROOT/venv/bin/pip" install --upgrade pip --quiet
    _install_torch_if_needed
    if [ ! -f "$ROOT/backend/requirements.txt" ]; then
        echo "ERROR: requirements.txt not found at $ROOT/backend/requirements.txt" >&2
        exit 1
    fi
    if [ -t 0 ]; then
        echo "  Source: https://pypi.org/"
        echo "  Packages:"
        grep -v '^[[:space:]]*#' "$ROOT/backend/requirements.txt" | grep -v '^[[:space:]]*$' | while IFS= read -r _pkg; do echo "    $_pkg"; done || true
    fi
    printf "  Update Python backend dependencies from requirements.txt? [Y/n] "
    read -r _reply || true
    case "$_reply" in
        [Nn]*) echo "  Skipping Python dependencies." ;;
        *)
            "$ROOT/venv/bin/pip" install -r "$ROOT/backend/requirements.txt"
            echo "  Done."
            ;;
    esac

    echo "[3/6] Installing/updating SAM2..."
    printf "  Install or update SAM2 from GitHub? [Y/n] "
    read -r _reply || true
    case "$_reply" in
        [Nn]*) echo "  Skipping SAM2." ;;
        *)
            if "$ROOT/venv/bin/pip" install "git+https://github.com/facebookresearch/sam2.git" pycocotools --quiet; then
                echo "  SAM2 up to date."
            else
                echo "  WARNING: SAM2 install failed."
            fi
            ;;
    esac

    echo "[4/6] Installing/updating SAM3..."
    printf "  Install or update SAM3 from GitHub? [Y/n] "
    read -r _reply || true
    case "$_reply" in
        [Nn]*) echo "  Skipping SAM3." ;;
        *)
            # --no-deps: sam3 pins numpy<2 and ftfy==6.1.1, which conflict with
            # requirements.txt; its real runtime deps are installed explicitly.
            if "$ROOT/venv/bin/pip" install "git+https://github.com/facebookresearch/sam3.git" --no-deps --quiet \
                && "$ROOT/venv/bin/pip" install iopath ftfy pycocotools "setuptools<81" --quiet; then
                echo "  SAM3 up to date."
            else
                echo "  WARNING: SAM3 install failed."
            fi
            ;;
    esac
    echo "  NOTE: SAM3 also needs the checkpoint: download sam3.safetensors from https://huggingface.co/1038lab/sam3 into models/sam3/"

    echo "[5/6] Updating frontend dependencies..."
    cd "$ROOT/frontend"
    npm install
    cd "$ROOT"
    echo "  Done."

    echo "[6/6] Building frontend..."
    _build_frontend

    echo ""
    echo "=== Update complete! ==="
    echo "Database migrations will run automatically on next start."
    echo ""
}

cmd_dev() {
    if [ ! -f "$ROOT/venv/bin/activate" ]; then
        echo "Virtual environment not found. Run ./manage.sh setup first." >&2
        exit 1
    fi

    _activate
    _migrate

    echo "Starting backend on :8000 and frontend dev server on :5173..."
    echo "Open http://localhost:5173 in your browser."
    echo ""

    rm -f "$ROOT/.restart"

    # Run uvicorn in a background subshell with restart loop support.
    # set +e prevents a non-zero uvicorn exit from aborting the inner loop.
    # The inner trap ensures uvicorn is killed when the subshell is stopped.
    (
        set +e
        _uvicorn_pid=""
        trap 'kill "$_uvicorn_pid" 2>/dev/null' EXIT INT TERM
        while true; do
            python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir backend &
            _uvicorn_pid=$!
            wait "$_uvicorn_pid" || true
            if [ -f "$ROOT/.restart" ]; then
                rm -f "$ROOT/.restart"
                echo ""
                echo "Restarting backend..."
                echo ""
            else
                break
            fi
        done
    ) &
    BACKEND_PID=$!
    trap 'kill "$BACKEND_PID" 2>/dev/null' EXIT INT TERM

    cd "$ROOT/frontend"
    npm run dev
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_menu() {
    echo ""
    echo "=== Crucible ==="
    echo ""
    echo "  1) Setup   - First-time install (creates venv, installs deps, builds frontend)"
    echo "  2) Start   - Launch the app at http://localhost:8000"
    echo "  3) Update  - Pull latest changes and rebuild"
    echo ""
    printf "Enter choice [1-3]: "
    read -r choice
    case "$choice" in
        1) cmd_setup  ;;
        2) cmd_start  ;;
        3) cmd_update ;;
        *) echo "Invalid choice. Run: $0 <setup|start|update|dev>" >&2; exit 1 ;;
    esac
}

case "${1:-}" in
    setup)  cmd_setup  ;;
    start)  cmd_start  ;;
    update) cmd_update ;;
    dev)    cmd_dev    ;;
    "")     _menu      ;;
    *)
        echo "Usage: $0 <setup|start|update|dev>" >&2
        exit 1
        ;;
esac
