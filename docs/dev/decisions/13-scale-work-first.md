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
