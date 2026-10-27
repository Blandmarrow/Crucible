# Setup, CI, test-harness and deployment findings

These are the verified findings in launchers, CI and the test harnesses, plus the network-exposure
decision. Read `docs/dev/environment-setup.md` and `docs/dev/backend-infrastructure.md` as relevant.
Conventions are in `docs/dev/gpt-review/README.md`.

## GPT-001 — [P2] Setup accepts Node versions the locked toolchain does not support

**Verdict:** CONFIRMED (static). NEW.

**Where:**
- `manage.ps1` (~109-116) and `manage.sh` `_node_version_ok` (~26-30) accept any major version
  ≥ 18.
- `README.md` says Node 18+, in both the badge and the prerequisites table.
- The locked Vite 8.1.4 (`frontend/package-lock.json` ~4048-4065) and the React plugin require
  `^20.19.0 || >=22.12.0`.
- `frontend/package.json` has no `engines` field.

**Fix notes.**
- Check major.minor in both launchers. Keep `manage.ps1` ASCII-only and parse-check it with a
  portable pwsh, per project memory.
- Update the README badge and the prerequisites table.
- Add `engines.node` to `frontend/package.json`.
- If the launchers install Node themselves, check which version they install.

## GPT-002 — [P3, GPT said P2] `api_env` defaults to foreign keys OFF, unlike production

**Verdict:** CONFIRMED (static). This is a known, deliberate choice: the comment in
`backend/tests/conftest.py` explains it and defers the global flip.

**Facts:**
- Production enables `PRAGMA foreign_keys=ON` (`backend/database.py:23`).
- `api_env(..., foreign_keys=False)` is the default (`conftest.py:97`).
- Of the 57 modules that use `api_env`, only 4 opt in.
- GPT flipped the default and ran all 57 modules: 645 cases with 0 new failures. That was on
  Windows, and the only failures were the known path asserts.

**Fix notes.** Flip the default to True, run the suite, and give an explicit `foreign_keys=False`
to any scenario that genuinely needs it.

## GPT-004 — [P3, GPT said P2] Test harnesses assume POSIX, and there is no Windows CI

**Verdict:** CONFIRMED (static only; it cannot be reproduced on Linux). NEW.

**Where:**
- **Playwright launcher:** `frontend/playwright.config.ts:22` runs `bash e2e/serve.sh`.
  `serve.sh` puts a raw `mktemp -d` POSIX path into the SQLite URL and only sources
  `venv/bin/activate`.
- **Hard-coded `/tmp`:**
  - `frontend/e2e/export-plain.spec.ts:12,37` hard-code `/tmp/...` export dirs.
  - `frontend/e2e/video-extract.spec.ts:611` does the same; GPT missed this one.
- **POSIX-slash `endswith` asserts:**
  - `test_video_import_rescan_http.py:404`
  - `test_video_ingest_http.py:51`
  - `test_video_rename_http.py:68,284`
  - `test_video_serving_http.py:168`

  Each compares `str(path)` values, which carry backslashes on Windows.
- **Symlink fixture:** `test_versioning_maintenance.py::test_backup_refuses_symlink_escaping_the_dataset`
  needs symlink privilege, failing with WinError 1314.
- **CI:** every workflow uses `runs-on: ubuntu-latest`.

**Fix notes.**
- Compare `Path(...).parts` or `.as_posix()`.
- Derive temp dirs from `os.tmpdir()` or `tempfile`.
- Write a Python launcher for e2e that picks `venv/Scripts` or `venv/bin`.
- Skip the symlink test when symlinks are unavailable.
- Then optionally add a `windows-latest` backend job. The app's Windows-only failure classes
  (PM-021 locks, SQLite variable limits) are the real motivation, so a Windows job would earn its
  keep.

## GPT-007 — [decision, GPT said P1] An unauthenticated API bound to all interfaces

**Verdict:** CONFIRMED (static), and KNOWN and deliberate. Project memory records that loopback
binding was raised before and declined, to keep LAN access.

**Facts:**
- `manage.ps1:680` and `:890`, and `manage.sh:652` and `:831`, bind `--host 0.0.0.0`.
- There is no authentication dependency anywhere.
- `POST /api/v1/shutdown` and `/restart` (`main.py`) are unauthenticated.
- The filesystem router accepts arbitrary absolute paths.
- CORS (`main.py`) is not authorization: GPT confirmed that a non-browser client is unaffected by
  it.
- PR #95 changed only frontend secure-context behaviour.

**For the decision:** this is what makes every path-traversal finding moot, including GPT-011.
Options are:
- a loopback default with an explicit opt-in LAN flag; or
- LAN binding plus a token, for example a shared secret set at setup that the SPA sends as a
  header.

The first option is cheap and fully reversible. The user's call.
