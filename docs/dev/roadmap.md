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

## Next session (updated 2026-11-05, after M6 §8b)

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
4. **M6 is ported and its build has started, held on `feat/m6-comfy-automation`**
   ([decision 7](decisions/07-m6-built-whole-on-one-branch.md)): per-row folders are 8dee5de
   there, the plan is rewritten in
   [comfy-automation-plan/README.md](comfy-automation-plan/README.md) by decisions 8 and 9, and
   `comfy-per-row-subfolders` is deleted. **Work on M6 happens on that branch.**
   - The ComfyUI dev docs sit behind the hub `docs/dev/comfyui.md`, with eleven files in
     `docs/dev/comfyui/`.
   - §7 (the mock ComfyUI server), §2a (blueprints, see `docs/dev/comfyui/structured.md`),
     §2b (the outline call) and §2c (Top up) have landed there, so the arc's cut point is
     reached.
   - §3, the optional LLM rewrite pass, landed on 2026-11-03
     (`docs/dev/comfyui/structured-llm.md` § The rewrite pass). On 2026-11-04 the user ran a
     draft and a rewrite with LM Studio and a thinking model, and both worked. Two hand
     checks are still open: a Stop mid-run, and a hand-edited row left alone.
   - M8 stages 2 and 3 merged to `main` on 2026-11-04 as PR #109, and this branch was
     rebased onto it the same day.
   - **§4, the stepping run, landed on 2026-11-04** (`docs/dev/comfyui/automation.md`, the
     Runs page), with every automated gate green. The user ran it by hand the same day
     against a real ComfyUI and LM Studio: full runs through every stage worked, `/free`
     really freed ComfyUI's VRAM, and the scorers unloaded after scoring (measured with
     the Windows per-process GPU counters). Still unchecked by hand: Pause, a restart
     mid-run and a `TopBar` cancel.
   - **§5, the goal loop, the gate and budgets, landed on 2026-11-04**
     (`docs/dev/comfyui/run-loop.md`, migration `c8e4a6b2d0f7` for the provider's
     `billed`), with every automated gate green and an Opus review's twelve findings
     fixed. Still unchecked by hand, against a real ComfyUI and LM Studio: a two-pass
     loop to target, and a run stopped by a cap. §4's three hand checks above come
     first, by the user's choice.
   - **§6, the dry run, the snapshot stage and the recipe library, was built on
     2026-11-04**, with the user's three extras (Use these settings, raising a cap to carry
     a run on, the remembered start form) and every automated gate green, and pushed the
     same day. Its hand checks, against a real
     ComfyUI and LM Studio: a dry run and a start with ComfyUI off, a run with Snapshot on
     (and a Cancel during it), a recipe exported and imported, and a pass cap raised.
   - **A branch review and its fixes landed on 2026-11-04** (four code reviewers, two doc
     audits). The user ruled twice: a run whose generate or caption job failed every item
     now fails, and `gate_rejected` clears when an image leaves `rejects/`. Still owed by
     hand against a real ComfyUI: a run whose ComfyUI rejects every row ends `failed` and
     Resume retries it, and a rejected image moved back loses its flag in the gallery and
     the Stats card.
   - **Next M6 work: the ComfyUI page rework, §8**, so prompts are easier to categorize and
     the prompt library easier to use. Scoped on 2026-11-05 in four sub-stages, with a
     mockup the user approved
     ([the plan's § 8](comfy-automation-plan/README.md#8-the-comfyui-page-rework-planned)).
     **§8a, the layout, landed on 2026-11-05** with every frontend gate green and the wide
     and narrow layouts checked by screenshot. **§8b, the plan rail, the folder tree, the
     grouped table and plan Duplicate, was built on 2026-11-05** (migration `d5b7f9a1c3e6`,
     `comfy_plans.declared_folders`), after the user ruled all four open questions by
     approving the recommendations (the plan's § The open questions, ruled). An Opus review's
     findings were fixed, among them a case-blind `LIKE` that let a folder delete take
     another folder's rows. Next is §8c. **Its first action is the split of
     `docs/comfyui.md`** queued in [pending-splits.md](pending-splits.md): §8b left it at
     2,498 of 2,500 words, and §8c rewrites the two sections that move.
   - Still owed by hand for §8b, against a real ComfyUI: the rail's dot turning blue during a
     run, and a folder rename refused while one is live.
   - **Found in the §8b review, not fixed there:** the gallery's
     `dataset_service.repath_subfolder` matches a folder's subtree with SQLite's case-blind
     `LIKE`, so renaming `knight` also re-paths `Knight/a`; `filesystem.py`'s
     `startswith(autoescape=True)` has the same shape. A fix for `main`, not this branch.
   - Decisions 7–9 and this M6 checklist exist only on that branch until it merges, so
     `main`'s next decision takes number 10. Its migrations `e5c1a9d3f7b2` →
     `b7d2f4a9c1e3` → `d4a8c2e6f1b9` → `f3b9d1a7c5e2` → `c8e4a6b2d0f7` → `a6d2f8c4e0b3` →
     `b2e7c9a4d1f6` → `c4a8e2f6b9d3` → `d5b7f9a1c3e6` follow `main`'s `c2b8e4f6a1d7`;
     rebase at stage boundaries.
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

- [x] Port per-row output folders from `comfy-per-row-subfolders`: c2d6a6d. Ported 2026-11-03 as
  8dee5de, with the migration re-parented onto `c2b8e4f6a1d7` and the plan moved into its own
  folder and rewritten. The old branch is deleted.
- [x] Split `docs/dev/comfyui.md` along its two recorded seams. Done 2026-11-03: the run body
  moved to `docs/dev/comfyui/run.md` and the page to `docs/dev/comfyui/page.md`, each with a Map row.
- [x] §7: the mock ComfyUI server. Done 2026-11-03: `backend/tests/fake_comfy.py`, in-process
  for pytest (`test_comfy_run_body.py`) and on port 8198 for Playwright (`comfy-run.spec.ts`).
  Every later stage tests against it.
- [x] §2a: blueprints, leaves, the pure expansion service and its guard rails, and prompts
  composed from templates. Done 2026-11-03: migration `b7d2f4a9c1e3` (which also carries §2b's
  `brief`/`draft` columns), the *Blueprint* tab, and `docs/dev/comfyui/structured.md`.
- [x] §2b: the outline call and the review panel, reachable without an LLM. Done 2026-11-03:
  the `comfy_outline` job saves a draft for *Use it* / *Discard*.
- [x] §2c: Top up. The plan's cut point if the arc stops early. Done 2026-11-03: Apply, reset
  failed rows, run pending ones, in one press.
- [x] Split comfy-structured.md before §3 grows it. Done 2026-11-03, re-cut three ways, with
  the ComfyUI family moved behind the hub `docs/dev/comfyui.md`.
- [x] §3: the optional LLM rewrite pass, and the outline's instruction-order fix. Done
  2026-11-03: the `comfy_rewrite` job, migration `d4a8c2e6f1b9` (`comfy_rows.prompt_origin`),
  Top up's refusal while rows wait, and `docs/dev/comfyui/structured-llm.md` § The rewrite
  pass. Verified by hand against a real LLM in part (2026-11-04, `qwen3.8-27b` in LM
  Studio): a draft and a rewrite both worked, and the draft review, the label wording and
  the rewrite count's refresh came out of that test. Stop mid-run and a hand-edited row
  are not yet checked.
- [x] Rebase onto `main` once M8 stages 2 and 3 have landed there. Needed by §4. Done
  2026-11-04, after PR #109.
- [x] §4: the stepping run (`Run` row, `advance_run()`), its page and the VRAM handoff. Done
  2026-11-04: start functions lifted out of the score, caption and export routes and Top
  up, migration `f3b9d1a7c5e2` (`runs`), `run_service.py`, the worker's `_after_terminal`
  hook, the Runs page and `docs/runs.md`. The user moved the gate to §5 and the snapshot
  stage to §6 that day. Full runs checked by hand against a real ComfyUI and LM Studio the
  same day; Pause, a restart mid-run and a `TopBar` cancel are not.
- [x] §5: the goal loop, the gate and budgets. Done 2026-11-04: the body/tail split and
  `next_pass`, the gate (`run_gate.py`, `rejects/`, `gate_rejected`), the rewrite stage,
  the caps and the paid-call cap, the per-provider `billed` setting (migration
  `c8e4a6b2d0f7`), the kept rule applied everywhere, and `docs/dev/comfyui/run-loop.md`.
  A two-pass loop and a capped run are not yet checked by hand.
- [x] §6: the dry run, the snapshot stage and the recipe library. Done 2026-11-04 with
  every gate green: `plan_run` behind both the dry run and the
  start, the head `snapshot` stage and its waiting cancel (migration `a6d2f8c4e0b3`), the
  `run_recipes` library (`b2e7c9a4d1f6`) and the remembered form (`c4a8e2f6b9d3`),
  `extend_run`, and `docs/dev/comfyui/recipes.md`. Not yet checked by hand against a real
  ComfyUI and LM Studio.
- [x] Review-fix pass. Done 2026-11-04: a branch review (four code reviewers and two doc
  audits) and its fixes, each with a regression test. Two rulings by the user: a run fails
  when a generate or caption job failed every item (`docs/dev/comfyui/automation.md`), and
  `gate_rejected` is cleared when an image leaves `rejects/` (`services/gate_flags.py`,
  `docs/dev/comfyui/run-gate.md` § The gate). The rest: the gate honours a cancel and needs
  no plan, a second Cancel finishes a stuck snapshot wait, a stale job cannot step a resumed
  stage, an empty pass stops the loop, `kept` counts completed rows only, Top up's error
  after a committed fill says so, id lists in the score and caption starts are chunked,
  recipe import refuses an ambiguous provider, and blueprint and export inputs are bounded.
  The M6 docs were audited against the final code.
- §8, the ComfyUI page rework, so prompts are easier to categorize and the prompt library
  easier to use (the user's call, 2026-11-04: it lands before the merge). Scoped with the
  user on 2026-11-05 and the mockup approved; the *what* is
  [comfy-automation-plan/README.md § 8](comfy-automation-plan/README.md#8-the-comfyui-page-rework-planned).
  Its open questions were ruled by the user on 2026-11-05.
  - [x] §8a: the sticky header and run bar, Blueprint in two columns with the preview
    sticky, collapsible editor sections. Done 2026-11-05, frontend only: the page is its own
    bounded scroller, a container query stacks the columns in a narrow pane, and the
    preview's sample prompts open per folder (the user's call). See
    `docs/dev/comfyui/page.md` § The layout and `docs/dev/comfyui/blueprint-tab.md` § The layout.
  - [x] §8b: the plan rail, the folder tree and rows grouped by folder, plan duplicate,
    the remembered plan and tab. Done 2026-11-05: migration `d5b7f9a1c3e6`
    (`comfy_plans.declared_folders`), the folder and duplicate endpoints, the `linked` mode
    on folder moves, and `docs/dev/comfyui/page.md` § The plan rail, the folder tree and the
    grouped table. `page.md` was split first, as queued: the Blueprint tab is now
    `docs/dev/comfyui/blueprint-tab.md`. Not yet checked by hand against a real ComfyUI.
  - [ ] §8c: the library side panel with nested categories, and drag and drop with a
    button route for every drag. Question 3 is ruled; split `docs/comfyui.md` first.
  - [ ] §8d: the Add menu, the selection bar, the paste dialog on `ModalShell`.
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
