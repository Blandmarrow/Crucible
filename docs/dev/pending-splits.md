# Pending documentation splits

A handoff queue, not a topic file — it has no Documentation Map row and nothing reads it
to learn about a subsystem. One entry per doc that needs restructuring, naming the seam.

It exists because the two halves of a split want opposite conditions. **Choosing** the
seam is best done by whoever just worked the file and can see which section stopped
belonging — knowledge that evaporates when the session ends. **Executing** the split is
an exhaustive sweep (move sections, update every inbound reference, fix `§` pointers, add
a Map row) that wants a fresh context and goes wrong in a long one. So the seam is
recorded here at the end of the session that trips the budget, and executed at the start
of the next session that would append to that file.

Nothing here is ever a reason to compress prose to get back under budget. See the
`doc-maintenance` skill, § Splitting an oversized doc.

## How entries work

The heading is the file's repo-relative path, exactly as `scripts/check_docs.py` prints
it — the checker matches on it in both directions.

```markdown
## docs/dev/example.md

- **Moves:** § Section A, § Section B
- **New file:** docs/dev/example-detail.md (mirrors docs/example.md)
- **Why here:** the two sections are about the router; the rest is the service layer
- **Watch for:** § Section A is referenced from docs/dev/other.md
```

`Watch for` is the field worth spending time on — it carries what the next session cannot
cheaply rediscover. The checker catches broken paths and anchors after the fact; it
cannot tell you which seam was the right one.

**Write a proposed new filename as plain text, never in `backticks`.** The file does not
exist yet, and an inline-code path that does not resolve is a check FAIL — an entry that
breaks CI is worse than no entry.

Three bands govern an entry's lifetime. A file **over budget** with no entry is a WARN
(record one). A file with an entry that has since dropped **under 60%** of budget is a
WARN the other way (the split evidently happened — delete the entry). Between the two is
the working band: entry recorded, split pending, no warning. A file that is a dumping
ground while sitting *below* that 60% floor — so it would read as already-split — takes a
`## docs/dev/example.md (structural)` heading, whose trailing marker exempts it from the
staleness sweep while still recording the seam.

## Queue

## CLAUDE.md

- **Moves:** the § Key invariants bullets that are *about one subsystem each* — the two
  video bullets (the poster/`video_count` one and the Windows open-file one), the
  `record_in_place`/`scores_stale` bullet, and the stem-keyed-derived-artifact bullet
- **New file:** none — they move **into the topic files that already own them**
  (`docs/dev/video.md`, `docs/dev/video-endpoints.md`, `docs/dev/scores-stale.md`,
  `docs/dev/image-files.md`), each leaving a one-line pointer behind in CLAUDE.md
- **Why here:** § Architecture is 2,905 words and § Key invariants is nearly all of it,
  which is a symptom rather than the problem: the section's stated test is "would I want
  this loaded even for a task in an unrelated subsystem?", and four of those bullets fail
  it outright. Each has grown a full sub-bullet tree of its own that only a reader already
  inside that subsystem can use, and each has a topic file that is the natural home. That
  is a *misfiling* fix, not a size fix, and it is why the seam is not "split CLAUDE.md in
  half" — a second always-loaded file would defeat the point of the split. The three
  bullets that must stay are the ones every module genuinely touches: the path-traversal
  guard, the client-supplied-regex rule, and the fallible-work-before-commit ordering.
- **Watch for:** every moved bullet needs its one-line pointer to name the destination
  file, or the fact becomes unfindable — the invariants list is the only index of these
  rules that a fresh conversation sees. The `record_in_place` bullet is cited by
  `docs/dev/postmortems.md` (PM-010) and by the AST guard's docstring in
  `backend/tests/test_scores_stale.py`; the stem-collision bullet is cited from
  `docs/dev/video-reextract.md` § The extension change. The Documentation Map is the other
  1,808 words and is **not** a split candidate: it is the routing table that makes the
  on-demand split work at all, and it grows by one row per topic file by design.

## docs/dev/backend-infrastructure.md

