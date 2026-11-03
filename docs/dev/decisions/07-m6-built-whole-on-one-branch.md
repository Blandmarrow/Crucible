# Decision 7: M6 is built whole on one branch

*Index: [decisions.md](../decisions.md).*

- **Date:** 2026-11-03.
- **Status:** **Decided** (2026-11-03, by the user).
- **Decision:** every M6 stage is built on `feat/m6-comfy-automation`, from the per-row folder
  port to the dry run and recipes, and nothing merges to `main` until the whole build is done.
  The old `comfy-per-row-subfolders` branch is deleted once its commit is verified on the new
  one. Numbers 7 to 9 are held on this branch, as 4 is on M4's.
- **Rationale:** the user wants to judge the feature as a whole before any of it ships. The
  per-row folders alone change how a ComfyUI run files its images, and structured generation
  is what makes that worth having.
- **Rejected alternative:** merging each stage as it lands, the way M5 and M7 were. It gets
  each piece in front of users sooner, but it ships parts of a design that may still change.
- **Accepted costs:** the branch lives a long time, so it rebases onto `main` at stage
  boundaries, and its migration chain grows. Whichever of M4 and M6 merges second re-parents
  its first migration onto the other's head. `main`'s roadmap and decision log do not show
  this work until it merges.
