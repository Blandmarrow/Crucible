# Decision 11: Windows backend tests run after merge, not on pull requests

*Index: [decisions.md](../decisions.md).*

- **Date:** 2026-11-08.
- **Status:** **Decided** (2026-11-08, by the user).
- **Decision:** the `backend-tests-windows` job in `.github/workflows/backend-tests.yml` skips
  `pull_request` events. It runs on every push to `main` that touches the backend, and on
  demand through `workflow_dispatch`. It keeps `continue-on-error: true`, so it reports a
  Windows break without failing the workflow. Detail: `docs/dev/backend-infrastructure.md`.
- **Rationale:**
  - Measured on PR #115 (2026-11-07): the Windows job took 43 minutes, 42 of them in pytest,
    against 9.5 for the Linux suite and 6.5 for e2e. Every backend PR waited on it, though it
    could not block one.
  - Its original plan was to drop `continue-on-error` once it ran green, which it had done
    ten times in a row by 2026-11-08. Doing that would have put the 43 minutes in front of
    every merge instead.
  - Running after the merge still catches a Windows-only break within one merge of its cause,
    and a branch with Windows-sensitive filesystem changes can be checked first from the
    Actions tab.
- **Rejected alternatives:**
  - **Make it blocking on PRs** (the original plan). Correct, but 43 minutes per backend PR.
  - **Parallelize the suite with `pytest-xdist` first, then make it blocking.** Set aside by
    the user for now; it needs a pass to find tests that clash when run side by side.
  - **Nightly only.** Fewer runs, but a break could take a day to surface and to trace back.
  - **Opt-in by PR label.** Workable, but easy to forget on exactly the change that needs it.