- **Moves:** § Database (1,142 words — the largest section by a factor of two), plus its
  two neighbours that are really about the same thing, § Startup database backup and
  § Migration drift check
- **New file:** docs/dev/database.md
- **Why here:** the file is two subjects wearing one title. One is the *schema and the
  SQLite engine* — per-connection pragmas, FK enforcement and every `ondelete`, the index
  inventories, deferred blob columns, the backup rotation, the drift check. The other is
  the *running process* — the lifespan hook, production frontend serving, the server
  control endpoints and the restart loop, SSE, job cancellation, the retention sweep, and
  the open `JobQueue.stop()` hang. A reader arrives for exactly one of the two and there
  is no overlap between them. Splitting there leaves ~2,130 and ~1,720 words, both close
  to the 60% target (2,100), which is why the seam is here and not between § Database and
  everything else. The process half has since grown by § App-level exception handlers, so
  it is the one with no headroom left — if a third subject appears, re-check the seam
  before assuming this one still holds.
- **Watch for:** the § Database prose is cited from several directions and every one of
  these is a `§`-level pointer the checker cannot verify — `docs/dev/video.md` and
  `docs/dev/video-extract.md` (the frame-lineage FK and `ix_images_source_video_id`),
  `docs/dev/versioning.md` (the versioning tables' cascades and the two decorative
  model-level FKs), `docs/dev/provenance.md` (the dropped `ix_images_dataset_license`),
  and CLAUDE.md's Documentation Map row and § Tests line. The migration-drift paragraph
  is also referenced from `scripts/check_migrations.py`'s own module docstring. The new
  file mirrors no user doc, so the naming convention gives no name for free — `database.md`
  is a proposal, not a constraint.

## docs/dev/gallery.md

- **Moves:** § Gallery subfolder sidebar (544 words), § The subfolder row context menu (190)
  and § Renaming and moving a subfolder (1,168)
- **New file:** docs/dev/gallery-subfolders.md (mirrors docs/gallery.md § Organising into
  subfolders)
- **Why here:** the file tripped its budget again the same day `gallery-dnd.md` was split
  out of it, because subfolder *management* arrived — rename, re-nest, the row context
  menu, the re-path endpoint and its five pieces of path-keyed bookkeeping. Those three
  sections are one subsystem and a reader arrives for exactly it: the sidebar is the UI,
  the menu is how the two new operations are reached, and the re-path section is the
  endpoint plus the client state it invalidates. What stays is *reading* a gallery —
  selection and the shift-click range model, the filter bar, sorting. The seam leaves
  ~1,900 and ~2,430 out of 4,335; the staying half is over the ~2,100 target because
  § Gallery image selection is 1,263 words on its own, and its recorded second seam (the
  select-all affordance + count key + pagination arithmetic, versus the shift-click range
  model) is the one to take if it needs a third file.
- **Watch for:** § Renaming and moving a subfolder is cited from `docs/dev/gallery-dnd.md`
  § Dragging a subfolder onto another and § Manual image ordering (both in the file that
  just split off, so they become cross-file pointers a second time), and § The subfolder
  row context menu from `docs/dev/styling.md` § Context menu and `docs/dev/file-browser.md`.
  `docs/dev/datasets-page.md` points at the subfolder sidebar — that moves now, so this is
  the one inbound reference that has to change direction rather than stay put. § Gallery
  filters and § Gallery image selection are cited from `docs/dev/image-filters.md` and
  stay behind. The sidebar section's own bullets cross-reference `docs/dev/gallery-dnd.md`
  in both directions.

## docs/dev/file-browser.md

- **Moves:** § `POST /move` (1,064 words) and § `POST /rename` (1,021), plus § `POST /delete`
  (395), § Still open (148) and the two short sections that exist only to serve them,
  § Name collisions (55) and § DB sync (119)
