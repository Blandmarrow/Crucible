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

1. M3 runs on branch `docs/architecture-v3.1`, which holds M1–M3. Do not merge it until M3
   is done or the user says otherwise.
2. Start M3 with its first task, the budget raise, so the splits after it have room.
3. Then the image similarity/scoring hub. Re-measure `docs/dev/image-similarity.md`
   first: under the recorded seam the half that stays is ~3,600 words, so it needs a
   second cut. Moving files into the family folder changes their paths; grep each old
   path across `docs/`, `CLAUDE.md`, `backend/` and `frontend/` before and after.

## Landed

- **M1 — Drift check upgrade** (2026-10-29): the reference checker, CI on every PR, this
  roadmap and the decision log. Record:
  [roadmap-archive/m1-drift-check-upgrade.md](roadmap-archive/m1-drift-check-upgrade.md).
- **M2 — CLAUDE.md** (2026-10-29): a Verification section with one gate table, and CLAUDE.md
  back under budget at 5,399 words. Record:
  [roadmap-archive/m2-claude-md.md](roadmap-archive/m2-claude-md.md).

## Milestones

### M3 — The split queue

Gate: every file over budget split or queued with a current seam, and the Map growing by
family rather than by file ([decision 2](decisions.md#2-claudemd-budget-and-hub-routing)).

The seams live in [pending-splits.md](pending-splits.md); this is only their order. Each
split re-measures before cutting, and a seam the file has grown past is re-chosen.

- [ ] Raise `MAX_CLAUDE_WORDS` to 6,000 in `scripts/check_docs.py`, with the dated reason
      pointing at decision 2, and delete the § CLAUDE.md queue entry.
- [ ] Image similarity/scoring hub: split `docs/dev/image-similarity.md` (its recorded seam
      needs a second cut) and move the family's detail files into a folder registered in
      `SPLIT_INDEXES`, with one Map row for the hub. Then split `docs/scoring.md` (the user
      doc whose new file shares the style-similarity name).
- [ ] Gallery hub: split `docs/dev/gallery.md` (the recorded seam needs re-cutting) and do
      the same folder move for `gallery-dnd.md`, `gallery-state.md` and `gallery-nav.md`.
- [ ] Video folder: move `docs/dev/video.md`'s eleven detail files into a folder with
      `video.md` as its index, and fold their Map rows into its one row. No split; the
      family's two queued seams (`video-reextract.md`, `docs/video.md`) run afterwards.
- [ ] `docs/dev/labels.md` (both seams), then `docs/dev/ml-models.md` (both seams).
- [ ] The other dev docs, largest first. A family that grows past about three files gets
      a hub as it splits.
- [ ] The user docs, each with its README Docs-table and `docs/features.md` rows; README
      § Features last.
- [ ] `pending-splits.md` itself, which should fall under budget once its entries are gone.

### M4 — Rating feature

Independent of M2–M3. Next: the rating column (§1). Build order and stage detail:
[rating-plan/README.md § Sequencing](rating-plan/README.md#sequencing). Open rulings go in
the decision log as they are made.
