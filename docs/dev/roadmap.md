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

## Next session (updated 2026-11-15, #130 designed)

1. **#130 Compare is designed and being built**, four phases, one PR each (see Now). The
   plan is [compare-plan/README.md](compare-plan/README.md), the rulings
   [decision 20](decisions/20-compare-view.md), and a clickable mockup is linked from the
   owner's notes. Pick up at the first open phase box. M11 otherwise holds #48, waiting
   on its reporter, and #129, design first.
2. **CLAUDE.md is 2 words over budget.** Its seam, two Map hubs, waits on the user's call
   between a hub and a budget raise (`docs/dev/pending-splits.md`).
3. M4 stays held, with its merge checklist under Waiting. The closed items of the
   previous blocks are in
   [roadmap-archive/handoff-2026-11-15.md](roadmap-archive/handoff-2026-11-15.md) and
   [roadmap-archive/handoff-2026-11-14.md](roadmap-archive/handoff-2026-11-14.md).

## Now

### M11 — Open enhancement issues

Ordered first by the user on 2026-11-13, ahead of M7's Stage 3 (now under Waiting). It holds
every open GitHub issue labelled `enhancement` except #90, #9 and #16, which stay parked
under Later.
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

- [x] #19 Light mode: merged 2026-11-14 as PR #162, which closes the issue. The user picked
  "Studio grey" from five drafts that day, with a Dark / Light / Match system choice in
  Settings → UI Behavior, dark the default. A second token block under
  `:root[data-theme="light"]`, set before first paint, plus `--accent-text` and
  `--on-accent`; about 60 files swept onto tokens (`docs/dev/styling.md` § Two themes).

Tier 3, captioning helpers, one batch: three PRs in this order, by the user's choice of
2026-11-14. Each helper sits in the detail page's caption panel and changes only the
unsaved draft.

