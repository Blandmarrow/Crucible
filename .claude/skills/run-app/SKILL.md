---
name: run-app
description: Launch, serve, or screenshot the Crucible app to confirm a change works in the real app (not just tests). Covers manage.sh start/dev, bare uvicorn, a throwaway DB instance, and the dev-container specifics (ComfyUI host, health check). Use when asked to run/start the app or verify a change end to end.
---

# Running the Crucible app

Backend is FastAPI on `:8000`; the frontend is a Vite SPA. Pick the launch mode
that matches what you need, then confirm readiness with the health check.

## The venv trap (read first)

Every backend command needs the venv **active in the same shell**:

```bash
source venv/bin/activate && python -m uvicorn backend.main:app --port 8000
```

Wrapping the `source` in a `( … )` subshell discards activation before the
command runs, and it silently falls back to system Python (no fastapi/sqlalchemy).
Never do `( source venv/bin/activate && … )`.

## Launch modes

| Command | What it does | When |
|---|---|---|
| `./manage.sh start` | `alembic upgrade head` → rebuild frontend *if stale* → uvicorn `:8000` under a **restart loop** | Production-like run; serves the built SPA |
| `./manage.sh dev` | `alembic upgrade head` → uvicorn `:8000 --reload` **and** Vite dev server `:5173` (proxies `/api` → `:8000`) | Active frontend work with hot reload |
| `source venv/bin/activate && python -m uvicorn backend.main:app --port 8000` | Bare backend: **no migrations, no restart loop**; serves the SPA only if `frontend/dist/index.html` already exists | Quick backend-only check against an already-migrated DB |

In `dev` mode open `http://localhost:5173`; otherwise `http://localhost:8000`.

## Bare-uvicorn caveats

- **Never click the in-app Restart or Shutdown buttons.** They write sentinel
  files (`.restart` / `.shutdown` at the repo root) that only `manage.sh`'s
  restart loop acts on. Under bare uvicorn the process just dies and never comes
  back.
- Bare uvicorn runs **no migrations**. If the DB schema is behind, requests 500.
  Run `cd backend && python -m alembic upgrade head` first (from the `backend/`
  dir — see the dev-container note below on why).

## Throwaway instance (never touches the live DB or data)

The live SQLite DB is `dataset_manager.db` at the repo root and real datasets
live under `data/`. To run against a scratch DB instead, export all three vars
(**`DATASETS_DIR` does not derive from `DATA_DIR` — set it explicitly**), then
migrate before serving because `init_db` creates **no tables** (they come only
from alembic):

```bash
source venv/bin/activate
export DATABASE_URL="sqlite+aiosqlite:////tmp/scratch/app.db"   # abs path ⇒ 4 slashes
export DATA_DIR="/tmp/scratch/data"
export DATASETS_DIR="/tmp/scratch/data/datasets"
( cd backend && python -m alembic upgrade head )
python -m uvicorn backend.main:app --port 8000
```

`frontend/e2e/serve.sh` is the canned, self-contained version of exactly this
(temp dir, three exports, alembic, uvicorn) — read it before hand-rolling one.

## Dev-container specifics

- ComfyUI (if used) runs on the Windows host, reachable at
  `http://host.docker.internal:8188` — not `127.0.0.1:8188`.
- Standalone `alembic <cmd>` can crash on `sys.path`; running it as
  `cd backend && python -m alembic upgrade head` works because `env.py` fixes the
  path first. Always run migrations from `backend/`.
- Readiness check (does not depend on the SPA being built):
  ```bash
  curl -fsS localhost:8000/api/v1/health   # → {"status":"ok",...}
  ```
