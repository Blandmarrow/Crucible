# Decision 13: Scale and data-safety work comes first

*Index: [decisions.md](../decisions.md).*

- **Date:** 2026-11-08.
- **Status:** **Decided** (2026-11-08, by the user) that M9 goes ahead of every other open
  milestone (M7, M8, M4 and M5's cleanup), which keep their own order behind it. **Leaning**
  on the rejected alternatives below. Each is safe to build around, and the user can still
  overrule any one of them.
- **Decision:** the recommendations from the 2026-11-08 outside review, as checked against
  the code, become milestone M9 with its own plan,
  [scale-plan/README.md](../scale-plan/README.md). Every large operation is to be
  incremental, bounded in memory, and recoverable. The order puts the cheapest data-safety
  fixes first (the gallery tie-breaker, then the orphan sweep), then chunked scoring, the
  largest risk of lost work, then the memory fixes, and then measurement before any
  structural change.
- **Rationale:**
  - Two of the findings lose user data or user hours outright. The startup orphan sweep
    deletes a dataset folder permanently when its row is missing (after an older DB backup is
    restored, for one). A scoring job writes nothing until every check has finished, so a
    crash or Restart discards all of it.
  - Crucible has other users, whose setups differ, so a silent loss on their machines is the
    worst failure available.
  - The rest bound memory on paths that grow with the dataset (Stats payloads, the single-layer
    style branch). The structural question, moving the embeddings to their own table, waits
    for a measurement that reproduces a migrated DB's column order.
- **Rejected alternatives:**
  - **Cursor (keyset) pagination for the gallery now.** The July scaling report recommended
    it together with a covering index. The gallery has numbered pages and persists the page
    number, which a cursor cannot jump to, and on a score sort the main cost is sorting the
    filtered set, not the offset. Revisit after M9 stage 6's numbers.
  - **A general job checkpoint system.** Once scoring commits per chunk and can skip rows it
    already scored, the rows themselves are the checkpoint, and a Retry that resubmits the
    stored config covers most of the rest (M9 stage 8).
  - **Provider keys in the OS keychain, or encrypted with a local key.** A keychain fails on
    headless Linux, systemd and container installs, which the docs support. A key stored
    beside the database protects little. The real leak paths are the automatic backups and a
    database shared for a bug report; a "copy the database without secrets" action would
    answer those if it is ever needed.
  - **Splitting the large routers and services now.** The topic docs already route by
    subsystem, and a broad split costs churn and `git blame` history. M8 stage 8 already
    splits `images.py`. Others split when someone is working in them.
  - **A memory-aware Stats cache.** M9 stage 4 replaces the raw value lists with
    fixed-size histograms, so each cache entry is a few KB and a count limit is enough.
  - **A quarantine UI and retention policy for orphan folders up front.** Moving the folder
    aside and logging it removes the loss. The UI can follow if someone needs it.
  - **Postgres, a vector database, or a distributed queue.** The July report and this
    review agree: none of the findings is a database-engine limit, and each adds a service
    that a single-user local app should not need.
- **Amendments:**
  - **2026-11-09.** A review timed the hot queries on a scratch database with the migrated
    column order (the last addendum of `backend/scripts/scaling_bottlenecks_report.md`).
    At the user's request its findings went onto the roadmap, and M9 gained three stages:
    stage 9 (stage 1's tie-breaker cost the score sorts their index; PM-036), stage 10 (an
    automated run's paid-call cap counts no caption calls) and stage 11 (hot-path quick
    wins). Stage 6 was split into a harness half before stage 4 and a numbers half after
    it, stage 4's design was amended, and stage 7 now covers `generation_metadata` as well
    as the embeddings. The order of what is left is 9, 10, 6a, 4, 11, 6b, 7. These are the
    review's recommendations and carry the same **Leaning** status as the list above.
  - **2026-11-09, cursor paging.** The first rejected alternative said to revisit it after
    stage 6's numbers. The partial run gives one: with the sort following its index, a
    page 60,000 rows deep costs about 5 ms at 100k rows, because skipping rows only steps
    index entries. Numbered pages stay, and cursor paging stays rejected. The 1M figure is
    still owed by stage 6b.
  - **2026-11-09, order.** Job throughput became M10, directly after M9
    ([decision 14](14-job-throughput-is-its-own-milestone.md)). It sits between M9 and M7;
    the other open milestones keep their own order.
  - **2026-11-10, stage 4 shrinks and stage 11 goes first.** **Decided** (2026-11-10, by
    the user). Stage 6a's baseline shows the Stats cost only at sizes nobody has yet: the
    user's datasets run from hundreds to just under 2k images, and a 20k dataset loads Stats
    in about 2 s cold. The real cost is a Stats tab left open during a job on a 100k+
    dataset, which redoes about 6 s of work per 5 s poll at 100k. The full design (bins built
    in SQL, the server owning the edges) would also make the custom-edge editor a server
    round trip of seconds where it is instant today. So stage 4 becomes the small fix: fetch
    the raw value lists only when something needs them, and return the means from `/stats`.
    The full design is parked in the plan, to revisit if a dataset nears 50k to 100k images
    or stage 6b's numbers call for it. Stage 11 goes next, since its gallery items help at
    any dataset size. The order of what is left is 11, 4, 6b, 7. The rejected memory-aware
    Stats cache stays rejected: the raw lists now reach the cache only when a user asks for
    them. The score sort inside a small subfolder, which 6a's baseline found, stays out of
    stage 11 and is parked until a dataset nears 20k images with small subfolders.