- [x] #45 Other images' captions: merged 2026-11-14 as PR #163, which closes the issue. An
  *Other captions* section in the detail page's caption panel, ranked by shared tags,
  detected objects, the stored CLIP vector or recency, inside the current dataset only (the
  user's ruling of 2026-11-14) (`docs/dev/caption-editor.md` § Other captions panel).
- [x] #50 Check for a better tag: merged 2026-11-14 as PR #164, which closes the issue.
  A **Check tags on Danbooru** button in the caption panel, press-only, through a new
  `POST /booru/check`. It flags aliases, rare tags (under 100 posts with a close tag holding
  at least 100 times more, the user's rule of 2026-11-14) and unknown ones, and an outage
  is a 502 rather than every tag "not found". Danbooru only, by the user's choice. The same
  PR moves the Booru page's Safebooru search to danbooru.donmai.us with a User-Agent, after
  safebooru.donmai.us answered every API request with a 403 (`docs/dev/caption-editor.md`
  § Check tags).
- [x] #52 Synonyms: merged 2026-11-15 as PR #165, which closes the issue. An offline
  WordNet lookup (`docs/dev/dictionary.md`), a Settings toggle, and a card under the
  caption box (`docs/dev/caption-editor.md` § Synonym card), the user's choice over a
  popover at the word, 2026-11-14. The install runs outside the job queue and irregular
  forms come from WordNet's own lists (decision 18). Licence checked
  2026-11-14: Open English WordNet 2025, core edition, which is CC BY 4.0 on top of the
  Princeton WordNet licence. Both allow any use. Credit both projects, keep Princeton's
  notice and disclaimer with every copy, note our conversion, and never use Princeton's
  name in advertising. The app downloads the official JSON zip (about 10 MB) with a pinned
  hash and converts it locally, so the repo carries none of the data. Skip the "2025+"
  edition, whose proper nouns come from Wikidata.

Tier 4, design first:

- #130 Better comparisons: a Compare page, designed 2026-11-15 in
  [compare-plan/README.md](compare-plan/README.md) under
  [decision 20](decisions/20-compare-view.md). One branch and PR per phase, in this order:
  - [x] Design: the plan, decision 20 and these boxes (`design/130-compare`, PR #168).
  - [x] Phase 1, the page and Side by side (`feat/130-compare-page`): `POST /images/by-ids`,
    the routed page, linked zoom, the selection toolbar's Compare button, opening in the
    neighbouring pane. Merged 2026-11-15 as PR #169, with an Opus review's seven findings
    fixed (`docs/dev/gallery/compare.md`).
  - [x] Phase 2, A/B (`feat/130-compare-ab`): swipe, flip and difference. Merged
    2026-11-15 as PR #170.
  - [ ] Phase 3, Grid and the contact sheet (`feat/130-contact-sheet`):
    `POST /images/contact-sheet`, timed on 100 large files before shipping.
  - [ ] Phase 4, actions (`feat/130-compare-actions`): the Quality page's Compare button,
    Keep this one, delete and labels. Then close the issue and delete the plan folder.
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
  have copied their data over. **Unblocked**: #101 shipped in `Version-1.2.2`, and
  `Version-1.3.0` followed. Its release notes say "update to 1.3 or later first"
  ([decision 19](decisions/19-release-versioning.md)). Record:
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
- Four routes still widen `image_ids: []` to the whole dataset: `/captioning/run` and
  `/captioning/pipeline` (`if body.image_ids:`), the quality scoring start
  (`backend/routers/quality.py`) and `caption_service.find_replace_captions`. Their
  docstrings warn of it and `backend/tests/test_caption_start_scope.py` pins it, so each
  caller's own frontend is the only guard; `services/image_scope.py`, detection, inpaint,
  upscale and LUT read `[]` as nothing. The user chose on 2026-11-14 to fix only the
  Captioning page's empty *Selected* scope (PR #161) and leave the routes as they are.
- No check stops a new fixed Tailwind palette class (`text-gray-500`, `border-gray-700`…) or
  a hard-coded UI colour from coming back, and each one is wrong in light mode. A grep-based
  guard in CI would hold the line (`docs/dev/styling.md` § Two themes). Found in the
  light-mode change, 2026-11-14.
- `bg-accent/10`, the tint on a selected model row in `ImageDetailPage` and
  `SelectionToolbar` (ten uses), generates no CSS: Tailwind 3 cannot apply an opacity
  modifier to a colour defined as a bare `var()`. It was already the case on `main`; the fix
  is `color-mix` or an `<alpha-value>` colour definition. Found 2026-11-14.
- Duplicates across datasets (issue #9), taken out of M11 and parked by the user on
  2026-11-14. The shape found when it was reviewed: a read-only report job. The `phash` scan
  (`find_duplicates_sync` in `backend/ml/technical_scorer.py`) already scales; the duplicate
  flag stays per dataset and the report writes none (CLAUDE.md § Key invariants, PM-022).
- Keyboard navigation (issue #16), taken out of M11 and parked by the user on 2026-11-14
  after a scope estimate for the whole app. Today there is almost no focus ring (one
  `:focus-visible` rule), about 46 clickable `div`s in 13 files that Tab cannot reach
  (gallery cards hold buttons, so they need `tabIndex` and a role, not `<button>`), and about
  15 window `keydown` listeners whose text-field guards are spelled four ways (one helper
  exists, `frontend/src/utils/keyboard.ts`). The phases proposed: a focus ring and one shared
  guard; the file browser (arrows, Enter, Backspace); the clickable `div`s; gallery grid
  arrows, the risky one (clashes with the detail page's arrows, the crop nudge,
  `VideoStrip`, `ComfyPage`'s capture listener, and every key needing the active-pane check);
  page shortcuts and a `?` help screen. Each phase adds Playwright key tests, since none
  exist.
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
- **Handoff notes, 14 November 2026**: the pipeline scope bug's Next session items and
  Later line, closed by its fix, PR #160
  ([PM-038](postmortems/PM-038-pipeline-scope-field-nothing-read.md)), and the
  empty-selection bug's, closed by PR #161.
  Record: [roadmap-archive/handoff-2026-11-14.md](roadmap-archive/handoff-2026-11-14.md).
- **Handoff notes, 15 November 2026**: #52's Next session item, closed when it merged as
  PR #165. Record: [roadmap-archive/handoff-2026-11-15.md](roadmap-archive/handoff-2026-11-15.md).
- **Release versioning and v1.4.0** (2026-11-15): three-number semantic versions tagged
  `vX.Y.Z` ([decision 19](decisions/19-release-versioning.md)), the version in
  `backend/version.py` and shown in Settings, and the `release` skill
  (`.claude/skills/release/SKILL.md`). v1.4.0 carries M11 Tier 1 to 3 and the fixes since
  `Version-1.3.0`.
