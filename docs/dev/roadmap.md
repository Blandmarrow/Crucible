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

## Next session (updated 2026-11-07, M6 hand checks on the box)

1. M5's five stages merged on 2026-10-30 as PR #101. Only its cleanup is open, and that waits
   a release. See [server-state-plan/README.md](server-state-plan/README.md) for what was
   checked by hand.
2. **M4 is implemented but held.** The port is complete on `feat/m4-rating-port` (pushed,
   not merged): the rating column, phase 0, rebindable rating keys and the docs, with every
   gate green. The user chose on 2026-11-02 not to ship it yet, so PR #106 was closed and the
   branch waits. On that branch only, for now: decision 4 (rating keys and old snapshots), the
   rebuilt `rating-plan/README.md` and the M4 roadmap ticks. Its source, `feat/scoring-arc`,
   is deleted; all four of its commits are on the port branch. Before merging, rebase onto
   `main` and re-run the gates; its migrations end at `e7c4a1b9d2f6`. If M7 merges first, M4's
   first migration `c3b8e1d7a52f` re-parents from `f2a6c8e4b1d7` onto M7's `c2b8e4f6a1d7`, and
   M4 resolves the overlap in `SelectionToolbar.tsx`, `queryKeys.ts` and `ImageDetailPage.tsx`.
   If M6 merges before M4, M4 re-parents onto M6's last migration instead.
3. **The M7 port merged on 2026-11-03 as PR #108**: layer 7 at 0.30/0.70 is the style
   default, each run records its blend weights, and the layer picker warns before a run that
   must fail. The user had both `style-embedding-measurement` and `feat/m7-style-port`
   deleted afterwards, and `exp/dinov3-test` and `exp/style-models` too: all three research
   runs live on `exp/style-media`, which contains the other two and is the record the docs
   cite. Its plan is
   now [style-embedding-plan/README.md](style-embedding-plan/README.md), and the research
   folder's findings moved into `docs/dev/quality/style-evaluation.md`. What is left in M7 is
   below.