- **New file:** docs/dev/file-browser-mutations.md
- **Why here:** the file is a read surface and a write surface bolted together. Everything
  moving is about *mutating the filesystem and keeping the DB in step* — the structural-folder
  refusals, the 409s, the orphan bookkeeping, the versioning hook, the collision rules. What
  stays is *browsing*: the eight endpoints' listing side, path safety, the three-panel page
  and its preview panel. A reader working on the preview never needs the move/rename
  semantics and vice versa. § Still open travels **with** the mutations rather than staying:
  it is a list of open holes in `/move`, `/rename` and `/delete` specifically, and it cites
  § `POST /move` by name. That lands ~2,800 moving and ~825 staying (§ Endpoints, § Path
  safety, § Frontend, intro) out of 3,652. The moving half is at 80% of budget rather than
  the 60% target, recorded honestly as the cost of the only coherent seam: this is one
  router, and two endpoint sections carry three-quarters of its words. A second seam between
  move/rename and delete does **not** help — § `POST /delete` is 395 words, so splitting it
  off leaves the move/rename file at ~2,400 and buys a third file for nothing.
- **Watch for:** § `POST /rename` carries a `### Directory branch` subsection that travels
  with it. § Frontend describes the page as a whole and stays, but its two PM-021 paragraphs
  (the `released` prop and the `<video>` unmount) are about the rename and delete mutations —
  decide with the file open whether they travel. § Path safety stays behind but is cited by CLAUDE.md § Key invariants and by
  `docs/dev/video-endpoints.md` § Serving bytes; § `POST /delete` is cited from
  `docs/dev/postmortems/PM-014-...` and `docs/dev/versioning-service.md`, and § `POST /move`
  from PM-011. Grep `file-browser.md` across `docs/` and `CLAUDE.md` — several of those are
  `§`-level pointers the checker cannot verify. The user doc it mirrors is
  `docs/workspace.md#file-browser`, not a docs/file-browser.md, so the naming convention
  gives no name for free.

## docs/dev/video-reextract.md

- **Moves:** § Target resolution — one function, two callers (873 words) and § The contract
  (444), i.e. the whole request-side half
- **New file:** docs/dev/video-reextract-endpoints.md
- **Why here:** the file is two subjects wearing one name. *Deciding which frames to
  re-extract* is `_resolve_reextract_targets` and the two endpoints that share it — pure
  request/response, no filesystem — and it is what a reader arrives for when they are
  changing the preview's accounting or a skip reason. *Rewriting a frame in place* is the
  job: `_rewrite`'s step order, the PM-013 window, the extension change and now the locked-
  file retry, all of which are about disk. The seam leaves ~1,300 and ~2,300; the second is
  over the ~2,100 target, so § The extension change (788) is the natural third piece if it
  needs one — it is about *naming and collisions* rather than about the rewrite order, and
  the material that pushed the file over budget landed in § The video_reextract job and
  § The extension change both.
- **Watch for:** the two halves cross-reference each other constantly — § The video_reextract
  job cites "§ What gets skipped, and why" and "§ The extension change" by name, and § The
  contract is what `docs/dev/video-reextract-ui.md` reads against. `docs/dev/video.md`'s
  § Where things live points `backend/routers/videos.py` and `backend/schemas/video.py` at
  this file for pass 2; both rows need splitting the same way. PM-021's write-up and
  CLAUDE.md § Key invariants now both link here for the `replace_retrying` site, which lives
  in the job half.

## docs/dev/ml-models.md

- **Moves:** § Upscaling (~700 words) and § LUT Color Grading (~430), i.e. everything below
  § ML model management
- **New file:** docs/dev/image-processing-models.md
- **Why here:** the file is a model *registry and loader* doc with two image-*processing*
  subsystems appended. § ML model management is about `model_manager.py` — the registry,
  eviction, VRAM accounting, per-loader quirks, the EXIF-open invariant, the device
  abstraction — and it is what a reader arrives for when adding or debugging a loader.
  Upscaling (spandrel) and LUT grading are pipelines that happen to load a model: their
  governing invariant is `image_service._open_safe`, not `open_rgb`, and their surfaces are
  `routers/upscaling.py` and `routers/lut.py`. A reader working on eviction never needs
  either. The seam leaves ~2,600 and ~1,150 out of 3,749 — the staying half is still over
  the ~2,100 target, and the natural second seam is § ML model management's own tail: the
  per-loader compatibility notes (Florence-2 PromptGen patches, JoyCaption inference
  details, `_TAG_STYLES`) are *quirks of individual models*, while the registry table, the
  VRAM budget and the device abstraction are the *mechanism*.
