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

## Next session (updated 2026-10-30)

1. M5 runs ahead of M4. PRs 1–5 are ticked because they're written and reviewed, but none is
   merged: each sits on its own branch stacked on the last (`feat/app-stored-prompts`, then
   `-presets`, `-comfy-settings`, `-captioning-workflow`, `-categories`). Opening or merging
   them is the user's call. Before PR 4 merges, it still needs the hand check against real
   browser data in [server-state-plan/README.md](server-state-plan/README.md).
2. M4 starts on a fresh branch off `main` after M5's PRs 1–5 land. Its first task is the rating
   column (§1); read [rating-plan/README.md](rating-plan/README.md) before starting.
3. Two docs seams stay queued in [pending-splits.md](pending-splits.md) with nothing over
   budget: `README.md`'s § Features deletion (deferred when its budget went to 4,000) and
   `docs/scoring.md`'s residual seam. Either is picked up by the next session that appends
   to its file. Re-measure first and check both halves land under 60% of budget — five of
   M3's seven recorded seams had to be re-cut on execution.
4. A split moves sections, so `§` pointers that named a section in the same file now
   point across files. `check_docs.py` does not check them, so grep `§` in every touched
   file and check each against the destination's headings by hand.

## Landed

- **M1 — Drift check upgrade** (2026-10-29): the reference checker, CI on every PR, this
  roadmap and the decision log. Record:
  [roadmap-archive/m1-drift-check-upgrade.md](roadmap-archive/m1-drift-check-upgrade.md).
- **M2 — CLAUDE.md** (2026-10-29): a Verification section with one gate table, and CLAUDE.md
  back under budget at 5,399 words. Record:
  [roadmap-archive/m2-claude-md.md](roadmap-archive/m2-claude-md.md).
- **M3 — The split queue** (2026-10-30): every over-budget doc split, four families behind
  hubs, and README's budget raised to 4,000 rather than cut. Record:
  [roadmap-archive/m3-split-queue.md](roadmap-archive/m3-split-queue.md).

## Milestones

### M5 — App-stored prompts

Saved prompts move from the browser to the database. It is listed ahead of M4 because it runs
first, and neither depends on the other. Gate: PRs 1–5 merged, with existing browser data
carried over. Design: [server-state-plan/README.md](server-state-plan/README.md). Ruling:
[decision 3](decisions/03-app-stored-prompts.md).

- [x] PR 1: backend models, migration, routes and the import endpoint.
- [x] PR 2: caption prompt presets. Needs PR 1.
- [x] PR 3: ComfyUI "Generate prompts" settings, and the conflict dialog. Needs PR 2.
- [x] PR 4: the captioning workflow blob. Needs PR 3, for the writer and the loading gate.
- [x] PR 5: declared categories. Needs PR 2.
- [ ] Cleanup, one release after PR 5: delete the legacy keys, the import code and the plan
  folder, then archive this milestone.

### M4 — Rating feature

Gate: the rating column and the learned head shipped, each with its user docs. Build order
and stage detail: [rating-plan/README.md § Sequencing](rating-plan/README.md#sequencing).
Open rulings go in the decision log as they are made.

- [x] Token UI (§6) and the aesthetic model picker (§2), both shipped 2026-08-02.
- [ ] The rating column (§1). Ships alone and needs no ML.
- [ ] The learned head (§3). Needs §1 and §2.
- DINOv3 (§5) stays deferred, the lowest priority; it needs only §2.
