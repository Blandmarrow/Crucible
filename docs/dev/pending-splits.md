# Pending documentation splits

A handoff queue, not a topic file — it has no Documentation Map row and nothing reads it
to learn about a subsystem. One entry per doc that needs restructuring, naming the seam.

It exists because the two halves of a split want opposite conditions. **Choosing** the
seam is best done by whoever just worked the file and can see which section stopped
belonging — knowledge that evaporates when the session ends. **Executing** the split is
an exhaustive sweep (move sections, update every inbound reference, fix `§` pointers, add
a Map row) that wants a fresh context and goes wrong in a long one. So the seam is
recorded here at the end of the session that trips the budget, and executed at the start
of the next session that would append to that file.

Nothing here is ever a reason to compress prose to get back under budget. See the
`doc-maintenance` skill, § Splitting an oversized doc.

## How entries work

The heading is the file's repo-relative path, exactly as `scripts/check_docs.py` prints
it — the checker matches on it in both directions.

```markdown
## docs/dev/example.md

- **Moves:** § Section A, § Section B
- **New file:** docs/dev/example-detail.md (mirrors docs/example.md)
- **Why here:** the two sections are about the router; the rest is the service layer
- **Watch for:** § Section A is referenced from docs/dev/other.md
```

`Watch for` is the field worth spending time on — it carries what the next session cannot
cheaply rediscover. The checker catches broken paths and anchors after the fact; it
cannot tell you which seam was the right one.

**Write a proposed new filename as plain text, never in `backticks`.** The file does not
exist yet, and an inline-code path that does not resolve is a check FAIL — an entry that
breaks CI is worse than no entry.

Three bands govern an entry's lifetime. A file **over budget** with no entry is a WARN
(record one). A file with an entry that has since dropped **under 60%** of budget is a
WARN the other way (the split evidently happened — delete the entry). Between the two is
the working band: entry recorded, split pending, no warning. A file that is a dumping
ground while sitting *below* that 60% floor — so it would read as already-split — takes a
`## docs/dev/example.md (structural)` heading, whose trailing marker exempts it from the
staleness sweep while still recording the seam.

## Queue

## docs/dev/backend-infrastructure.md

- **Moves:** § Database (1,142 words — the largest section by a factor of two), plus its
  two neighbours that are really about the same thing, § Startup database backup and
  § Migration drift check
- **New file:** docs/dev/database.md
- **Why here:** the file is two subjects wearing one title. One is the *schema and the
  SQLite engine* — per-connection pragmas, FK enforcement and every `ondelete`, the index
  inventories, deferred blob columns, the backup rotation, the drift check. The other is
  the *running process* — the lifespan hook, production frontend serving, the server
  control endpoints and the restart loop, SSE, job cancellation, the retention sweep, and
  the open `JobQueue.stop()` hang. A reader arrives for exactly one of the two and there
  is no overlap between them. Splitting there leaves ~1,785 and ~1,720 words, both close
  to the 60% target, which is why the seam is here and not between § Database and
  everything else.
- **Watch for:** the § Database prose is cited from several directions and every one of
  these is a `§`-level pointer the checker cannot verify — `docs/dev/video.md` and
  `docs/dev/video-extract.md` (the frame-lineage FK and `ix_images_source_video_id`),
  `docs/dev/versioning.md` (the versioning tables' cascades and the two decorative
  model-level FKs), `docs/dev/provenance.md` (the dropped `ix_images_dataset_license`),
  and CLAUDE.md's Documentation Map row and § Tests line. The migration-drift paragraph
  is also referenced from `scripts/check_migrations.py`'s own module docstring. The new
  file mirrors no user doc, so the naming convention gives no name for free — `database.md`
  is a proposal, not a constraint.

The five entries previously recorded here were executed on 2026-07-31: `bulk-ops.md` →
`bulk-image-jobs.md`, `versioning.md` → `versioning-service.md`, `export.md` →
`export-licensing.md`, `detection.md` → `detection-inference.md`, and `statistics.md`'s
misfiled `GET /images/` filter table → `image-filters.md`.
