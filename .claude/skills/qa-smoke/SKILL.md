---
name: qa-smoke
description: Run Crucible's full QA smoke suite before merging — backend pytest, frontend typecheck+lint, and the Playwright E2E journeys — and report pass/fail per stage. Use when asked to smoke-test, QA, or verify the app end to end before a merge/release.
---

# QA smoke run

Run these three stages in order, from the repo root. Report pass/fail per stage;
do not stop at the first failure unless a stage is a hard prerequisite for the
next (noted below).

## 1. Backend tests

```bash
source venv/bin/activate && python -m pytest backend/tests/ -q
```

Same-shell venv rule: never wrap the `source` in `( … )` — activation is
discarded and it runs on system Python (no fastapi/sqlalchemy).

## 2. Frontend typecheck + lint (hard prerequisite for stage 3)

```bash
cd frontend && npm run build && npm run lint
```

`npm run build` (`tsc -b && vite build`) is the only real typecheck AND it
refreshes `frontend/dist`, which the E2E server serves. **Stale dist tests stale
code**, so a green build here is required before stage 3. Lint currently carries
pre-existing react-hooks debt (non-blocking in CI) — report new lint errors your
change introduced, but pre-existing hits are not a stage failure.

## 3. Playwright E2E

```bash
cd frontend
npx playwright install chromium   # first run only
npx playwright test
```

The config starts its own backend on **:8199** against a throwaway DB
(`e2e/serve.sh`) — it never touches the live repo-root DB. On failure, the HTML
report is written to `frontend/playwright-report/`; open it to triage.

## Report

Summarise each stage: `backend pytest: N passed`, `frontend build+lint: ok`,
`e2e: N passed / M failed`. On any failure, quote the failing test name and the
assertion, and point at `playwright-report/` for E2E.

## Pitfalls

- **Port 8199 already bound** (a previous run's server, or a dev session on it) →
  the webServer step fails to start. Kill the stray process first.
- **Never point the suite at a real dev server.** `reuseExistingServer` is false
  precisely so it always spins up its own throwaway backend.
- **Don't rebuild the frontend mid-run.** Rebuild only between full runs, in
  stage 2 — never while `playwright test` is executing against the served dist.
- E2E deliberately excludes Restart/Shutdown, ComfyUI, booru and all GPU jobs
  (no ML server during e2e); do not add journeys that need them.
