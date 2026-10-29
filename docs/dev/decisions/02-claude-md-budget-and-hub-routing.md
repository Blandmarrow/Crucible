# Decision 2: CLAUDE.md budget and hub routing

*Full record for entry 2 in the [decision log](../decisions.md#2-claudemd-budget-and-hub-routing).*

- **Date:** 2026-10-29
- **Status:** Decided (2026-10-29, by the user)
- **Decision:** two changes, both made in M3 of the [roadmap](../roadmap.md):
  - `MAX_CLAUDE_WORDS` in `scripts/check_docs.py` goes from 5,400 to 6,000.
  - Large subsystem families route through a **hub doc**: one Documentation Map row per
    family ("Start here for anything X", sized `~N (+ details)`), and a table in the hub
    from code path to the detail file that owns it, the routing `docs/dev/video.md` already
    does for video. The detail files live in a folder named for the family
    (`docs/dev/<family>/`), registered in `SPLIT_INDEXES` with the hub as its index, so the
    checker requires the hub to link every one of them and CLAUDE.md needs no row per file.
    Gallery and image similarity/scoring are the first two, built alongside their M3
    splits; another family gets a hub when it grows past about three files. A split inside
    a hubbed family adds a file to the folder and a row to the hub, not to CLAUDE.md.
- **Rationale:** measured 2026-10-29 at 5,393 words, CLAUDE.md is § Key invariants (2,321
  words), the Documentation Map (1,653 words, 57 rows at ~29 words each) and about 1,400
  words of commands, verification and maintenance rules. The invariants pass the test for
  being always loaded: each describes a failure that is silent or far from its cause. The
  Map grows by one row per topic file by design, and M3's ~20 new files would add ~580
  words, so under 5,400 every split would have forced an invariant out to pay for its own
  index row. A budget that does that measures the wrong thing. Hubs make the Map grow by
  family rather than by file, at the cost of one extra file read when a task lands in a
  hubbed family; the raised budget covers families without a hub.
- **Rejected alternatives:**
  - *Keep 5,400 and move the reviewer site-lists out of the invariants*: docs-architecture
    v3.1 wants an invariant's enumerated sites inside it, as the reviewer's checklist.
  - *A larger raise*: CLAUDE.md's cost is attention more than tokens (about 7,000 tokens a
    conversation is affordable); every resident paragraph makes the others a little less
    likely to be followed, so the budget should track what must be resident.
  - *Moving the Map out of CLAUDE.md*: it is the router that makes on-demand loading work,
    so it has to be resident.
- **Amends:** [decision 1](01-documentation-architecture.md), which kept Crucible's budgets.
  The topic, user and README budgets are unchanged.
- **How it's verified:** `scripts/check_docs.py` enforces `MAX_CLAUDE_WORDS`. Its coverage
  check requires every top-level `docs/dev/*.md` to be named in CLAUDE.md and every file in
  a `SPLIT_INDEXES` folder to be linked from its index, so a detail file that is in
  neither place WARNs. Moving files into a family folder changes their paths: the move
  repoints every inbound link, and the link and path checks FAIL on any it misses.
- **Every family gets its own folder, video included** (the user, 2026-10-29). Video is
  the largest family and already has its hub, so its eleven detail files move into
  `docs/dev/video/` and their eleven Map rows become one.
- **Amendments:** none yet.
