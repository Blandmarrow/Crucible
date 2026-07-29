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

### Structure first — these need headings before they can be split well

Four files are organised with `**Bold**:` lead-ins and have no ATX sub-headings (or one
wrapping everything). They are not over budget, so no split is due. But the missing
structure is a prerequisite: `scripts/check_docs.py` picks a seam by reporting per-section
word counts, and for these it can only say "read the file". Add `##` headings along the
groupings below — the lead-ins already mark every boundary — and the eventual split
becomes mechanical. Do this when next editing the file, not as a standalone sweep.

## docs/dev/export.md

- **Problem:** 3,184 words (91%), one `#` title and no sub-headings. Its longest paragraph
  is 246 words against the 250 limit — already at the compression ceiling.
- **Proposed headings:** The shared export loop (shared loop, stem uniquification, disk
  preflight) · Filters and license controls (filters, page controls, `unlicensed_count`) ·
  Provenance manifests (manifests, untrusted values, lifecycle) · Output options (caption
  format, resize, strip metadata, loss masks, captions-only)
- **Watch for:** the eventual seam is almost certainly Provenance manifests → its own file,
  since `docs/dev/provenance.md` already owns that vocabulary and is itself at 91%.

## docs/dev/detection.md

- **Problem:** 2,888 words (83%), no sub-headings.
- **Proposed headings:** Router and endpoints · Model and task matrix (tasks by model,
  multi-phrase prompts, `_ALLOWED_MODELS`) · ML inference · Storage · Frontend surfaces
- **Watch for:** § Deferred work & upstream constraints is a status note, not reference —
  it dates fastest and should not travel into a new file unexamined.

## docs/dev/statistics.md

- **Problem:** 2,845 words (81%), no sub-headings.
- **Proposed headings:** Frontend page (panel organization, live polling, error states) ·
  Backend aggregation (server-side aggregation, validator-keyed cache, `DatasetStats`
  schema and its subfolder invariant) · Panels (editable histograms, BucketPanel,
  detections, licenses, lightbox) · CSV export
- **Watch for:** the Frontend/Backend boundary is the natural seam if it ever trips.

## docs/dev/versioning.md

- **Problem:** 3,166 words (90%) with 3,141 of them under a single `### Dataset versioning`
  — structurally identical to having none.
- **Proposed headings:** promote the existing `###` contents to `##` groups: Model and
  storage (object store, GC, `is_present`, DB tables, `DatasetVersion` fields,
  `passive_deletes`) · Guards (versioning mode, dataset-busy) · Backend (router, service,
  copy-on-write injection points) · Frontend (+ TanStack Query keys) · Provenance mirror
  and regression tests
- **Watch for:** the `### Dataset versioning` heading duplicates the `# Dataset versioning`
  title; deleting it is the first move. Its anchor may be linked — grep before removing.
