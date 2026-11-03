# Decision 9: Automated runs step forward on job completion

*Index: [decisions.md](../decisions.md).*

- **Date:** 2026-11-03.
- **Status:** **Decided** (2026-11-03, by the user).
- **Decision:** an M6 automated run is a `Run` row holding its list of stages and the current
  one. Each child job carries the run's id. When such a job reaches a terminal state, the one
  existing worker calls `advance_run()` after the job has committed. That function reads the
  result and enqueues the next stage, or marks the run done, failed or paused, and it logs
  rather than raises. Stages call service functions, not router handlers, so M8 stages 2 and
  3 land first. Plan: [comfy-automation-plan/README.md](../comfy-automation-plan/README.md) § 4.
- **Rationale:** nothing waits, so nothing can be stuck waiting. The August plan's own list
  of quiet failures came from its waiting parent: a long-lived session that never sees the
  child finish, a second queue the test harness must rebuild or the suite hangs, and a parent
  cancelled while its child still runs. A run that steps forward has none of them. Cancel
  cancels the current child and marks the run; resume calls `advance_run()` again from the
  first stage not done.
- **Rejected alternatives:**
  - **A second `JobQueue` whose parent job polls each child** (the August plan). It keeps the
    worker's lifecycle for the parent, but it needs a shared cancel set, fresh sessions per
    poll and a second loop-bound queue in tests.
  - **Calling router handlers in-process.** It reuses their checks, but no code here has ever
    done it, and a handler that gains a `Request` parameter breaks it silently.
- **How it's verified:** against the mock ComfyUI server (stage 2 of M6), a run is driven
  through every stage, cancelled mid-stage and resumed, and `advance_run()` is made to raise to
  prove the child job's outcome is unchanged.
