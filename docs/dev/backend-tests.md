# Backend tests

**Status: living.**

This file covers the conventions for writing or fixing a backend pytest test: which harness a test drives, what CI installs and how to add a dependency, the skip gates for optional dependencies (cv2, torch, symlinks), how `api_env` treats foreign keys, and the coverage, lint and migration-drift commands. The command line that runs the suite is in CLAUDE.md § Commands; the pre-merge gate table is in CLAUDE.md § Verification. The cv2 skip convention and the video fixtures live in `docs/dev/video-tests.md`.

## Which harness

Request-level tests drive `backend.main.app` over httpx (see
`backend/tests/conftest.py`); everything else is service-level. Anything needing a
decodable video is gated on cv2 — see `docs/dev/video-tests.md` (§ cv2 in CI, and the skip
convention) before adding a test or a CI dependency there.

## What CI installs

**CI installs
`backend/requirements-ci.txt`** — the floor-pinned, torch-free base set all three CI jobs share
(each adding only its own alembic/uvicorn/cv2 on top); a new import in the app or the
suite's collection path goes there, not into a workflow's install line.

## Gating tests on optional dependencies

Torch is the one
dependency that will never arrive, so a test importing anything under `backend/ml/` past the
pure-numpy modules carries `conftest.needs_torch` (the sibling of `needs_cv2`) — without it
the module *errors* on the runner rather than skipping; a test that creates a real symlink
likewise carries `conftest.needs_symlinks` (Windows refuses one without privilege, and
`backend-tests-windows` runs the suite there).

## Foreign keys in api_env

`api_env` enforces foreign keys by default,
as production does — pass `foreign_keys=False` only to plant a state the app cannot reach,
with a comment saying why.

## Coverage, lint and migrations

Coverage is opt-in:
add `--cov=backend` (or `--cov=backend/routers`) — there is no pytest `addopts`, so
CI runs plain. Lint the backend with `ruff check backend` (config in `ruff.toml`,
scoped to `E9`+`F`). Run `python scripts/check_migrations.py`
after a model/migration change (CI job `migration-drift`; see `docs/dev/database.md` § Migration drift check).
