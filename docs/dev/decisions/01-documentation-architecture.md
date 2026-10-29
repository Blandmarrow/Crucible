# Decision 1: Documentation architecture

*Full record for entry 1 in the [decision log](../decisions.md#1-documentation-architecture).*

- **Date:** 2026-10-29
- **Status:** Decided (2026-10-29, by the user)
- **Version:** docs-architecture v3.1, upgraded from a pre-v3 setup that had grown in this
  repo: word budgets, the split queue, map word counts and the paragraph cap, but a
  checker that never scanned `docs/dev/` subfolders, no decision log and no sequencing
  roadmap. A later upgrade reads this line to know where the repo starts from, and adds a
  dated amendment below when it moves to a newer version.
- **Decision:** CLAUDE.md is the always-loaded entry point and table of contents. Subsystem
  depth lives in on-demand topic files under `docs/dev/`, with the
  [postmortems](../postmortems.md), this decision log and a [roadmap](../roadmap.md).
  `scripts/check_docs.py` (stdlib only, the reference checker with Crucible's CONFIG)
  enforces links, paths, link labels, word budgets and coverage, locally and in CI, where
  it self-tests first and runs on every PR. The maintenance rules live in the
  `doc-maintenance` skill (`.claude/skills/doc-maintenance/SKILL.md`).
- **Optional modules applied:** B, the split queue ([pending-splits.md](../pending-splits.md),
  already in use); C, transient handoff folders (`docs/dev/gpt-review/`,
  [rating-plan/](../rating-plan/README.md)).
- **Rationale:** an agent pays for every always-loaded word in every conversation, so depth
  loads on demand. Budgets count words because a line budget can be met by writing longer
  lines. The checker was swapped rather than patched because the reference already covered
  what Crucible's lacked (recursive scanning, transient folders, link labels, a
  self-test), and only its CONFIG needs to differ.
- **Rejected alternatives:**
  - *The reference budgets* (CLAUDE.md 2,500, topic files 3,000, README 3,000): with 17
    splits already queued, lower budgets would only lengthen the queue. Revisit once it is
    cleared.
  - *A `**Status:**` line on every topic file*: nothing reads them, so adding 56 is churn.
    New records (roadmap, decisions, archive) carry one.
  - *Moving the maintenance rules from the skill into a docs/dev/doc-maintenance.md*: the
    skill is committed and auto-triggers; the cost, that the drift check does not cover
    it, was accepted.
- **Amendments:** none yet.