- **Watch for:** this file is the single most-cited dev doc — CLAUDE.md § Key invariants
  points at it for both the EXIF-open and close-PIL-images rules, and
  `docs/dev/scoring.md`, `docs/dev/detection-inference.md`, `docs/dev/captioning.md`,
  `docs/dev/tag-consolidation.md` and `docs/dev/environment-setup.md` all reference it. Nearly
  all of those want the *management* half, so they stay pointed at `ml-models.md` — check
  each rather than repointing by reflex. The upscaling prose is cited from
  `docs/dev/bulk-image-jobs.md` (crop-upscale) and `docs/dev/image-files.md`; those are the
  two that move. Grep `ml-models.md` across `docs/` before and after. The user-facing side of
  the moving half lives in `docs/editing.md`, not in per-feature pages, so the
  mirror-the-user-doc naming convention gives no name for free — image-processing-models.md
  is a proposal, not a constraint.

## docs/dev/image-similarity.md

- **Moves:** § Style similarity in full — its mode table, § What the modes are actually
  worth, § Making the score readable — the run descriptor and the percentile, and every
  subsection under it (2,437 words, more than half the file)
- **New file:** docs/dev/style-similarity.md (mirrors docs/scoring.md § Style similarity,
  whose own second seam names the same subject)
- **Why here:** the file has been two subsystems under one title since it was written, and
  its own `# Title` says so — "duplicates **and** style". They share nothing but the word
  *similarity*: duplicates are pHash Hamming distance, a grouping pass inside the technical
  scorer, and a destructive resolution UI; style is CLIP/DINOv2 cosines, a synchronous
  CPU-only endpoint, a run descriptor table, a percentile contract and three rendered
  meters. A reader arrives for exactly one. Splitting there leaves ~2,300 and ~2,440 — both
  over the ~2,100 target, which is the honest cost of a file that is two full subsystems
  rather than one that grew a tail. If the style half needs a second seam it is between the
  *scoring* (modes, gate findings, all-layers vectorization) and the *reading* (descriptor,
  endpoint, percentile, surfaces) — the § Making the score readable heading is already that
  line.
- **Watch for:** the duplicates half is cited from `docs/dev/scoring.md`,
  `docs/dev/bulk-ops.md` and `docs/dev/video.md`; the style half from
  `docs/dev/scores-stale.md` (§ The clear predicate, in both directions),
  `docs/dev/statistics.md` (the `"style"` cache slot), `docs/dev/gallery.md` (the card
  meter), `docs/dev/image-detail.md` (the Style match block and the layer breakdown) and
  `docs/dev/persistence.md` (the meter key). CLAUDE.md's Documentation Map row names both
  subjects and has to become two rows. `backend/scripts/style_gate_report.md` is the source
  the gate findings summarise and points back here by name. Several of these are `§`-level
  pointers the checker cannot verify — grep `image-similarity.md` across `docs/`,
  `CLAUDE.md` and `backend/scripts/` before and after. The user doc the new file mirrors is
  a *section* rather than a page today; if `docs/scoring.md`'s own recorded second seam runs
  first and produces a docs/style-similarity.md, the names line up for free.

## docs/dev/labels.md

- **Moves:** § Filters (384 words), § Export (241), § The mirror column (165) and
  § Versioning and cross-dataset hooks (631, which now also carries the same-dataset
  derivative table)
