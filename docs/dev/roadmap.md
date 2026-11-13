# Roadmap

**Status: living.** The *sequencing* layer: the order work happens in and what blocks what.
It owns **order and dependencies only**. The *what* is in the linked files, the *why* in
[decisions.md](decisions.md) and the postmortems.

**Keeping this current:** when a task lands, the same session ticks it here and adds the
next one, the same way it runs the drift check. When a milestone completes, the next one
gets expanded into ordered tasks, and the finished milestone shrinks to a one-line "Landed"
entry linking its record in [roadmap-archive/](roadmap-archive/) (status
`archive (landed DATE)`). Archive eagerly, as soon as only open follow-ups remain; any open
box moves to Waiting or Later here.

**Detail is tiered:** the milestone under Now is checkbox-level; later ones are one-line
destinations until the work before them teaches us their shape.

A feature with its own design plan keeps that plan in a transient folder (`TRANSIENT_DIRS`
in `scripts/check_docs.py`); this file names the plan and its place in the order, and never
restates its stages.

**The sections are fixed, in this order:**

1. **Next session**: dated, **at most five items and about 250 words**, only what the next
   session needs to start. Each session *replaces* the block rather than adding to it. A
   closed item moves word for word to a handoff record in `roadmap-archive/`; an open one
   moves to the section below that fits it.
2. **Now**: the one milestone being worked on.
3. **Waiting**: work blocked on the user's decision or on something outside the repo. Each
   entry says what unblocks it.
4. **Later**: open findings and parked designs with no milestone yet, one line each,
   linking the record that holds the full text.
5. **Landed**: one line per finished milestone or handoff record.

## Next session (updated 2026-11-14, pipeline scope fix built)

1. **The pipeline scope fix is built** on `fix/pipeline-uncaptioned-scope`
   ([PM-038](postmortems/PM-038-pipeline-scope-field-nothing-read.md)): open its PR and
   merge.
2. **A second bug, found by that fix's review**, under Later: *Selected* with nothing
   selected recaptions the whole dataset. Same class of harm as PM-038; it wants the
   user's call on whether it goes before Tier 2.
3. **M11 goes on with Tier 2** (see Now): #9, #16, then #19. #48 waits on a question to its
   reporter, and #19 on the user picking a light palette. M7's Stage 3 waits behind M11,
   under Waiting.
