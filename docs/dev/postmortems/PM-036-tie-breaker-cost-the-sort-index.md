# PM-036: A tie-breaker added for stable paging cost the gallery's score sorts their index

### Symptom

Sorting the gallery by a score (aesthetic, blur, style similarity) became slower with the
size of the dataset, on every page, after PR #124 (M9 stage 1, merged 2026-11-08). Nothing
errors and every page is correct, so no test and no small dataset shows it. Found on
2026-11-09 by timing the query on a scratch database of 100,000 rows built with the
migrated column order (the 2026-11-09 addendum of
`backend/scripts/scaling_bottlenecks_report.md`):

| Query | Before PR #124 | After |
|---|---|---|
| Aesthetic, best first, page 1 | 1.4 ms | 323 ms |
| The same, `OFFSET 60000` | not run | 4,509 ms |
| The same, `OFFSET 10000`, 20,000 ComfyUI images | not run | 6,902 ms |

`EXPLAIN QUERY PLAN` for the slow statement reads
`SEARCH images USING INDEX ix_images_dataset_similarity (dataset_id=?)` followed by
`USE TEMP B-TREE FOR ORDER BY`. A copy of the real dev database gives the same plan.
`GET /images/ids` and the detail view's arrows build their order with the same function,
so they pay it too.

### Root cause

`_apply_image_ordering` (`backend/routers/images.py`) used to end a score sort at the sort
column, which `ix_images_dataset_aesthetic` and its siblings satisfy by themselves. Ties
were then in no fixed order, so `OFFSET` paging could repeat or skip an image. PR #124
fixed that by appending `created_at ASC, id ASC`.

No index carries those two keys after a score. SQLite could still have walked the score's
index and sorted only each group of ties, and forcing that index proves it is fast (1.5 ms).
The planner did not choose it. With three `ORDER BY` terms it reaches the dataset's rows
through whichever `dataset_id` index it picks, puts **every row of the dataset, all selected
columns included,** into a sorter, and returns one page. The cost grows with the dataset
and with the row width, which is why a dataset whose rows carry 55 KB of
`generation_metadata` is the worst case.

Two obvious repairs do not work. `ANALYZE` changes which index the scan uses but not the
full sort. A wider index `(dataset_id, aesthetic_score, created_at, id)` fixes the ascending
sort only: the descending one is `DESC, ASC, ASC`, which matches that index in neither
direction.

### Generalizable rule

**An `ORDER BY` key added for uniqueness can take the whole sort off its index.** When a
sort gains a key, or changes a key's direction:

- Read `EXPLAIN QUERY PLAN` for the statement the code really builds, every selected column
  included. `USE TEMP B-TREE FOR ORDER BY` with no "RIGHT PART" or "LAST TERM" qualifier
  means every matching row is sorted for each page.
- Check that the added keys run in the **same direction** as the leading key, or no index
  can serve them in a descending sort.
- Prefer a tie-breaker the index already carries. In SQLite every index entry ends with the
  table's `rowid`.
- Time it at a row count where a full sort hurts. A plan can be read on an empty table; its
  cost cannot.

### Why it wasn't caught the first time

- **The acceptance test checked the result, not the plan.** It proves pages are disjoint
  and cover every row, which they are.
- **The plan called for the check, and it was read too kindly.** Stage 1's text says to
  check `EXPLAIN QUERY PLAN` afterwards and that a "USE TEMP B-TREE FOR RIGHT PART OF ORDER
  BY" line is "expected and cheap under a `LIMIT`". That is what the `created_at` sort
  gets. The score sorts get the unqualified line, which is not cheap, and nothing compared
  the two.
- **The dev database has 1,033 images.** A full sort of that takes no time anyone notices.
- **The benchmark predates the change** and builds its schema with `create_all`. M9 put the
  measuring stage (6) after the stages it would have measured.

### Fix

M9 stage 9 (`docs/dev/scale-plan/README.md` § Stage 9), merged 2026-11-09 as PR #134.
Three parts, and no new index:

- **Ties break on `rowid`, in the sort's own direction.** Every index entry already ends
  with `rowid`, so `aesthetic_score DESC, rowid DESC` is the index read backwards.
  `_apply_image_ordering` applies the one rule to every branch.
- **`GET /images/` picks the page's ids first, then loads those rows.** Thirteen sort
  columns have no index and still sort the dataset; the sorter now holds an id per row.
- **`backend/tests/test_image_sort_plan.py` reads the plan.** Each sort field is listed as
  indexed or unindexed. An indexed one fails on a `TEMP B-TREE` step, an unindexed one
  fails without it, and an unlisted one fails until it is classified. Run against the old
  ordering, the plan it reads is the full sort above.

Timed through the ORM on scratch databases shaped like the table above (best of three,
warm cache, `LIMIT 100`):

| Query | Before | After |
|---|---|---|
| Aesthetic, best first, page 1 | 287 ms | 3.0 ms |
| The same, `OFFSET 60000` | 2,908 ms | 5.0 ms |
| The same, `OFFSET 10000`, 20,000 ComfyUI images | 7,056 ms | 11.1 ms |

One request got slower: page 1 of the default sort (`created_at`, newest first) went from
1.2 ms to 2.4 ms, the price of the second statement. `docs/dev/gallery/filters.md` holds the
rule as it now stands.

### Status & date

MITIGATED — found 2026-11-09, fixed the same day by M9 stage 9. The guard test covers the
gallery's sorts. Since M9 stage 7 (2026-11-11) `backend/tests/test_stats_plan.py` covers
the Stats statements too: stage 11's index had changed every one of their plans with no
test to see it, the same failure by another road. Another paged query that gains an
`ORDER BY` key still has no guard, and one is known to sort already: style scoring's keyset
page sorts the whole dataset for every page of 2,000 (the stage 7 addendum of
`backend/scripts/scaling_bottlenecks_report.md`).
Last reviewed for staleness: 2026-11-11.