- **New file:** docs/dev/label-consumers.md
- **Why here:** the file describes a vocabulary and then, separately, every *other*
  subsystem that reads or carries an assignment. What stays is the thing itself — the two
  tables, `label_service.py`, the `/labels` router and its five validation rules, and the
  four frontend surfaces (Settings panel, detail panel, hotkeys, bulk modal); a reader
  arrives there to change the endpoint or the UI. What moves is where a label *shows up*:
  the `GET /images/` filter block, the export narrowing and its client encoder, the
  snapshot mirror and restore's Pass 2c, cross-dataset copy/duplicate/move, and the five
  same-dataset derivative sites. That reader is working on export or versioning and wants
  none of the CRUD. The seam leaves ~2,355 and ~1,421 out of 3,776 — the staying half is
  over the ~2,100 target, and its own second seam is § Frontend (638), which would become
  docs/dev/labels-ui.md and mirrors nothing in `docs/`. The file tripped its budget when
  the review fixes landed: the shared filter validator, the derivative table, the assign
  guard and the render-time bounds check all arrived at once.
- **Watch for:** § Frontend is cited **by name** from `docs/dev/gallery.md` § Gallery
  filters ("six edits, see `docs/dev/labels.md` § Frontend") and stays behind — that is
  the one pointer that keeps working only if § Frontend does not move in a later pass.
  Everything moving has an inbound pointer of its own: `docs/dev/image-filters.md` (the
  three label rows), `docs/dev/export.md`, `docs/dev/versioning-service.md` (a full
  paragraph restating the mirror and Pass 2c), and CLAUDE.md § Key invariants' join-table
  bullet, which names this file for the mirror rules specifically. `docs/dev/settings.md`,
  `docs/dev/persistence.md` and `docs/dev/image-detail.md` point at the halves that stay.
  The user doc is `docs/labels.md` and covers both halves, so the mirror-the-user-doc
  convention gives no name for free — label-consumers.md is a proposal, not a constraint.
  Grep `labels.md` across `docs/` and `CLAUDE.md` before and after, and note that
  `docs/labels.md` matches the same grep.

## docs/dev/image-detail.md

