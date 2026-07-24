#!/usr/bin/env bash
# Playwright webServer command: serve the built SPA + API against a throwaway
# SQLite DB and data dir, so E2E never touches the live repo-root DB or ./data.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
export DATABASE_URL="sqlite+aiosqlite:///${TMP}/e2e.db"   # abs path ⇒ 4 slashes total
export DATA_DIR="${TMP}/data"
export DATASETS_DIR="${TMP}/data/datasets"                # does NOT derive from DATA_DIR
# Works both locally (venv) and in CI (system pip); the guard makes the source optional.
[ -f "${ROOT}/venv/bin/activate" ] && source "${ROOT}/venv/bin/activate"
[ -f "${ROOT}/frontend/dist/index.html" ] || { echo "frontend/dist missing — run npm run build" >&2; exit 1; }
# init_db creates NO tables — the schema comes only from alembic.
( cd "${ROOT}/backend" && python -m alembic upgrade head )
# exec so Playwright's process kill reaches uvicorn directly.
cd "${ROOT}" && exec python -m uvicorn backend.main:app --port "${E2E_PORT:-8199}"
