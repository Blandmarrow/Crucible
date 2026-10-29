# Roadmap

**Status: living.** The *sequencing* layer: the order work happens in and what blocks what.
It owns **order and dependencies only**. The *what* is in the linked files, the *why* in
[decisions.md](decisions.md) and the postmortems.

**Keeping this current:** when a task lands, the same session ticks it here and adds the
next one, the same way it runs the drift check. When a milestone completes, the next one
gets expanded into ordered tasks, and the finished milestone shrinks to a one-line "Landed"
entry linking its record in [roadmap-archive/](roadmap-archive/) (status
`archive (landed DATE)`). Archive eagerly, as soon as only open follow-ups remain.

**Detail is tiered:** the next milestone is checkbox-level; later ones are one-line
destinations until the work before them teaches us their shape.

A feature with its own design plan keeps that plan in a transient folder (`TRANSIENT_DIRS`
in `scripts/check_docs.py`); this file names the plan and its place in the order, and never
restates its stages.

## Next session (updated 2026-10-29)

1. M2 runs on branch `docs/architecture-v3.1`, which holds M1–M3. Do not merge it until M3
   is done or the user says otherwise.
2. Finish M2 with the second CLAUDE.md seam. Quote CLAUDE.md's word count before (5,955 on
   2026-10-29) and after in the commit.
3. Precaution: moved bullets go verbatim, and each leaves a pointer that still states the
   rule and names its destination; the rules every module touches stay whole (see the
   queue entry's Watch for).

## Landed

- **M1 — Drift check upgrade** (2026-10-29): the reference checker, CI on every PR, this
  roadmap and the decision log. Record:
  [roadmap-archive/m1-drift-check-upgrade.md](roadmap-archive/m1-drift-check-upgrade.md).

## Milestones

### M2 — CLAUDE.md

Gate: CLAUDE.md under its 5,400-word budget, with every gate listed in one table.

- [x] A `## Verification` section: the gate table (local, CI, blocking, and what CI cannot
      run), the `tsc --noEmit` trap, and the report-what-the-run-says rule. The `qa-smoke`
      skill points at it.
- [x] A `## Working here` section with one dated milestone line linking this file.
- [x] The first CLAUDE.md seam: five invariant bullets to their topic files (the Windows
      open-file trap keeps its rule; only its site list moved) and the test conventions to
      [backend-tests.md](backend-tests.md). 7,013 → 5,955 words.
- [ ] The second CLAUDE.md seam, about 555 words: [pending-splits.md](pending-splits.md)
      § CLAUDE.md (reviewer site-lists in the three largest bullets, and Map "covers"
      halves that duplicate a file's intro).

### M3 — The split queue

The seams live in [pending-splits.md](pending-splits.md); this is only their order. Each
split re-measures before cutting, and a seam the file has grown past is re-chosen.

1. `docs/dev/image-similarity.md`, then `docs/scoring.md` (their new files share a name).
2. `docs/dev/gallery.md` (the recorded seam needs re-cutting).
3. `docs/dev/labels.md` (both seams), then `docs/dev/ml-models.md` (both seams).
4. The other dev docs, largest first.
5. The user docs, each with its README Docs-table and `docs/features.md` rows; README
   § Features last.
6. `pending-splits.md` itself, which should fall under budget once its entries are gone.

### M4 — Rating feature

Independent of M2–M3. Next: the rating column (§1). Build order and stage detail:
[rating-plan/README.md § Sequencing](rating-plan/README.md#sequencing). Open rulings go in
the decision log as they are made.
