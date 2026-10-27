# PM-028: the PM-015 regression tests never ran in CI

### Symptom

`backend/tests/test_spa_fallback_containment.py`, the four tests guarding PM-015's
arbitrary-file read through the SPA catch-all, were skipped on every CI run since they
were written. CI stayed green, and the suite reported them only as `skipped`. An external
review found it (GPT-003). It also found the trap waiting behind the obvious fix (GPT-005):
enabling the module on a fresh CI install **hung** the job instead of running it. The hang
was `test_an_absolute_path_does_not_replace_the_dist_root`, with no output and no timeout,
on Starlette ≥1.8. Reproduced on 1.10.0 in the dev venv: `pytest` never returned, while
the other three tests passed in under a second.

### Root cause

Two independent defects, each hiding the other.

1. **A skip keyed on a build artifact CI never builds.** `main.py` registered the
   catch-all only when `frontend/dist` existed, so the module carried
   `skipif(not (FRONTEND_DIST / "index.html").is_file())`. `frontend/dist` is gitignored.
   `backend-tests.yml`, the only workflow that runs pytest, never runs `npm run build`.
   The two workflows that build it (`frontend-build.yml`, `e2e-smoke.yml`) never run
   pytest. The docstring's reassurance, "`qa-smoke` builds the frontend before pytest",
   was true and beside the point: a skill someone may run is not enforcement.
2. **A hand-built ASGI peer that no real server resembles.** The raw-scope helper,
   needed because httpx cannot send `//foo` as a path (PM-015), had a `receive()` that
   returned `http.request` on *every* call, and a scope without `spec_version`.
   Starlette 1.8 added a disconnect listener to `FileResponse`, a `while True: await
   receive()` loop beside the body send. Against that `receive()` it never yields, so
   the response starves forever. uvicorn's `receive()` blocks after the request and
   eventually reports `http.disconnect`. The helper did not, and nothing bounded the wait.
   The repo pins `starlette>=1.0.1` with no cap, so a fresh install picked up 1.8 the day
   it shipped.

The skip in (1) is what kept (2) invisible: a test that never runs cannot hang.

### Generalizable rule

- **A security regression test must not depend on a build step, a fixture download or
  any other artifact the test job doesn't produce.** Flag any `skipif` whose condition is
  "file X exists" when X is gitignored. Restructure the code under test so the test can
  supply its own X: here, `main.mount_spa(app, dist)` against a `tmp_path` tree. Do not add
  a CI build step and hope every future job keeps it.
- **A hand-rolled protocol peer in a test (ASGI `receive`/`send`, a fake socket, a mock
  transport) must follow the protocol's state machine, not just produce one message.**
  Deliver the request once, then block until the response completes and report the
  disconnect. Wrap the call in a timeout so the next mismatch fails instead of hanging.

### Why it wasn't caught the first time

PM-015's fix proved the tests bite by reverting the guard locally, where `frontend/dist`
was built. Nobody checked where the module actually ran in CI. A skipped test reads as
"not applicable here", not "unenforced", and no check asserted that a security test
executed on any runner. The hang needed a Starlette release that came after the test.

### Fix

- `backend/main.py`: the SPA block is now `mount_spa(target, dist)`, called by the
  module for the real `frontend/dist`. Behaviour is unchanged.
- `backend/tests/test_spa_fallback_containment.py`: the module-level skip is gone. Each
  test mounts the SPA on a fresh `FastAPI()` over a `tmp_path` `dist`, with a `secret.md`
  beside it as the escape target.
- The raw-scope helper now has `spec_version: "2.4"`, a `receive()` that blocks until the
  final body chunk and then reports `http.disconnect`, and a 10 s `wait_for`.
- Before the fix, the module hung on Starlette 1.10.0. After it, all four tests pass.
  Removing the `is_relative_to` guard still fails both vector tests.

### Status & date

MITIGATED — 2026-10-27.
Last reviewed for staleness: 2026-10-27.
