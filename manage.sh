#!/usr/bin/env bash
# manage.sh - Dataset Manager launcher (Linux / macOS)
# Usage: ./manage.sh <setup|start|update|dev>

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_check_deps() {
    echo "[1/2] Checking Python..."
    if command -v python3 &>/dev/null; then
        PYTHON=python3
    elif command -v python &>/dev/null; then
        PYTHON=python
    else
        echo "ERROR: Python not found. Please install Python 3.10+ and add it to PATH." >&2
        exit 1
    fi
    echo "  Found: $($PYTHON --version)"

    echo "[2/2] Checking Node.js..."
    if ! command -v node &>/dev/null; then
        echo "ERROR: Node.js not found. Please install Node.js 18+ and add it to PATH." >&2
        exit 1
    fi
    echo "  Found: Node $(node --version)"
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

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

cmd_setup() {
    echo ""
    echo "=== Dataset Manager - First-Time Setup ==="
    echo ""

    _check_deps

    echo "[3/5] Creating Python virtual environment..."
    if [ -d "$ROOT/venv" ]; then
        echo "  venv already exists, skipping creation."
    else
        $PYTHON -m venv --system-site-packages "$ROOT/venv"
        echo "  venv created at $ROOT/venv (inherits system site-packages)"
        echo ""
        echo "  NOTE: GPU inference requires PyTorch with CUDA in your system Python."
        echo "  Install it first if needed: https://pytorch.org/get-started/locally/"
        echo ""
    fi

    echo "[4/5] Installing Python dependencies..."
    "$ROOT/venv/bin/pip" install --upgrade pip --quiet
    "$ROOT/venv/bin/pip" install -r "$ROOT/backend/requirements.txt"
    echo "  Python dependencies installed."

    echo "[5/5] Installing frontend dependencies and building..."
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
    echo "=== Dataset Manager ==="

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
    python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
}

cmd_update() {
    echo ""
    echo "=== Dataset Manager - Update ==="
    echo ""

    echo "[1/4] Pulling latest changes..."
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

    echo "[2/4] Updating Python dependencies..."
    "$ROOT/venv/bin/pip" install --upgrade pip --quiet
    "$ROOT/venv/bin/pip" install -r "$ROOT/backend/requirements.txt"
    echo "  Done."

    echo "[3/4] Updating frontend dependencies..."
    cd "$ROOT/frontend"
    npm install
    cd "$ROOT"
    echo "  Done."

    echo "[4/4] Building frontend..."
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

    python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir backend &
    BACKEND_PID=$!
    trap 'kill "$BACKEND_PID" 2>/dev/null' EXIT INT TERM

    cd "$ROOT/frontend"
    npm run dev
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

case "${1:-}" in
    setup)  cmd_setup  ;;
    start)  cmd_start  ;;
    update) cmd_update ;;
    dev)    cmd_dev    ;;
    *)
        echo "Usage: $0 <setup|start|update|dev>" >&2
        exit 1
        ;;
esac
