# PM-022: Orphaned `is_duplicate` flags survived deletion and re-scanning

### Symptom

Reported from a live Windows install. The Stats page showed a *Duplicate* count that no
longer matched anything on screen: the Quality page's duplicates panel rendered lone
images as "groups of one", the gallery's *Flagged: duplicate* filter returned images with
no duplicate anywhere in the dataset, and their cards and detail pages carried the
duplicate badge. Re-running Quality scoring with **Technical** enabled — the obvious
recovery, and the one a maintainer would suggest — changed nothing at all.

The reporter had done nothing unusual: run a duplicate scan, then prune each group by hand
from the gallery and the lightbox down to the copy they wanted to keep.

The same stale flag also reached `exclude_flags` at export and the captioning, detection
and LUT exclusion filters, so images were being silently skipped by jobs the user had
scoped to "everything except duplicates".

### Root cause

`is_duplicate` is not a property of one image. It is a **relationship**: it is meaningful
only alongside `duplicate_of`, which names *another row*. Nothing in the codebase treated
it that way, and derived relational state maintained additively fails in both directions
at once.

- **The flag could be falsified by a delete that never touched the flagged row.**
  `_flag_duplicates` flags `group[1:]` only, so the root carries a clean flag and every
  member carries `is_duplicate: true` + `duplicate_of: <root id>`. Every delete path
  removed rows and touched nothing else. Only `resolve_duplicates` cleared flags, and only
  on the ids the caller explicitly listed in `keep_ids`. Prune a group by hand to one
  survivor and that survivor stayed flagged as a duplicate of an image that no longer
  existed.
- **The scan was purely additive, so re-running it repaired nothing.** `_flag_duplicates`
  wrote flags onto `dup_of.keys()` and returned early when `dup_of` was empty. It never
  cleared the flag from a row that was no longer in any group — so a re-scan was a no-op
  on exactly the rows that needed it, and flags written at a looser `duplicate_threshold`
  survived a stricter re-scan.

Neither half is a bug in isolation; together they mean nothing in the system could ever
remove a flag the user had not personally clicked *Keep* on.

### Generalizable rule

**Flag any column whose value refers to another row.** If a column names an id, a stem, a
path or a group key belonging to some *other* record, then deleting that other record
falsifies it, and the writing path is not where the invariant lives. Two questions decide
whether it is maintained:

1. *Which paths can falsify it without touching this row?* Each of them owes it a prune.
   Enumerate them and state the exemptions in a comment, because the next reviewer's
   instinct will be to "finish the conversion".
2. *What recomputes it, and does that recomputation clear as well as set?* A recomputation
   that only ever adds is not a repair path, however often it runs. An empty result is the
   most important case to handle, not the one to early-return on — that early return is
   usually the whole bug.

The corollary for the prune itself: **do not derive the affected keys from the ids in the
current request.** That only works when the referenced row goes in the same call, and the
common user behaviour — deleting one image at a time — is precisely the case where it does
not: the delete that strands the last survivor names a *member*, whose own pointer is
unreadable by then because its row is already gone. Re-checking the invariant across the
touched scope is idempotent, placement-insensitive, and repairs pre-existing drift for
free. That version of the prune was written first, passed four of its five tests, and the
one it failed was the sequential delete — the realistic one.

### Why it wasn't caught the first time

Nothing owned the invariant, so no test could be written against it: the flag was set in
`_flag_duplicates` and cleared in `resolve_duplicates`, and neither file is where a
reviewer of a *delete* endpoint looks. `test_duplicate_groups_http.py` even pinned the
adjacent behaviour — `test_a_root_deleted_since_the_scan_leaves_its_copies_grouped`
asserts that deleting a root leaves its copies grouped, which is correct — without anyone
asking what happens on the *next* delete.

The path-containment and versioning-hook sweeps (PM-014, V-83) had already enumerated
every delete site in the codebase, twice. Both enumerations were about what a delete must
do to *files*; neither asked what a delete owes the rows that survive it.

The symptom was also mistaken for a display bug first: the Stats "N flagged" badge sums
seven overlapping flag counts and legitimately exceeds the image count, which made
`688 vs 586` look like the badge's arithmetic rather than the data underneath it.

### Fix

New `backend/services/duplicate_service.py` owning both directions:
`apply_duplicate_groups` (the scan's authoritative reconciliation — flags what it grouped,
clears everything else in the dataset, with the `if not dup_of: return` early return
deleted) and `prune_orphaned_duplicate_flags` (the delete-side repair, scoped by dataset).
Six delete sites call the prune between their row DELETEs and their commit; three are
deliberately exempt and say so in a comment. `_flag_duplicates` moved to module level and
now delegates. Tests: `backend/tests/test_duplicate_flags_authoritative.py` (service
level, no cv2/torch/job queue) and new cases in `test_duplicate_groups_http.py` covering
each delete path — including the case the prune must **not** touch.

Out of scope, asked and declined: the Stats "N flagged" badge, which still sums
overlapping counts and will still exceed the image count. This fix moves the *Duplicate*
card, not the badge total.

### Status & date

MITIGATED — the invariant has one owner and the scan is the repair path, but nothing
structurally prevents a *new* delete endpoint from skipping the prune; the enumerated list
in CLAUDE.md and in the service docstring is a reviewer's checklist, not a guard.
Last reviewed for staleness: 2026-08-05.