- **Moves:** the *persistence* half of § Gallery persistence & detail-view navigation —
  the two-key table, the debounced-input rule and its restore-side corollary, the main
  save effect and the unmount flush, and the Reset filters paragraph (~1,300 of that
  section's 3,112 words)
- **New file:** docs/dev/gallery-state.md
- **Why here:** the section is 84% of the file and is two subsystems sharing a heading.
  One is *what GalleryPage remembers* — the `gallery-state-*` blob's fields, the six edits
  a new one costs, the mount-debounce rule, Reset filters; a reader arrives there while
  adding a filter and never opens the detail view. The other is *how the arrows walk* —
  `gallery-nav-*`, `navPageParams`, the boundary prefetch, `injectNavId`, `atEnd`, and the
  post-delete re-derivation; that reader is in `ImageDetailPage` and does not care what
  localStorage holds. The file tripped its budget when `search`/`detectionLabel`/
  `scoreFilters` were persisted, which added the seeding rule and a longer blob row to the
  first half only. The seam leaves ~1,300 and ~2,390 out of 3,693; the staying half is
  over the ~2,100 target, and its own second seam is the crop tool + caption panel +
  generation metadata sections, which are unrelated to navigation entirely.
- **Watch for:** the persistence half is cited by name from `docs/dev/persistence.md`
  § Three persistence shapes, `docs/dev/labels.md` § Frontend, `docs/dev/gallery.md`
  § Gallery filters (three bullets, all pointing at the seeding rule) and
  `docs/dev/settings.md` § Gallery defaults; PM-012's Fix section names the file too. The
  nav half is cited from `docs/dev/video-ui.md` and `docs/dev/image-filters.md`. Both
  halves reference `frontend/e2e/gallery-restore.spec.ts` and `gallery-nav-filters.spec.ts`
  respectively, which is a clean split of the tests as well. The two keys are described in
  one table, so it has to be cut in two rather than moved whole — and the *user* doc for
  either half is `docs/gallery.md`, itself queued below, so the mirror-the-user-doc naming
  convention gives nothing here.

## docs/gallery.md

- **Moves:** § Datasets (436 words) and § Getting images in (696)
- **New file:** docs/datasets.md (mirrors docs/dev/datasets-page.md)
- **Why here:** the page is two user tasks under one title, and its own name says so —
  "Datasets **&** Gallery". One is *acquiring*: making a dataset, categories, duplicating,
  uploading, importing a folder, rescan, caption import, versioning-mode. The other is
  *curating* what is already in one: browsing and filtering the grid, subfolders and now
  renaming/re-nesting them, manual ordering, sorting. A reader arrives for exactly one,
  and the seam leaves ~1,130 and ~1,400 out of 2,580 — both under the ~1,500 target, which
  is rare enough to take. It tripped the budget when § Organising into subfolders gained
  rename, move and the no-filename-renames note.
- **Watch for:** this is a user doc, so the inbound links are markdown links — README has
  five (three of them pointing at the *acquiring* half: Organize, Import, Sync) plus its
  Docs-table row, and a new file needs its own row there and in `docs/features.md`, which
  is an index and takes a row rather than prose. `docs/captioning.md`, `docs/video.md`,
  `docs/workspace.md` (a `gallery.md#getting-images-in` **anchor**, which moves) and
  `docs/duplicates.md` all link here too — check each for whether it wants the half that
  moves. README's `gallery.md#manual-image-ordering` and
  `gallery.md#renaming-and-re-nesting-a-subfolder` anchors both stay behind.

## docs/video.md

- **Moves:** § Extracting frames (706 words), § While it runs, and afterwards (531) and
  § Re-extracting at full resolution (503)
- **New file:** docs/video-extraction.md
- **Why here:** two user tasks share one page. *Holding videos* — adding them, the strip,
  the player, rename and delete, what plays in a browser and what does not — is what a user
  does before they have decided to extract anything, and § Browsing them grew again when
  strip delete and the unplayable message landed. *Turning a video into frames* is the
  two-step dialog, the progress rows, and pass 2, which a user reads once they are committed.
  The seam leaves ~1,000 and ~1,740; the second is over the ~1,500 target, so consider moving
  § Re-extracting at full resolution to its own file or leaving it behind with § Browsing them
  — it is the one section that is about *curated frames* rather than about the dialog.
- **Watch for:** this is a user doc, so the inbound links are markdown links from
  `README.md` (three of them, plus the Docs table row), `docs/gallery.md` (five), and
  `docs/features.md`'s index row — a new file needs its own README Docs-table row and a
  `docs/features.md` row, not prose. Several of those links point at the *page*, not a
  section, so they keep working; the ones to check are any `video.md#...` anchors. The dev
  docs mirroring this page are `docs/dev/video-extract.md` and `docs/dev/video-extract-ui.md`,
  which is where the mirror-the-user-doc naming convention points.

`docs/scoring.md` → `docs/duplicates.md` was executed on 2026-08-02, at the start of the
session that appended the style-similarity percentile material to § Style similarity — the
seam held as recorded, and the staying half landed at ~2,100 words. That half is still over
the 2,500 user budget's 60% target, so its recorded second seam stands: § Style similarity
is now the largest section and is a separate CPU-only workflow with its own concepts.

`docs/dev/gallery.md` → `docs/dev/gallery-dnd.md` was executed on 2026-08-03, at the start of
the session that added subfolder rename and re-nesting — the seam held as recorded, and
both § Manual image ordering and § Drag images onto subfolders moved together. The staying
half landed at ~2,900 words before that session's own additions, above the ~2,100 target
rather than the ~2,540 the entry predicted, because § Gallery image selection kept growing;
its recorded second seam therefore still stands.

The six entries previously recorded here were executed on 2026-07-31: `bulk-ops.md` →
`bulk-image-jobs.md`, `versioning.md` → `versioning-service.md`, `export.md` →
`export-licensing.md`, `detection.md` → `detection-inference.md`, `statistics.md`'s
misfiled `GET /images/` filter table → `image-filters.md`, and CLAUDE.md § Shared
utilities → `docs/dev/shared-utilities.md`.
