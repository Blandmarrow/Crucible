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

## Next session (updated 2026-11-12, M10 T1 merged)

0. **M10, job throughput, is under way**
   ([decision 14](decisions/14-job-throughput-is-its-own-milestone.md)), planned in
   [throughput-plan/README.md](throughput-plan/README.md). M9 landed on 2026-11-12 (see
   Landed). T1 merged on 2026-11-12 as PR #145. Next is T2 or T3: both are CPU-only and do
   not depend on each other, and T6 waits on nothing. See M10 below. M9 left four things open, none of which blocks M10
   ([roadmap-archive/m9-scale-and-data-safety.md § Left open](roadmap-archive/m9-scale-and-data-safety.md#left-open)):
   - **Two findings wait for the user to place them in the order.** Style scoring's keyset
     page sorts the whole dataset for every page (a `TEMP B-TREE` behind
     `ix_images_dataset_updated_at`), with no test reading that plan. And a report, not
     verified, that an in-place pixel rewrite leaves an image's old embeddings in place.
   - **Not checked by hand:** stage 11's index and gallery changes in a browser, stage 4's
     polling against a real job, and the Stats page under the pin in a browser.
   - **Parked, each with a trigger** in [scale-plan/README.md](scale-plan/README.md): the
     side table ([decision 15](decisions/15-big-columns-stay-in-the-row.md)), stage 4's full
     design, the small-subfolder score sort and stage 11's cache header.
1. **After M10 comes M7**: the CLIP QuickGELU plan or Stage 3, neither of which waits on
   anything (see M7 below).

2. M5's five stages merged on 2026-10-30 as PR #101. Only its cleanup is open, and that waits
   a release. See [server-state-plan/README.md](server-state-plan/README.md) for what was
   checked by hand.
3. **M4 is implemented but held.** The port is complete on `feat/m4-rating-port` (pushed,
   not merged): the rating column, phase 0, rebindable rating keys and the docs, with every
   gate green. The user chose on 2026-11-02 not to ship it yet, so PR #106 was closed and the
   branch waits. On that branch only, for now: decision 4 (rating keys and old snapshots), the
   rebuilt `rating-plan/README.md` and the M4 roadmap ticks. Its source, `feat/scoring-arc`,
   is deleted; all four of its commits are on the port branch. Before merging, rebase onto
   `main` and re-run the gates; its migrations end at `e7c4a1b9d2f6`. If M7 merges first, M4's
   first migration `c3b8e1d7a52f` re-parents from `f2a6c8e4b1d7` onto M7's `c2b8e4f6a1d7`, and
   M4 resolves the overlap in `SelectionToolbar.tsx`, `queryKeys.ts` and `ImageDetailPage.tsx`.
   M6 merged first (2026-11-07), so `c3b8e1d7a52f` re-parents onto M6's last migration,
   `a3c7e9b1d5f2`, instead. Since then decision 12's `b8d2f4a6c1e3` and PR #132's
   `c3e7a9d1f5b2` follow it (the head on 2026-11-09; stage 9 added none). M9 stage 11 added
   `d5f1a8c3e7b2` (an index) on top and stage 7 added none, so re-parent onto whatever
   `main`'s head is at the time.
   **Until it is rebased past M9 stage 2, do not start the M4 branch on a real datasets
   folder**: its startup sweep has no dot-skip and deletes `.orphaned` with everything
   parked there (`docs/dev/dataset-folders.md` § Two known costs).
   M4 also resolves M9's overlap, listed in
   [scale-plan/README.md § Interaction with the other milestones](scale-plan/README.md#interaction-with-the-other-milestones).
   M4's decision, number 4 on its branch, is unaffected; `main`'s next new decision is 16.
4. Five docs seams are queued in [pending-splits.md](pending-splits.md) (counted
   2026-11-12): `docs/dev/comfyui/page.md` and `docs/dev/comfyui/queue.md`, both over budget
   (3,936 and 3,538 words of 3,500), `docs/dev/quality/scoring.md` (3,696, over since T1;
   split it before T5 adds to it), `docs/scoring.md`'s residual
   seam, and `README.md`'s § Features deletion (deferred when its budget went to 4,000). Each
   is picked up by the next session that appends to its file. Re-measure first and check both
   halves land under 60% of budget — five of M3's seven recorded seams had to be re-cut on
   execution.
5. A split moves sections, so `§` pointers that named a section in the same file now
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
- **M6 — ComfyUI per-row folders and automation** (2026-11-07, PR #110): per-row output
  folders, blueprints with the outline call, Top up and the rewrite pass, automated runs with
  the goal loop, gate, budgets, dry run, snapshot and recipes, the ComfyUI page rework, and two
  installs sharing a 16 GB card. Record:
  [roadmap-archive/m6-comfy-automation.md](roadmap-archive/m6-comfy-automation.md).
- **M9 — Scale and data safety** (2026-11-12, PRs #124 to #143): a unique tie-breaker for
  every gallery sort and the index it cost, an orphan sweep that moves folders aside, scoring
  that commits per chunk and can be retried, Stats lists fetched on demand, paged style
  similarity, the paid-call cap counting caption calls, the hot-path wins, three rounds of
  measurement up to 1M rows, and [decision 15](decisions/15-big-columns-stay-in-the-row.md):
  no side table yet, and the Stats scans pinned to their index with a plan guard. Record:
  [roadmap-archive/m9-scale-and-data-safety.md](roadmap-archive/m9-scale-and-data-safety.md).
- **Handoff notes, 2 to 9 November 2026**: the closed items of this file's Next session
  block (M6's loose ends, the M7 port, M8's follow-ups, PRs #131 and #132, Copy from plan),
  moved out on 2026-11-09. Record:
  [roadmap-archive/handoff-2026-11.md](roadmap-archive/handoff-2026-11.md).

## Porting the unmerged branches

Three branches held work from August that never reached `main`. M7's and M6's have landed.
M4's is ported and held on its own branch until the user chooses to ship it. M4 and M7
conflicted in six files, and M4, landing second, resolves them; M7 did not wait for M4
([decision 5](decisions/05-m7-goes-ahead-without-m4.md)). M6 shares no files with M4, but both
add migrations, and M6 merged first, so **M4 re-parents its first migration** onto M6's head,
`a3c7e9b1d5f2`.

1. Cut each port's branch from the latest `main`, and merge it before starting the next. All
   three add migrations from old revisions, so each re-parents onto the head the last one left.
2. `git cherry-pick -x` the commits its milestone names, so the messages and the link to the
   original survive. Skip merge commits.
3. Port doc changes by hand into `main`'s current layout rather than resolving their conflicts.
   A plan file gets its own transient folder, registered in `TRANSIENT_DIRS`.
4. Before merging, check every path added to `main` since the branch was cut. A clean port only
   reconciles lines that clash, and a missed mirror or rebuild path fails silently.
5. Leave the source branch untouched until its port has merged, then delete it. M4 and M6 were
   the exceptions: the user had `feat/scoring-arc` deleted on 2026-11-02 and
   `comfy-per-row-subfolders` on 2026-11-03, once every commit was verified on the port
   branch. M4's port branch is still held unmerged.

## Milestones

### M10 — Job throughput

The next milestone, now that M9 has landed
([decision 14](decisions/14-job-throughput-is-its-own-milestone.md)). It comes before M7.
Gate: T1–T3 merged with their tests, T4's timing recorded, and T5 then built or ruled out
by that timing. Plan: [throughput-plan/README.md](throughput-plan/README.md), which holds
each stage's design and acceptance; this list is the order. PM-035's one-GPU-thread rule
is not touched: only CPU work moves.

- [x] T1: the technical scorer on several cores. Merged 2026-11-12 as PR #145: up to 4
  calls at once, 2.0× to 2.3× faster, scores identical. Timing and the reason for the cap: `docs/dev/quality/scoring.md` § The
  technical scorer runs several images at once.
- [ ] T2: concurrent requests and a reused client for the OpenAI-compatible and Ollama
  captioners, behind a per-provider setting that defaults to 1. An automated run's
  paid-call cap (M9 stage 10) must not overshoot by more than the requests in flight.
- [ ] T3: parallel import, with one decode per file. Name and stem picking stays serial.
- [ ] T4: time how much of a GPU scoring run is image decoding, on a real card. A hand
  measurement, since no gate runs torch. Needs nothing before it, and gates T5.
- [ ] T5: decode ahead of the GPU call, on the default pool. Needs T4, and is built only if
  T4 shows it pays; otherwise a decision rules it out.
- [ ] T6: fetch a model's weights off the GPU thread, so a first-run download inside a
  loader no longer holds up every other GPU call (PM-035's follow-up). Waits on nothing.

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

### M7 — Style embedding

Gate: the layer and blend change merged with its docs, then the remaining stages expanded here
in their plan's order. Plan: [style-embedding-plan/README.md](style-embedding-plan/README.md).
Stage 2 there (DINOv3) is not built, by
[decision 6](decisions/06-layer-7-default-and-no-new-style-model.md). Does not wait for M4 ([decision 5](decisions/05-m7-goes-ahead-without-m4.md)).

- [x] Test DINOv3 offline before building it in (2026-11-02, no app code).
  Two pre-registered runs. On two anime styles (`test2`) three of four DINOv3 settings beat
  DINOv2 layer 7, partly on a file-format confound. On seven public-domain illustrators
  (`test3`, identical files) all four tied it, and every `test2` hypothesis failed. **Verdict:
  do not build DINOv3 on this evidence.** Reading DINOv2 at layer 7 instead of its final
  embedding beat `main` on both sets, by +0.085 to +0.223. Detail: § The DINOv3 run and § The
  seven-illustrator run in docs/dev/roadmap/style-embedding.md on `exp/style-media`.
- [x] Research and test other models for style similarity (2026-11-03, no
  app code). Kaloscope 3.0 and CSD passed a pre-registered test on a new anime set and on
  `test3`; PE-Core tied. Summary: `docs/dev/quality/style-evaluation.md` § Kaloscope, CSD and
  PE-Core.
- [x] Test the three models on photographs, 3D art and digital paintings (2026-11-03,
  `exp/style-media`, no app code). No candidate is general; Kaloscope's verdict is fragile.
  Summary: the same section.
- [x] Decide whether to adopt Kaloscope (or CSD) as a style mode: not for now
  ([decision 6](decisions/06-layer-7-default-and-no-new-style-model.md)). Reopening it needs
  a superseding entry, then an adoption plan: licence, its own embedding column and
  discriminator, residency.
- [ ] Plan the CLIP QuickGELU fix. `model_manager` builds CLIP with GELU, but the weights
  expect QuickGELU. It touches every stored `clip_embedding` and the aesthetic scorer's
  input, so old and new vectors must never mix.
- [x] Decide whether the layer 7 default stands: it does
  ([decision 6](decisions/06-layer-7-default-and-no-new-style-model.md)). It beat `main` on
  all five sets of the two pre-registered runs: anime, illustrators, photographs, paintings
  and 3D films.
- [x] Port from `style-embedding-measurement`: every commit except 3b00223, already on `main`,
  and the doc splits 9f6219e and 9156988, because `main` made its own. Ported 2026-11-03 on
  `feat/m7-style-port` and merged the same day as PR #108; 1d816ec was plan-file prose only
  and arrived with the plan folder.
- [x] Stage 2b: stop the layer labels asserting things nobody measured
  (`DINO_LAYER_LABELS`). Merged 2026-11-08 as PR #119: each label is a depth word plus a
  measured lean. Detail:
  [style-embedding-plan/README.md § Stage 2b](style-embedding-plan/README.md#stage-2b--stop-the-layer-labels-asserting-things-nobody-measured).
- [ ] Stage 3: dense features for prompt-free subject masks. Untested; separate from style
  scoring.

### M8 — Structural cleanup

Four bug fixes, then targeted consolidation rather than a broad restructure. Gate: stage 1 landed
with regression tests; the later stages can stop wherever the duplication stops costing anything.
Detail, hazards and what to leave alone:
[cleanup-plan/README.md](cleanup-plan/README.md).

- [x] Stage 1: fix B1 (failed job shows success), B2 (stale dataset counts), B3 (bind limit) and
  B4 (unchecked write path). Landed 2026-11-02 on `fix/m8-stage1-bugs`.
- [x] Stage 1 follow-ups 1–6 (cleanup plan § Stage 1 follow-ups). Landed 2026-11-02 on
  `fix/m8-stage1-followups`. Items 7 and 8 there are folded into stages 6 and 3.
- [x] Stage 1 follow-ups 8 (a refusal's reason for the other edit jobs), 10 (batch resize, crop
  and detection crop's stats on a partway failure) and 11 (two hard-cancel edges in the stats
  wrapper). Merged 2026-11-07 as PR #114; 11's shutdown edge is accepted, not fixed.
- [x] Stage 1 follow-up 9 (the rest of the unchunked `IN` class). Merged 2026-11-07 as PR #115.
  `_apply_bulk_filters` takes a 422 cap at `SELECT_ALL_ID_CAP` rather than chunking, by the
  user's choice on 2026-11-07.
- [ ] Item 9's remainder: chunk batch move and copy to another dataset. Needs M4 merged, which
  rewrites both routes. Items 12 and 13 are folded into stage 6, beside item 7.
- [x] Stage 1 follow-up 14: guard the copy-mode thumbnail in detection crop and the upscale and
  LUT loop, as inpaint does. Merged 2026-11-08 as PR #116.
- [ ] Stage 1 follow-up 15: the same order for `images.py`'s single-image copy crop and
  `crop_upscale`'s new-file worker. Needs M4 merged, which rewrites `images.py`.
- [x] Stage 2: one shared helper for a job's image scope. Landed 2026-11-04 on
  `refactor/m8-stages-2-3`.
- [x] Stage 3: a shared job loop for upscale and LUT. Needs stage 2. Landed 2026-11-04 on the
  same branch.
- [x] Stage 4: `DatasetsPage` dialogs into components. Merged 2026-11-08 as PR #117.
- [ ] Stage 5: `SelectionToolbar` dialogs into forms. Needs M4 merged (M7 has).
- [ ] Stage 6: the detail page's inline tools onto those forms. Needs M4 merged (M7 has), and B1.
- [ ] Stage 7: `StatsPage` into files. Needs M4 merged.
- [ ] Stage 8: split `images.py` by section. Needs M4 merged and stage 2.
- [x] Any time: fix the coverage config, and correct CLAUDE.md § Data flow. Merged 2026-11-08
  as PR #120: `.coveragerc` sets `concurrency = greenlet,thread`, which lifts
  `images.py` from 55% to 89%.
- [ ] Remove the `minimatch` override in `frontend/package.json` (PR #111, for
  GHSA-g3hv-75wg-m249) once `eslint` and `typescript-eslint` depend on `minimatch ^11`
  themselves. Waits on upstream; check with `npm view eslint dependencies.minimatch`.
