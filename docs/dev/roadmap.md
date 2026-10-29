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
2. The budget raise, the Quality, Gallery and Versioning hubs, the whole video family (the
   folder move, the `docs/dev/video/reextract.md` split and the three-way `docs/video.md`
   split) and the three-way `docs/dev/labels.md`, `docs/dev/ml-models.md`,
   `docs/dev/captioning.md`, `docs/dev/bulk-image-jobs.md` and `docs/dev/file-browser.md`
   splits have landed. Next are the other over-budget dev docs, largest first:
   `backend-infrastructure.md`, then `image-files.md`, each with a seam in
   `pending-splits.md` (re-measure first, and check each seam leaves both halves under 60%
   of budget — the captioning seam as recorded would have left ~93%, the file-browser one
   ~80%, the versioning-service one ~65%, and none of the three went as recorded).
3. A split moves sections, so `§` pointers that named a section in the same file now
   point across files. `check_docs.py` does not check them, so grep `§` in every touched
   file and check each against the destination's headings by hand. The video folder move
   found eight such pointers already broken, most naming a bold run-in rather than a
   heading. Point at the owning heading and name the run-in after it.

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

- [x] Raise `MAX_CLAUDE_WORDS` to 6,000 in `scripts/check_docs.py`, with the dated reason
      pointing at decision 2, and delete the § CLAUDE.md queue entry (2026-10-29).
- [x] Image similarity/scoring hub (2026-10-29): the old image-similarity.md split four
      ways, the old dev scoring.md split two ways, and both moved with scores-stale.md into
      `docs/dev/quality/` behind the `docs/dev/quality.md` hub. `docs/scoring.md` split off
      `docs/style-similarity.md`; its residual seam stays queued.
- [x] Gallery hub (2026-10-29): the old gallery.md (6,017 words) split four ways and moved,
      with the dnd, state and nav files, into `docs/dev/gallery/` behind the
      `docs/dev/gallery.md` hub.
- [x] Video folder (2026-10-29): `docs/dev/video.md`'s eleven detail files moved, without a
      split, into `docs/dev/video/` behind the hub, and twelve Map rows became one.
- [x] The video family's two queued seams (2026-10-29): `docs/dev/video/reextract.md` (the
      request side moved to `docs/dev/video/reextract-endpoints.md` and § Tests into
      `docs/dev/video/tests.md`), then the user doc `docs/video.md`, split three ways into
      itself, `docs/video-extraction.md` and `docs/video-reextract.md`.
- [x] `docs/dev/labels.md`, both seams (2026-10-29): the consumers (filters, export, the
      snapshot mirror, cross-dataset and derivative hooks) moved to
      `docs/dev/label-consumers.md` and the frontend to `docs/dev/labels-ui.md`, as three
      flat siblings rather than a hub.
- [x] `docs/dev/ml-models.md` (2026-10-29): both recorded seams plus a third. LaMa joined
      `docs/dev/image-processing-models.md`; eviction, unloads, leases and releasing moved
      to `docs/dev/model-residency.md`; the ID registry and per-model loader notes moved to
      `docs/dev/model-registry.md`. Three flat siblings, no hub.
- [x] `docs/dev/captioning.md` (2026-10-29): the recorded provider seam plus the picker.
      Providers moved to `docs/dev/llm-providers.md` and the model picker to
      `docs/dev/caption-model-picker.md`; the job half stays at ~2,480 (71%) as one
      subsystem. Three flat siblings, no hub.
- [x] `docs/dev/bulk-image-jobs.md` (2026-10-29): the recorded seam. Detection-driven
      cropping moved to `docs/dev/detection-crop.md` and watermark removal to
      `docs/dev/inpaint.md`; the crop remap stays as its own `##`, since all four crop sites
      use it. Three flat siblings, no hub.
- [x] `docs/dev/file-browser.md` (2026-10-29): three ways rather than the recorded two,
      whose mutations half would have sat at 80%. `POST /move` (API-only) moved to
      `docs/dev/file-browser-move.md`, and rename and delete, the two DB-syncing mutations
      the page fires, to `docs/dev/file-browser-rename-delete.md`; the endpoint table, collisions,
      path safety, § Still open and the page stay. Three flat siblings, no hub.
- [x] Versioning hub (2026-10-29): the recorded restore seam would have left
      `versioning-service.md` at ~65%, and moving the mirrors too made the family four
      files, so it got a hub. Both `versioning-service.md` and the old body of
      `docs/dev/versioning.md` split eight ways into `docs/dev/versioning/` (guards, routes,
      storage, hooks, restore, mirrors, frontend, tests) behind the `docs/dev/versioning.md`
      hub, and two Map rows became one.
- [ ] The other dev docs, largest first. A family that grows past about three files gets
      a hub as it splits.
- [ ] The user docs, each with its README Docs-table and `docs/features.md` rows; README
      § Features last.
- [x] `pending-splits.md` itself (2026-10-29): under budget at ~3,340 once the ml-models
      entry left.

### M4 — Rating feature

Independent of M2–M3. Next: the rating column (§1). Build order and stage detail:
[rating-plan/README.md § Sequencing](rating-plan/README.md#sequencing). Open rulings go in
the decision log as they are made.
