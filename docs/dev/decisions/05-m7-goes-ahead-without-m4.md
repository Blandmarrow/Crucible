# Decision 5: M7 goes ahead without M4

*Index: [decisions.md](../decisions.md).*

- **Date:** 2026-11-02.
- **Status:** **Decided** (2026-11-02, by the user).
- **Decision:** M7 (style embedding) no longer waits for M4 (the rating feature) to merge.
  The port order in the roadmap's § Porting the unmerged branches (now in
  [handoff-2026-11-13.md](../roadmap-archive/handoff-2026-11-13.md)) stops being
  binding for M7, and work on it starts with the DINOv3 question. Number 4 is skipped
  because decision 4 already exists on the held `feat/m4-rating-port` branch.
- **Rationale:** the user is not sure the other unmerged branches will ever be merged, so
  holding M7 behind M4 could hold it forever. The order existed only to settle the six files
  M4 and M7 both change, and that conflict can be settled by whichever branch lands second.
- **Rejected alternative:** keeping the order M4, M6, M7. It is cleaner for the conflict, but
  it ties M7 to a branch the user has chosen not to ship for now.
- **Accepted costs:** if M4 merges after M7, its port has to resolve the six-file conflict
  against M7's version of those files rather than the other way round. M8 stages that need
  both branches merged still wait for both.