4. M4 stays held, with its merge checklist under Waiting. The closed item of the previous
   block, the pipeline bug's triage, is in
   [roadmap-archive/handoff-2026-11-14.md](roadmap-archive/handoff-2026-11-14.md#next-session-updated-2026-11-13-105-merged).

## Now

### M11 — Open enhancement issues

Ordered first by the user on 2026-11-13, ahead of M7's Stage 3 (now under Waiting). It holds
every open GitHub issue labelled `enhancement` except #90, which stays parked under Later.
Each issue holds the *what*; a box adds only the code pointer found when the issues were
reviewed. Gate: each box ships with its user docs (`README.md` and the matching
`docs/*.md`), on its own branch unless it says otherwise. The two design-first items get a
plan folder and are expanded here before any code. The tiers are the order.

Tier 1, small:

- [x] #24 Remove captions: a `clear` operation for caption bulk edit, merged 2026-11-13 as
  PR #157, which closed the issue. The user chose to delete a cleared caption's
  `.txt`, and to make that the rule for every path that empties a caption
  (`docs/dev/image-import.md` § Caption sidecars).
- [x] #105 Re-caption with the existing caption: merged 2026-11-13 as PR #159, which closed
  the issue. `{previous_caption}` now works on every pipeline step and on `/run`, the
  detail page's AI panel has a Fix current mode, and the Captioning page's step 1 has an
  "Include current caption" toggle (`docs/dev/captioning.md` § The `{previous_caption}`
  placeholder).
- [ ] #48 Toggle thumbnails: ask the reporter what the problem is first. The likely fix is a
  thumbnail size setting (today `thumbnail_size = 256` in `backend/config.py`, set only by
  environment variable) plus the existing Rebuild thumbnails job. Full resolution in the
  gallery as an opt-in at most; thumbnails stay on, since other views depend on them.

Tier 2, medium:

- [ ] #9 Duplicates across datasets: a read-only report job. The `phash` scan
  (`find_duplicates_sync` in `backend/ml/technical_scorer.py`) already scales; the duplicate
  flag stays per dataset and the report writes none (CLAUDE.md § Key invariants, PM-022).
- [ ] #16 Keyboard navigation: the file browser first (arrows, Enter, Backspace). Only
  `ContextMenu` handles arrow keys today. The rest of the app is a separate box, added once
  this lands.
- [ ] #19 Light mode: a second set of the colour tokens on `:root` in
  `frontend/src/index.css`, a setting to switch, then the hard-coded colours moved onto
  tokens: about 160 hex or rgba values in `.tsx` files and about 200 fixed gray, white or
  black classes in 15 files, plus the shadows and the brand mark (`scripts/check_mark.py`).
  Waits on the user picking a light palette. The token conversion suits `delegate-to-loop`.

Tier 3, captioning helpers, one batch:

- [ ] #45 Other images' captions: a similar-images panel on the detail page, ranked by
  shared tags first and by the stored CLIP or DINO vectors later.
- [ ] #50 Check for a better tag: post counts and aliases through the booru routes
  (`/search` and `/autocomplete` in `backend/routers/booru.py`). Overlaps
  `TagConsolidatePage`.
- [ ] #52 Synonyms: an offline WordNet lookup, a Settings toggle, and a popover in the
  caption editor. Check WordNet's licence before adding it.

Tier 4, design first:

- [ ] #130 Better comparisons: a design pass. Candidates: zoom and pan kept in sync across 2
  to 4 images, a swipe or overlay A/B view, and a contact sheet export.
- [ ] #129 Chat with an LLM: a plan folder. Read-only tools first, writes behind a
  confirmation, and the paid-call cap applies; weak tool calling in local models is the main
  risk. Large enough to become its own milestone when it is expanded.

## Waiting

### M7 — Style embedding

Waits on M11, by the user's ordering of 2026-11-13; nothing else blocks it.
Gate: the layer and blend change merged with its docs, then the remaining stages expanded here
in their plan's order. Plan: [style-embedding-plan/README.md](style-embedding-plan/README.md).
Stage 2 there (DINOv3) is not built, by
[decision 6](decisions/06-layer-7-default-and-no-new-style-model.md). Does not wait for M4 ([decision 5](decisions/05-m7-goes-ahead-without-m4.md)).

- [x] Test DINOv3, Kaloscope 3.0, CSD and PE-Core (2026-11-02 to 2026-11-03, no app code):
  none adopted, and the layer 7 default stands
  ([decision 6](decisions/06-layer-7-default-and-no-new-style-model.md)). Results:
  `docs/dev/quality/style-evaluation.md` § Other models were measured and not adopted.
  Reopening a model needs a superseding entry, then an adoption plan: licence, its own
  embedding column and discriminator, residency.
- [x] Port from `style-embedding-measurement`: merged 2026-11-03 as PR #108.
- The full text of these boxes, with the run results and commit hashes, is in
  [roadmap-archive/handoff-2026-11-13.md § M7's done boxes](roadmap-archive/handoff-2026-11-13.md#m7s-done-boxes).
- [x] Stage 2b: stop the layer labels asserting things nobody measured
  (`DINO_LAYER_LABELS`). Merged 2026-11-08 as PR #119. Detail:
  [style-embedding-plan/README.md § Stage 2b](style-embedding-plan/README.md#stage-2b--stop-the-layer-labels-asserting-things-nobody-measured).
- [x] Plan and build the CLIP QuickGELU fix (2026-11-13, `fix/clip-quickgelu`,
  [decision 17](decisions/17-clip-quickgelu-fix.md)): one builder with QuickGELU, and a
  migration that clears old vectors and watermark results and re-labels old LAION scores.
  Measured shift: `docs/dev/quality/style-evaluation.md` § The CLIP QuickGELU fix.
- [x] Merge `fix/clip-quickgelu`: PR #154, 2026-11-13. The migration and the re-score flow
  were run end to end on a throwaway instance (decision 17 § How it's verified). After the
  merge, the real migration over seeded old-build rows was run on another throwaway
  instance, and the "LAION (old build — re-score)" label was seen in a headless browser on
  the Quality page's coverage line and the Stats page's Aesthetic tile, with no page errors.
- [ ] Stage 3: dense features for prompt-free subject masks. Untested; separate from style
  scoring.

### M4 — Rating feature (held)

Waits on the user's decision to ship it. Gate: the rating column and head phase 0 shipped
with their user docs, and the learned head either shipped or ruled out by phase 0's numbers.
Build order and stage detail:
[rating-plan/README.md § Sequencing](rating-plan/README.md#sequencing), which the port brings up
to date. Open rulings go in the decision log as they are made.

- [x] Token UI (§6) and the aesthetic model picker (§2), both shipped 2026-08-02.
- [x] The rating column (§1) and head phase 0 (the rating event log and metrics page), with rebindable rating keys and old snapshots
  leaving ratings alone. Ported 2026-11-02 on `feat/m4-rating-port` (67ac441, 9004f18,
  4233da1, ce0c788), every gate green. The user chose not to ship it yet, so PR #106 was
  closed; the source branch, `feat/scoring-arc`, is deleted.
- [ ] Merge `feat/m4-rating-port` once the user decides to ship the feature:
  1. **Until it is rebased past M9 stage 2, do not start the branch on a real datasets
     folder**: its startup sweep has no dot-skip and deletes `.orphaned` with everything
     parked there (`docs/dev/dataset-folders.md` § Two known costs).
  2. Rebase onto `main` and re-run the gates. Its migrations end at `e7c4a1b9d2f6`;
     re-parent its first, `c3b8e1d7a52f`, onto whatever `main`'s head is at the time.
  3. Resolve the overlap with M7, six files in all
     ([decision 5](decisions/05-m7-goes-ahead-without-m4.md)), among them
     `SelectionToolbar.tsx`, `queryKeys.ts` and `ImageDetailPage.tsx`; and M9's, listed in
     [scale-plan/README.md § Interaction with the other milestones](scale-plan/README.md#interaction-with-the-other-milestones).
  4. Check every path added to `main` since the branch was cut. A clean port only
     reconciles lines that clash, and a missed mirror or rebuild path fails silently.
     Since the CLIP fix, the unrebased branch builds CLIP the old way: never run it on a
     database migrated past `f3a8d1c6b9e2`, or it writes old-build vectors and `laion`
     scores again ([decision 17](decisions/17-clip-quickgelu-fix.md)).
  5. Port its doc changes by hand into `main`'s layout: decision 4 (rating keys and old
     snapshots, unaffected; `main`'s next new decision is 17), the rebuilt
     `rating-plan/README.md`, and its ticks onto this file's sections.
- [ ] The learned head (§3, phases 1+). Needs the port and re-ratings to measure. Not built if
  phase 0 shows an existing scorer already near its ceiling.
- DINOv3 (§5) stays deferred, the lowest priority. M7's plan supersedes §5, so it is settled
  there.

### Unblocked when M4 merges

M8's open items, each needing M4's rewrite of the files it touches. Detail:
[cleanup-plan/README.md](cleanup-plan/README.md); record:
[roadmap-archive/m8-structural-cleanup.md](roadmap-archive/m8-structural-cleanup.md).

- [ ] Item 9's remainder: chunk batch move and copy to another dataset.
- [ ] Follow-up 15: commit the row before the thumbnail in `images.py`'s single-image copy
  crop and `crop_upscale`'s new-file worker, as follow-up 14 did for the batch paths.
- [ ] Stage 5: `SelectionToolbar` dialogs into forms.
- [ ] Stage 6: the detail page's inline tools onto those forms.
- [ ] Stage 7: `StatsPage` into files.
- [ ] Stage 8: split `images.py` by section.

### M5 cleanup

- [ ] One release after PR #101: delete the legacy keys, the import code and the plan
  folder, [server-state-plan/](server-state-plan/README.md). Waits on a release so browsers
  have copied their data over. Record:
  [roadmap-archive/m5-app-stored-prompts.md](roadmap-archive/m5-app-stored-prompts.md).

### Upstream

- [ ] Remove the `minimatch` override in `frontend/package.json` (PR #111, for
  GHSA-g3hv-75wg-m249) once `eslint` and `typescript-eslint` depend on `minimatch ^11`
  themselves. Check with `npm view eslint dependencies.minimatch`.

## Later

Each line links the record that holds the full text.

- `test_gpu_executor_guard.py` cannot see a direct GPU call made on the event-loop thread,
  and wants a rule for direct calls
  ([m10-job-throughput.md § Left open](roadmap-archive/m10-job-throughput.md#left-open)).
- `/pipeline` refuses `save_backup` and `rename_on_caption`; wiring them up is parked, and
  how is in `CaptionPipelineRequest`'s docstring (`backend/routers/captioning.py`)
  ([m10-job-throughput.md § Left open](roadmap-archive/m10-job-throughput.md#left-open)).
- Not checked by hand: M9 stage 11's index and gallery changes in a browser, stage 4's
  polling against a real job, and the Stats page under the pin in a browser
  ([m9-scale-and-data-safety.md § Left open](roadmap-archive/m9-scale-and-data-safety.md#left-open)).
- Parked, each with a trigger, in [scale-plan/README.md](scale-plan/README.md): the side
  table ([decision 15](decisions/15-big-columns-stay-in-the-row.md)), stage 4's full design,
  the small-subfolder score sort, stage 11's cache header and style scoring's sort per page.
- Parked: M10's T6, a first-run weight download stalling the GPU thread
  ([throughput-plan/README.md § T6](throughput-plan/README.md#t6--fetch-model-weights-off-the-gpu-thread-parked)),
  and the plan's untimed
  [open questions](throughput-plan/README.md#open-questions-for-later).
- One docs seam is queued in [pending-splits.md](pending-splits.md), `README.md`'s
  § Features deletion.
- `model_manager._load_aesthetic_sync`'s first-run check looks for CLIP in
  `openai/clip-vit-large-patch14`, but open_clip loads it from
  `timm/vit_large_patch14_clip_224.openai`. Only the "Downloading…" progress message is
  affected. Found while planning the CLIP fix (2026-11-13).
- The detail page's crop box takes its frame from the browser's `naturalWidth`. An EXIF
  orientation tag in a PNG or WebP, which Pillow honours, is not confirmed to be applied
  by every browser; one that ignores it would crop the wrong region
  (`docs/dev/image-detail.md` § The crop box, PR #156).
- An empty selection on the Captioning page's *Selected* scope recaptions the whole
  dataset. The scope is remembered in the saved workflow, but the selection lives only in
  memory, so after a reload the page sends `image_ids: []`, which `/run` and `/pipeline`
  widen to every image (`if body.image_ids:`), with `overwrite` on. Run is not blocked at a
  count of 0. The likely fix is a run-blocked reason for `scope === "selected"` with no
  selection here; the detect run on the same page sends `[...selectedIds]` and wants the
  same check. Found by the review of PM-038's fix (2026-11-14).
- Face similarity (issue #90), researched 2026-11-13 and parked by the user. The pick is
  YuNet to find faces and SFace to compare them. Both are built into OpenCV, so there is no
  new dependency, and their weights are MIT and Apache-2.0. InsightFace was rejected because
  its weights are for non-commercial use only. In a trial on LFW, a standard set of face
  photos, SFace separated each of six people perfectly from 1,500 others. On this
  dataset's anime frames it could not tell the characters apart, though YuNet still found
  the faces. The plan follows style similarity's two steps: store the face vectors in a
  scoring job, then run a fast pass against reference faces.

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
- **M5 — App-stored prompts** (2026-10-30, PR #101): saved prompts moved from the browser to
  the database in five stages. Its cleanup waits a release, under Waiting. Record:
  [roadmap-archive/m5-app-stored-prompts.md](roadmap-archive/m5-app-stored-prompts.md).
- **M6 — ComfyUI per-row folders and automation** (2026-11-07, PR #110): per-row output
  folders, blueprints with the outline call, Top up and the rewrite pass, automated runs with
  the goal loop, gate, budgets, dry run, snapshot and recipes, the ComfyUI page rework, and two
  installs sharing a 16 GB card. Record:
  [roadmap-archive/m6-comfy-automation.md](roadmap-archive/m6-comfy-automation.md).
- **M8 — Structural cleanup** (2026-11-08, PRs #114 to #117 and #120): the four bugs and
  most of their follow-ups, a shared image-scope helper, a shared upscale and LUT job loop, the
  `DatasetsPage` dialogs and the coverage config. What is left waits on M4, under Waiting.
  Record:
  [roadmap-archive/m8-structural-cleanup.md](roadmap-archive/m8-structural-cleanup.md).
- **M9 — Scale and data safety** (2026-11-12, PRs #124 to #143): a unique tie-breaker for
  every gallery sort and the index it cost, an orphan sweep that moves folders aside, scoring
  that commits per chunk and can be retried, Stats lists fetched on demand, paged style
  similarity, the paid-call cap counting caption calls, the hot-path wins, three rounds of
  measurement up to 1M rows, and [decision 15](decisions/15-big-columns-stay-in-the-row.md):
  no side table yet, and the Stats scans pinned to their index with a plan guard. Record:
  [roadmap-archive/m9-scale-and-data-safety.md](roadmap-archive/m9-scale-and-data-safety.md).
- **M10 — Job throughput** (2026-11-12, PRs #145 to #149): the technical scorer on 4
  cores, concurrent remote caption requests
  ([decision 16](decisions/16-concurrent-caption-requests.md)), parallel import with one
  decode per file, the decode share timed on two cards, and the GPU checks preparing
  images ahead, 2.3× to 3.6× faster with identical scores. T6 parked. Record:
  [roadmap-archive/m10-job-throughput.md](roadmap-archive/m10-job-throughput.md).
- **Handoff notes, 2 to 9 November 2026**: the closed items of this file's Next session
  block (M6's loose ends, the M7 port, M8's follow-ups, PRs #131 and #132, Copy from plan),
  moved out on 2026-11-09. Record:
  [roadmap-archive/handoff-2026-11.md](roadmap-archive/handoff-2026-11.md).
- **Handoff notes, 10 to 13 November 2026**: the Next session block of 2026-11-12 and the
  "Porting the unmerged branches" section, moved out on 2026-11-13 when this file was given
  fixed sections. Record:
  [roadmap-archive/handoff-2026-11-13.md](roadmap-archive/handoff-2026-11-13.md).
- **Handoff notes, 14 November 2026**: the pipeline scope bug's Next session item and Later
  line, closed by its fix ([PM-038](postmortems/PM-038-pipeline-scope-field-nothing-read.md)).
  Record: [roadmap-archive/handoff-2026-11-14.md](roadmap-archive/handoff-2026-11-14.md).
