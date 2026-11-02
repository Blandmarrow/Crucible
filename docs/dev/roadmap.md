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

## Next session (updated 2026-11-03)

1. M5's five stages merged on 2026-10-30 as PR #101. Only its cleanup is open, and that waits
   a release. See [server-state-plan/README.md](server-state-plan/README.md) for what was
   checked by hand.
2. **M4 is implemented but held.** The port is complete on `feat/m4-rating-port` (pushed,
   not merged): the rating column, phase 0, rebindable rating keys and the docs, with every
   gate green. The user chose on 2026-11-02 not to ship it yet, so PR #106 was closed and the
   branch waits. On that branch only, for now: decision 4 (rating keys and old snapshots), the
   rebuilt `rating-plan/README.md` and the M4 roadmap ticks. Its source, `feat/scoring-arc`,
   is deleted; all four of its commits are on the port branch. Before merging, rebase onto
   `main` and re-run the gates; its migrations end at `e7c4a1b9d2f6`.
3. **M7 no longer waits on M4** ([decision 5](decisions/05-m7-goes-ahead-without-m4.md)).
   DINOv3 was tested offline on 2026-11-02, on branch `exp/dinov3-test`. That branch is cut
   from `style-embedding-measurement` and changes no app code. Its result is in that branch's
   docs/dev/roadmap/style-embedding.md, § The DINOv3 run and § The seven-illustrator run.
   DINOv3 tied DINOv2 on seven real styles, so Stage 2 is not worth building on this
   evidence. Next for M7: port the branch. Its DINOv2 layer-7 change won on both test sets,
   which also answers the layer-7 question. The style-model research ran on 2026-11-03, on
   branch `exp/style-models` (pushed, not merged). Kaloscope 3.0 and CSD passed and PE-Core
   tied; the layer-7 baseline beat `main` again on a third set. It also found that the app
   builds CLIP with the wrong activation. **Pick up here:** the owner asked for the same
   test on photographs, 3D art and digital paintings. The feasibility pass is done. Start by
   putting its three open choices to the owner, in
   [style-model-research/README.md § Next: other media](style-model-research/README.md#next-other-media-owners-request-2026-11-03).
   M6 is still independent and can go any time.
4. M8's follow-ups 9, 10 and 11 can go any time. Batch move and copy, in item 9, must wait for
   M4 to merge. Items 7, 8, 12 and 13 wait for stages 6 and 3, which replace that code.
5. Two docs seams stay queued in [pending-splits.md](pending-splits.md) with nothing over
   budget: `README.md`'s § Features deletion (deferred when its budget went to 4,000) and
   `docs/scoring.md`'s residual seam. Either is picked up by the next session that appends
   to its file. Re-measure first and check both halves land under 60% of budget — five of
   M3's seven recorded seams had to be re-cut on execution.
6. A split moves sections, so `§` pointers that named a section in the same file now
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

## Porting the unmerged branches

Three branches hold work from August that never reached `main`, and none has an open PR. They
land one at a time. M4 and M7 conflict with each other in six files, and whichever lands second
resolves them; M7 does not wait for M4 ([decision 5](decisions/05-m7-goes-ahead-without-m4.md)).
M6 touches neither and is the smallest.

1. Cut each port's branch from the latest `main`, and merge it before starting the next. All
   three add migrations from old revisions, so each re-parents onto the head the last one left.
2. `git cherry-pick -x` the commits its milestone names, so the messages and the link to the
   original survive. Skip merge commits.
3. Port doc changes by hand into `main`'s current layout rather than resolving their conflicts.
   A plan file gets its own transient folder, registered in `TRANSIENT_DIRS`.
4. Before merging, check every path added to `main` since the branch was cut. A clean port only
   reconciles lines that clash, and a missed mirror or rebuild path fails silently.
5. Leave the source branch untouched until its port has merged. M4 is the exception: the user
   had `feat/scoring-arc` deleted on 2026-11-02 once every commit was verified on the port
   branch, which is held unmerged.

## Milestones

### M5 — App-stored prompts

Saved prompts moved from the browser to the database, merged on 2026-10-30 as PR #101. Neither
this nor M4 depends on the other. Gate: the cleanup below done, once browsers have had a release
to copy their data over. Design: [server-state-plan/README.md](server-state-plan/README.md). Ruling:
[decision 3](decisions/03-app-stored-prompts.md).

- [x] Stage 1: backend models, migration, routes and the import endpoint.
- [x] Stage 2: caption prompt presets. Needs stage 1.
- [x] Stage 3: ComfyUI "Generate prompts" settings, and the conflict dialog. Needs stage 2.
- [x] Stage 4: the captioning workflow blob. Needs stage 3, for the writer and the loading gate.
- [x] Stage 5: declared categories. Needs stage 2.
- [ ] Cleanup, one release after PR #101: delete the legacy keys, the import code and the plan
  folder, then archive this milestone.

### M4 — Rating feature

Gate: the rating column and head phase 0 shipped with their user docs, and the learned head
either shipped or ruled out by phase 0's numbers. Build order and stage detail:
[rating-plan/README.md § Sequencing](rating-plan/README.md#sequencing), which the port brings up
to date. Open rulings go in the decision log as they are made.

- [x] Token UI (§6) and the aesthetic model picker (§2), both shipped 2026-08-02.
- [x] Port the rating column (§1) and head phase 0 (the rating event log and metrics page) from
  `feat/scoring-arc`: 67ac441, 9004f18, 4233da1, ce0c788. Ported 2026-11-02 on
  `feat/m4-rating-port`, with rebindable rating keys and old snapshots leaving ratings alone.
  **Held unmerged** by the user's choice; the source branch is deleted.
- [ ] Merge `feat/m4-rating-port` once the user decides to ship the feature.
- [ ] The learned head (§3, phases 1+). Needs the port and re-ratings to measure. Not built if
  phase 0 shows an existing scorer already near its ceiling.
- DINOv3 (§5) stays deferred, the lowest priority. M7's plan supersedes §5, so it is settled
  there.

### M6 — ComfyUI per-row folders and automation

Gate: per-row folders merged with their docs, then the remaining stages expanded here in their
plan's order.

- [ ] Port per-row output folders from `comfy-per-row-subfolders`: c2d6a6d. Its plan comes with
  it (docs/dev/roadmap/comfy-automation.md on that branch).

### M7 — Style embedding

Gate: the layer and blend change merged with its docs, then the remaining stages expanded here
in their plan's order. Does not wait for M4 ([decision 5](decisions/05-m7-goes-ahead-without-m4.md)).

- [x] Test DINOv3 offline before building it in (2026-11-02, `exp/dinov3-test`, no app code).
  Two pre-registered runs. On two anime styles (`test2`) three of four DINOv3 settings beat
  DINOv2 layer 7, partly on a file-format confound. On seven public-domain illustrators
  (`test3`, identical files) all four tied it, and every `test2` hypothesis failed. **Verdict:
  do not build DINOv3 on this evidence.** Reading DINOv2 at layer 7 instead of its final
  embedding beat `main` on both sets, by +0.085 to +0.223. Detail: § The DINOv3 run and § The
  seven-illustrator run in that branch's docs/dev/roadmap/style-embedding.md.
- [x] Research and test other models for style similarity (2026-11-03, `exp/style-models`, no
  app code). Kaloscope 3.0 and CSD passed a pre-registered test on a new anime set and on
  `test3`; PE-Core tied. Summary: [style-model-research/README.md](style-model-research/README.md).
- [ ] Test the three models on photographs, 3D art and digital paintings, at the owner's
  request. The feasibility pass is done; three choices are open first. Detail:
  [style-model-research/README.md § Next: other media](style-model-research/README.md#next-other-media-owners-request-2026-11-03).
- [ ] Decide whether to adopt Kaloscope (or CSD, the permissive fallback) as a style mode. It
  is the owner's call, then an adoption plan: licence, its own embedding column and
  discriminator, residency. Needs the M7 port first.
- [ ] Plan the CLIP QuickGELU fix. `model_manager` builds CLIP with GELU, but the weights
  expect QuickGELU. It touches every stored `clip_embedding` and the aesthetic scorer's
  input, so old and new vectors must never mix.
- [ ] Decide whether the layer 7 default stands, given that the photograph test failed its
  pre-registered criterion. See docs/dev/roadmap/style-embedding.md on the branch.
- [ ] Port from `style-embedding-measurement`: every commit except 3b00223, already on `main`,
  and the doc splits 9f6219e and 9156988, because `main` made its own.

### M8 — Structural cleanup

Four bug fixes, then targeted consolidation rather than a broad restructure. Gate: stage 1 landed
with regression tests; the later stages can stop wherever the duplication stops costing anything.
Detail, hazards and what to leave alone:
[cleanup-plan/README.md](cleanup-plan/README.md).

- [x] Stage 1: fix B1 (failed job shows success), B2 (stale dataset counts), B3 (bind limit) and
  B4 (unchecked write path). Landed 2026-11-02 on `fix/m8-stage1-bugs`.
- [x] Stage 1 follow-ups 1–6 (cleanup plan § Stage 1 follow-ups). Landed 2026-11-02 on
  `fix/m8-stage1-followups`. Items 7 and 8 there are folded into stages 6 and 3.
- [ ] Stage 1 follow-ups 9 (the rest of the unchunked `IN` class), 10 (batch resize, crop and
  detection crop's stats on a partway failure) and 11 (two hard-cancel edges in the stats
  wrapper). No order between them; item 9's batch move and copy need M4 merged. Items 12 and
  13 are folded into stage 6, beside item 7.
- [ ] Stage 2: one shared helper for a job's image scope.
- [ ] Stage 3: a shared job loop for upscale and LUT. Needs stage 2.
- [ ] Stage 4: `DatasetsPage` dialogs into components.
- [ ] Stage 5: `SelectionToolbar` dialogs into forms. Needs M4 and M7 merged.
- [ ] Stage 6: the detail page's inline tools onto those forms. Needs M4 and M7 merged, and B1.
- [ ] Stage 7: `StatsPage` into files. Needs M4 merged.
- [ ] Stage 8: split `images.py` by section. Needs M4 merged and stage 2.
- [ ] Any time: fix the coverage config, and correct CLAUDE.md § Data flow.
