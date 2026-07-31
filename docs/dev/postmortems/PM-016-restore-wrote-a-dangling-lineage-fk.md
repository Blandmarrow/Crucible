# PM-016: restore wrote back a `source_video_id` whose video was gone

### Symptom

Restoring a snapshot taken while a video still existed failed with
`sqlalchemy.exc.IntegrityError: (sqlite3.IntegrityError) FOREIGN KEY constraint failed` on
`UPDATE images SET ... source_video_id=?`. The `restore_snapshot` job ended `failed`,
nothing was restored, and **every retry failed identically** — that snapshot, and any
branch whose head it was, could never be checked out again. Each attempt still committed
its own pre-restore auto-snapshot and advanced the branch head, so the versions list filled
with junk entries the user could not use either.

The sequence is ordinary: extract frames from a clip, snapshot, delete the video, later
restore. Deleting a video is a *supported* action that deliberately NULLs the live frames'
lineage rather than destroying curated data, so nothing in it warns the user they have just
made a snapshot un-restorable. Found in the second code review of the video-support branch,
not in production.

### Root cause

`Image.source_video_id` is a real `ForeignKey("videos.id", ondelete="SET NULL")`.
`VersionImageState.source_video_id` deliberately carries **no** FK — that is what lets a
snapshot outlive the video it names, which is the whole point of the mirror. Restore's Pass
2c then copied the stored value straight back onto the live row.

`ondelete="SET NULL"` covers exactly one direction: deleting the *parent* row. It says
nothing about updating a *child* to a parent key that no longer exists, which is a plain
constraint violation. So the mirror's design (no FK, survives the delete) and the live
column's design (FK, enforced) are individually right and jointly a trap, and the write-back
was the one place they met.

The two sibling rebuild paths had already met it and handled it: `duplicate_dataset`
**remaps** `source_video_id` through an old→new id map and falls back to NULL on a miss, and
`batch_copy_dataset` writes NULL outright. Restore was the only path that wrote a live id
without asking whether it still resolved.

### Why no test caught it

`backend/tests/conftest.py` builds the harness engine with SQLite's `foreign_keys` pragma
**off** — the per-connection default. `backend/database.py` installs `PRAGMA
foreign_keys=ON` on the app engine, so every FK in the schema is enforced in production and
unenforced in the suite. The existing lineage test restored while the video still existed,
so even an enforcing engine would have passed it.

This is the second bug hidden by that gap; `test_duplicate_video_fk_enforced.py` documents
the first and opts the pragma back on for its own path. A whole failure class is invisible
by default here, and turning the pragma on suite-wide is a behaviour change to every
existing scenario rather than a fix to this one.

### Generalizable rule

**A column mirrored onto `VersionImageState` without its constraints is not the same column
any more.** When a snapshot column points at another table, the restore write-back must
re-resolve it against the live table rather than trusting it — the mirror is a record of
what *was* true, and a foreign key is a claim about what *is*. Ask of every such column:
what deletes the thing it points at, and is that deletion supported? If it is, the write-back
needs the same NULL fallback the copy paths already use.

More generally, for any snapshot/restore column: the snapshot is allowed to remember rows
the database has forgotten. Restoring is the moment that memory has to be reconciled with
reality, and it is the only pass where "write it back verbatim" can be actively wrong.

**And a constraint the test harness does not enforce is a constraint the suite cannot see.**
When adding a test for anything that writes an FK column, opt into `foreign_keys=True`
explicitly (`api_env(..., foreign_keys=True)`, or `make_env(..., foreign_keys=True)` in
`test_versioning_restore.py`). A green suite is not evidence about foreign keys here.

### Fix

`backend/services/version_service.py` Pass 2c: collect the snapshot's non-NULL
`source_video_id` values, resolve them against `videos` in one `chunked` `IN (...)` query,
and write NULL for the ids that no longer exist, logging how many frames were affected. The
lookup is per restore, not per row.

Guarded by `backend/tests/test_versioning_restore.py`:
`test_restore_after_the_source_video_was_deleted` (the dangling case, on an engine with the
pragma on) and `test_restore_keeps_lineage_when_the_video_still_exists` (so the guard cannot
degenerate into a blanket NULL).

### Status

MITIGATED — the write-back is guarded and both directions are pinned. The underlying
asymmetry stands by design: `VersionImageState` still carries no FKs, so any *future*
snapshot column that references another table inherits the same obligation.