4. **M6 is being built on `feat/m6-comfy-automation`**
   ([decision 7](decisions/07-m6-built-whole-on-one-branch.md)), under the plan
   [comfy-automation-plan/README.md](comfy-automation-plan/README.md) as revised by decisions 8
   and 9. **Work on M6 happens on that branch.** What has landed, stage by stage, is the
   § M6 checklist below; this item holds only what is open.
   - **Next: the merge** (§ M6 below). Every stage is built, and the hand checks the
     milestone owed were run on 2026-11-07 on the second machine (a 16 GB card) against a
     real ComfyUI and a real Strata, with the user's browser checks in Firefox. All passed
     or were fixed that day; the results, check by check, are in
     [the plan's § Hand checks on the box](comfy-automation-plan/README.md#hand-checks-on-the-box-2026-11-07).
     Three failures found there were fixed under rulings by the user: `ContextMenu` keeps a
     disabled item focusable, the library panel's selection actions keep a fixed slot, and
     the Runs page refetches on any job of its dataset. LM Studio itself was not used (Strata
     stood in); its handoff path was checked by hand on 2026-11-04.
   - **Not held open: §6c's "cancelling after the snapshot" state.** On the box a snapshot of
     834 images took about 1 s, too short to hold the wait open by hand, so its UI text and a
     second Cancel during it rest on `test_run_snapshot.py`'s gated snapshot.
   - **What the box checks found beyond the owed list was fixed the same day**, under the
     user's rulings: a run that ends `failed` or `cancelled` frees ComfyUI and any local LLM
     it left loaded, and a `TopBar` cancel of a queued child steps its run at once
     (`docs/dev/comfyui/automation.md` § Cancel, pause, resume, restart); a paused run shows
     an amber rail dot and a `TopBar` chip, and the page greys what the server would refuse
     (`docs/dev/comfyui/page.md`); and Restart and Shutdown work under `manage dev`'s
     `--reload` (`docs/dev/http-edge.md`), a bug `main` has too and gets with the merge.
     That Restart was confirmed live on Linux; on Windows it is unit-tested only, since
     uvicorn's reloader could not restart a worker in the headless session it was tried in
     (a bare uvicorn app hung the same way), so one run of `manage.ps1 dev` plus Restart in
     a real terminal is still owed.
   - **Found in the §8b review, not fixed there:** the gallery's
     `dataset_service.repath_subfolder` matches a folder's subtree with SQLite's case-blind
     `LIKE`, so renaming `knight` also re-paths `Knight/a`; `filesystem.py`'s
     `startswith(autoescape=True)` has the same shape. A fix for `main`, not this branch.
   - **Found in the §8 review, left open on purpose:** `utils.normalize_subfolder` keeps
     whitespace inside a segment, so a hand-typed `knight ` (or `knight /  `) is a folder of its
     own, which blueprint leaf `knight` (whose segments are stripped) never adopts or locks.
     It predates §8, and stripping now would re-read stored rows, so it wants a ruling and a
     data migration rather than a quiet change.
   - Decisions 7–10 and this M6 checklist exist only on that branch until it merges, so
     `main`'s next decision takes number 11. Its migrations `e5c1a9d3f7b2` →
     `b7d2f4a9c1e3` → `d4a8c2e6f1b9` → `f3b9d1a7c5e2` → `c8e4a6b2d0f7` → `a6d2f8c4e0b3` →
     `b2e7c9a4d1f6` → `c4a8e2f6b9d3` → `d5b7f9a1c3e6` → `e7d1c5a9b3f2` → `a3c7e9b1d5f2` follow
     `main`'s `c2b8e4f6a1d7`; rebase at stage boundaries.
5. M8's follow-ups 8, 9, 10 and 11 can go any time; stage 3 left inpaint and detection crop
   out, so item 8 no longer waits for it. Batch move and copy, in item 9, must wait for M4 to
   merge. Items 7, 12 and 13 wait for stage 6, which replaces that code. M8 stages 5 and 6
   now wait only for M4, since M7 has merged. Stage 4 can go any time. What stages 2 and 3
   found and left alone is in [cleanup-plan/README.md](cleanup-plan/README.md) § Found during
   stages 2 and 3, left as they were.
6. Four docs seams stay queued in [pending-splits.md](pending-splits.md) on `main` with nothing
   over budget: `README.md`'s § Features deletion (deferred when its budget went to 4,000),
   `docs/scoring.md`'s residual seam, `docs/dev/quality/style-similarity.md` at 91% after
   the M7 port, and `docs/dev/image-processing-models.md` at 98% after M8 stage 3. This
   branch's own, comfy-structured.md, was split three ways on
   2026-11-03 and its family hubbed (pending-splits.md records how). Each is picked up by the
   next session that appends to its file. Re-measure first and check both halves land under 60% of budget — five of
   M3's seven recorded seams had to be re-cut on execution.
7. A split moves sections, so `§` pointers that named a section in the same file now
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

Three branches held work from August that never reached `main`. M7's has landed. M4's and
M6's are both ported and held on their own branches: M4 until the user chooses to ship it, M6
until its whole build is done ([decision 7](decisions/07-m6-built-whole-on-one-branch.md)). M4
and M7 conflicted in six files, and M4, landing second, resolves them; M7 did not wait for M4
([decision 5](decisions/05-m7-goes-ahead-without-m4.md)). M6 shares no files with M4, but both
add migrations, so **whichever of the two merges second re-parents its first migration** onto
the other's head.

1. Cut each port's branch from the latest `main`, and merge it before starting the next. All
   three add migrations from old revisions, so each re-parents onto the head the last one left.
2. `git cherry-pick -x` the commits its milestone names, so the messages and the link to the
   original survive. Skip merge commits.
3. Port doc changes by hand into `main`'s current layout rather than resolving their conflicts.
   A plan file gets its own transient folder, registered in `TRANSIENT_DIRS`.
4. Before merging, check every path added to `main` since the branch was cut. A clean port only
   reconciles lines that clash, and a missed mirror or rebuild path fails silently.
5. Leave the source branch untouched until its port has merged, then delete it. M4 and M6 are
   the exceptions: the user had `feat/scoring-arc` deleted on 2026-11-02 and
   `comfy-per-row-subfolders` on 2026-11-03, once every commit was verified on the port
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

Gate: the whole build below, done on `feat/m6-comfy-automation` and merged once
([decision 7](decisions/07-m6-built-whole-on-one-branch.md)). Plan:
[comfy-automation-plan/README.md](comfy-automation-plan/README.md), revised by
[decision 8](decisions/08-templates-first-filed-by-folder.md) and
[decision 9](decisions/09-runs-step-forward-on-job-completion.md). Tick each stage when its
gates are green and it is pushed.

- [x] Per-row folders ported (2026-11-03), the comfyui doc splits, §7 the mock ComfyUI, §2a
  blueprints, §2b the outline call, §2c Top up, §3 the rewrite pass, the rebase onto M8
  stages 2–3, §4 the stepping run, §5 the goal loop, §6 the dry run, snapshot and recipes, and
  the review-fix pass (all by 2026-11-04). What each delivered is the plan's § Landed stages.
- §8, the ComfyUI page rework, so prompts are easier to categorize and the prompt library
  easier to use (the user's call, 2026-11-04: it lands before the merge). Scoped with the
  user on 2026-11-05 and the mockup approved; the *what* is
  [comfy-automation-plan/README.md § 8](comfy-automation-plan/README.md#8-the-comfyui-page-rework-landed).
  Its open questions were ruled by the user on 2026-11-05.
  - [x] §8a, the layout, and §8b, the plan rail, folder tree and grouped table (2026-11-05).
    What each delivered is the plan's § Landed stages.
  - [x] §8c, the library side panel, Copy to plan, the row menu and drag and drop
    (2026-11-05). What it delivered is the plan's § 8c.
  - [x] §8d: the Add menu, the selection bar, the paste dialog on `ModalShell`
    (2026-11-05). What it delivered is the plan's § 8d.
  - [x] §8 review fixes (2026-11-05), under four rulings by the user that day: the plan's
    [§ Review fixes](comfy-automation-plan/README.md#review-fixes-2026-11-05).
- [x] §9, two installs and a 16 GB card (2026-11-06): `CRUCIBLE_PORT`/`CRUCIBLE_VITE_PORT`, the
  orphan sweep's refusal on a mismatched tree, and the Strata GPU handoff
  (`docs/dev/comfyui/gpu-handoff.md`, [decision 10](decisions/10-unload-strata-while-comfyui-generates.md));
  running several installs is `docs/dev/multiple-installs.md`. Its migration `a3c7e9b1d5f2` is
  the branch's new last.
- [ ] Merge: rebase onto `main`, re-parent the first migration onto M4's head if M4 merged
  first, run every gate, open the PR. The ComfyUI docs moved behind a hub on this branch, so
  any doc `main` has added since that names an old path (comfy-prompts.md, comfyui-sync.md, or
  `docs/dev/comfyui.md` meaning the queue) needs repointing; `check_docs.py` FAILs on the first two.

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
- [ ] Stage 2b: stop the layer labels asserting things nobody measured
  (`DINO_LAYER_LABELS`). The plan's only defect rather than improvement. Detail:
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
- [ ] Stage 1 follow-ups 8 (a refusal's reason for the other edit jobs), 9 (the rest of the
  unchunked `IN` class), 10 (batch resize, crop and detection crop's stats on a partway
  failure) and 11 (two hard-cancel edges in the stats wrapper). No order between them; item
  9's batch move and copy need M4 merged. Item 8 was folded into stage 3, which left inpaint
  and detection crop out, so it is open again. Items 12 and 13 are folded into stage 6,
  beside item 7.
- [x] Stage 2: one shared helper for a job's image scope. Landed 2026-11-04 on
  `refactor/m8-stages-2-3`.
- [x] Stage 3: a shared job loop for upscale and LUT. Needs stage 2. Landed 2026-11-04 on the
  same branch.
- [ ] Stage 4: `DatasetsPage` dialogs into components.
- [ ] Stage 5: `SelectionToolbar` dialogs into forms. Needs M4 merged (M7 has).
- [ ] Stage 6: the detail page's inline tools onto those forms. Needs M4 merged (M7 has), and B1.
- [ ] Stage 7: `StatsPage` into files. Needs M4 merged.
- [ ] Stage 8: split `images.py` by section. Needs M4 merged and stage 2.
- [ ] Any time: fix the coverage config, and correct CLAUDE.md § Data flow.
