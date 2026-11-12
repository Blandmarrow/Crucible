# Backend tests

**Status: living.**

This file covers the conventions for writing or fixing a backend pytest test: which harness a test drives, what CI installs and how to add a dependency, the skip gates for optional dependencies (cv2, torch, symlinks), how `api_env` treats foreign keys, and the coverage, lint and migration-drift commands. The command line that runs the suite is in CLAUDE.md § Commands; the pre-merge gate table is in CLAUDE.md § Verification. The cv2 skip convention and the video fixtures live in `docs/dev/video/tests.md`.

## Which harness

Request-level tests drive `backend.main.app` over httpx (see
`backend/tests/conftest.py`); everything else is service-level. A fake shared across test
files lives in a non-`test_` module beside them, so pytest does not collect it, and imports no
`conftest` if anything outside pytest runs it: `fake_comfy.py` is the example
(`docs/dev/comfyui/run.md` § Testing the run). Anything needing a
decodable video is gated on cv2 — see `docs/dev/video/tests.md` (§ cv2 in CI, and the skip
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
the module *errors* on the runner rather than skipping. A test of the job *machinery* that
merely passes through such an import takes the `conftest.stub_aesthetic_scorer` fixture
instead of skipping: a `quality_score` job imports `backend.ml.aesthetic_scorer` even for a
technical-only check, so an automated run's Score stage failed nine run tests on CI, while a
developer machine with torch passed. The stub's functions fail if called. A test that
creates a real symlink
likewise carries `conftest.needs_symlinks` (Windows refuses one without privilege, and
`backend-tests-windows` runs the suite there).

## No real LLM server

The autouse fixture `_no_real_llm_server` in `backend/tests/conftest.py` points
`run_service._LLM_TRANSPORT` at a transport that refuses every connection, for every test.
Test providers use `127.0.0.1:1234`, which is LM Studio's real port on a developer's
machine, so a run test could otherwise list or unload real models, and pass or fail by
what is running. A test that wants a server sets its own transport with `monkeypatch`.

The same fixture covers the two remote captioners (M10 T2). `ollama_captioner._HTTP_TRANSPORT`
gets the refusing transport. The OpenAI SDK is built on `httpx2`, so no `httpx` transport
reaches it: `openai_compat_captioner.make_client` is replaced by `RefusingOpenAIClient`
(in `conftest.py`), whose every request fails as a refused connection would. A test that
wants a server patches `make_client`, or `caption_image` itself.

## Foreign keys in api_env

`api_env` enforces foreign keys by default,
as production does — pass `foreign_keys=False` only to plant a state the app cannot reach,
with a comment saying why.

## Coverage, lint and migrations

Coverage is opt-in:
add `--cov=backend` (or `--cov=backend/routers`) — there is no pytest `addopts`, so
CI runs plain. Its config is `.coveragerc` at the repo root, which pytest-cov finds only when
pytest runs from there. It sets `concurrency = greenlet,thread` and omits `backend/tests/` and
`backend/alembic/`. Do not drop the concurrency line: SQLAlchemy's async layer runs each query
in a greenlet, and model and file work goes through `run_in_executor`, so the default tracer
marks every line after an `await db.execute(...)` and every line in a worker thread as missed.
On 2026-11-08 that read `routers/images.py` at 55% against a real 89%, and `comfy.py` at 51%
against 83%. Lint the backend with `ruff check backend` (config in `ruff.toml`,
scoped to `E9`+`F`). Run `python scripts/check_migrations.py`
after a model/migration change (CI job `migration-drift`; see `docs/dev/database.md` § Migration drift check).
