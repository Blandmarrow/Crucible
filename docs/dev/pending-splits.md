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
  `docs/dev/video/endpoints.md` § Serving bytes; § `POST /delete` is cited from
  `docs/dev/postmortems/PM-014-...` and `docs/dev/versioning-service.md`, and § `POST /move`
  from PM-011. Grep `file-browser.md` across `docs/` and `CLAUDE.md` — several of those are
  `§`-level pointers the checker cannot verify. The user doc it mirrors is
  `docs/workspace.md#file-browser`, not a docs/file-browser.md, so the naming convention
  gives no name for free.

## docs/dev/image-files.md

- **Moves:** § Importing captions & folder rescan (the three import paths, the rescan
  reconciliation and its rename-vs-adopt asymmetry), together with § A stem-keyed derived
  artifact needs a collision guard at every writer, which it explains
- **New file:** docs/dev/image-import.md (mirrors the import and sync half of the user-facing
  dataset docs)
- **Why here:** the file is three `###` sections and no `##`, and the last is most of its
  length: naming and cross-dataset moves are one subsystem, bringing files in from disk is
  another. Add `##` headings when next editing it so the per-section breakdown works
- **Watch for:** `docs/dev/video.md` § Poster stems and collisions, `docs/dev/file-browser.md`
  § Path safety and `docs/dev/video/reextract.md` cite the stem-collision section by heading;
  CLAUDE.md's stem-keyed pointer names it too

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
- **Watch for:** § Frontend is cited **by name** from `docs/dev/gallery/filters.md` § Gallery
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
- **Take both seams at once (noted 2026-10-29).** The file is now ~4,300 words, and the
  growth since this entry was written landed on the *staying* side: § Frontend is ~1,110
  words after the hotkey assign-queue paragraphs. The first seam alone would leave
  ~2,830, so move § Frontend to docs/dev/labels-ui.md in the same pass — that lands
  roughly 1,770 / 1,470 / 1,160. Doing so breaks the pointer the entry above calls out:
  `docs/dev/gallery/filters.md` and `docs/dev/persistence.md` both cite "§ Frontend" for the six
  persistence edits, so give that part its own heading in labels-ui.md and repoint both,
  and check the bullet at `docs/dev/gallery/filters.md` § Gallery filters against the 250-word
  paragraph limit afterwards, since a longer pointer can push it over.

## docs/scoring.md

- **Moves:** § Quality flags (162 words) and § Stale scores (320) — only if the file grows
  past budget again; it is under budget now
- **New file:** none chosen. A ~500-word page is thin, so the likelier home is a
  "Reading your scores" page if a third scoring-adjacent subject arrives to join them
- **Why here:** the § Style similarity seam was executed on 2026-10-29 into
  `docs/style-similarity.md`, leaving 1,940 of 2,500 words, over the ~1,500 target.
  Everything still here is one workflow end to end: running a scoring run, what each scorer
  produces and choosing the aesthetic model are about *producing* scores, and the two
  sections named above are about *reading* them — the flags those scores set, and when they
  go stale. That is the line if the file trips again.
- **Watch for:** `scoring.md#stale-scores` is linked from `docs/export.md` and
  `docs/style-similarity.md` (the dimmed meter), and `scoring.md#quality-flags` from
  `docs/captioning.md`; both are user-doc markdown links, so a move FAILs the checker
  until they are repointed. Do **not** take `### VRAM after a run` as the seam: it is six
  lines and belongs exactly where it is, under § Running a scoring run.

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
  docs mirroring this page are `docs/dev/video/extract.md` and `docs/dev/video/extract-ui.md`,
  which is where the mirror-the-user-doc naming convention points.

`docs/dev/gallery.md` → the Gallery hub and its `docs/dev/gallery/` folder was executed on
2026-10-29 as an M3 task, and was **re-cut on execution**. The recorded seam assumed 4,335
words and moved the three subfolder sections out; the file had reached 6,017, so taking only
that seam would have left ~3,100 behind. Every section moved instead: § Gallery image
selection to `docs/dev/gallery/selection.md`; the recorded seam's three sections, cut once
more at the UI/endpoint line because together they came to ~2,860, to
`docs/dev/gallery/subfolders.md` (the sidebar and its context menu) and
`docs/dev/gallery/subfolder-rename.md` (the re-path endpoint and its client bookkeeping); and
§ Gallery filters with § Sorting to `docs/dev/gallery/filters.md`. gallery-dnd.md,
gallery-state.md and gallery-nav.md moved in, with only their intros and pointers edited, as `docs/dev/gallery/drag-drop.md`,
`docs/dev/gallery/state.md` and `docs/dev/gallery/nav.md`, and `docs/dev/gallery.md` became the
family hub. Selection's recorded second seam (select-all + count key + pagination, versus the
shift-click range model) still stands, but at ~1,280 words it is not needed.

`docs/dev/image-detail.md` → `docs/dev/gallery/state.md` **and** `docs/dev/gallery/nav.md`
(then gallery-state.md and gallery-nav.md, moved into the Gallery family on 2026-10-29) was
executed on 2026-08-10, at the start of the session that would have appended the live-refresh
fixes to it. The seam was **re-cut on execution**: the recorded seam moved the persistence half
out and left the nav half behind, but between recording and execution the file grew 3,781 →
4,664 words and every one of those ~880 words landed in the *nav* half — so cutting only the
recorded seam would have left ~3,360 words behind and bought nothing. Both seams the entry
named were therefore taken at once, the recorded one and the fallback second seam (the crop
tool + caption panel + generation metadata sections, "unrelated to navigation entirely"), which
is what put all three files under the 60% target. The lesson for the next entry: a recorded
seam predicts where the *next* words will land, and when they land on the other side of it the
seam has to be re-chosen rather than executed as written.

`docs/scoring.md` → `docs/duplicates.md` was executed on 2026-08-02, at the start of the
session that appended the style-similarity percentile material to § Style similarity — the
seam held as recorded, and the staying half landed at ~2,100 words. That half is still over
the 2,500 user budget's 60% target, so its recorded second seam stood: § Style similarity
was the largest section and a separate CPU-only workflow with its own concepts. That seam
was executed on 2026-10-29 into `docs/style-similarity.md`, together with the dev side's
Quality hub.

`docs/dev/gallery.md` → `docs/dev/gallery/drag-drop.md` (then gallery-dnd.md) was executed on
2026-08-03, at the start of
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

## README.md

- **Moves:** § Features in full — the eight `###` capability groups under it (~1,150 words),
  every one of which already ends in a `→ [details](docs/…)` link
- **New file:** none. The prose does not move to a sibling; it is *deleted* and replaced by
  a short paragraph pointing at the Docs table already sitting further down the file.
- **Why here:** README is over budget (2,744 against 2,500) and the overflow is a second
  index. § Features restates, one bullet at a time, what the twelve rows of the Docs table
  already point at — the deletion rule's "duplicated in a sibling file, replace it with a
  cross-reference" case, not a compression. It grows by a bullet every time a feature ships
  (watermark removal was the latest), so it will trip again on the next one. What must stay
  is what a README is *for* and no topic doc carries: the one-paragraph pitch, Quick Start,
  Prerequisites, the Workflow chain, Configuration and the Docs table.
- **Watch for:** the Workflow chain's numbered steps also carry `→ [details]` links and read
  as a narrative rather than an index — keep those. Check `docs/features.md` before deleting:
  it is the index doc for the same material, so anything § Features says that the topic docs
  do not is a fact to move *there* rather than lose. A handful of Features bullets name
  things with no topic doc at all (verify each against the Docs table before cutting).


## docs/dev/ml-models.md

- **Moves:** § The LaMa inpainter (~910 words after this session's OOM-ladder paragraph)
- **New file:** none. It moves into the existing `docs/dev/image-processing-models.md`
  (~1,810 words today, ~2,720 after), whose own intro already frames it as "the two
  pipelines that rewrite pixels through a loaded model" — upscaling and LUT grading. LaMa
  is the third, shipped later and filed here only because its *loader* was the novel part.
- **Why here:** ml-models.md is over budget (3,615 against 3,500) and the seam is a
  subsystem line, not a size cut. What is left is the lifecycle every model shares — the
  manager and eviction, the ID registry, the device abstraction, `open_rgb`, `HF_TOKEN`,
  the weight-download rules — at ~2,700 words, and the destination sits comfortably under
  budget. The `and` test passes in both directions: "model lifecycle" and "pixel pipelines"
  are two things, and today's file is the one holding both.
- **Watch for:** the CLAUDE.md Documentation Map row for `ml-models.md` names "the LaMa
  inpainter" in its trigger, and `image-processing-models.md`'s row does not — both cells
  change, plus both `Words` counts. `docs/dev/bulk-image-jobs.md` § Painting detections out
  points at "`docs/dev/ml-models.md` § The LaMa inpainter" by name, as do
  `backend/routers/inpaint.py` and `backend/ml/lama_inpainter.py` in prose comments; grep
  `LaMa` across `docs/` and `backend/` before finishing. Keep the *loader* rules (the
  SHA-256 pin, the pickle argument, `_ensure_weights`' plain GET, the function-scope torch
  import) with the inpainter — they are the reason the section is long, and they are about
  this model rather than about the manager.
- **Second seam (recorded 2026-10-28, GPT-032/034):** the LaMa move alone no longer gets the
  file under budget — it is 4,442 words after § Leases (440) arrived, so ~3,650 once LaMa
  leaves. Move § Leases and § Releasing an entry's weights (765) together into a new
  docs/dev/model-residency.md: *what keeps a model resident and how its weights leave*. They
  are one subject — the lease is what decides whether a release may happen, and
  `_release_to_cpu`'s tenant walk is what makes it free anything — and both are read by
  someone touching a consumer's hold or an unload path, not by someone adding a model ID.
  Leaves ~2,450 here. **Watch for:** `§ Leases` is cited by name from
  `docs/dev/quality/scoring.md`, `docs/dev/detection.md`, `docs/dev/image-processing-models.md`,
  `docs/dev/backend-infrastructure.md` and PM-030; `§ Releasing an entry's weights` from
  `docs/dev/detection-inference.md` and this file's own sam2 table row. Grep both headings.


## docs/dev/captioning.md

- **Moves:** § OpenAI-compatible providers in full (~720 words) — the `/providers` router
  table, the `OpenAIProvider` model and schema, `ml/openai_compat_captioner.py`, the
  `openai_compat:{provider_id}:{model_name}` id format, the Settings LLM Providers tab and
  the `ModelPicker` component.
- **New file:** docs/dev/llm-providers.md. There is no user doc of that name to mirror
  (provider setup is a section of `docs/settings.md`), so the name is taken from the
  Settings tab users actually see.
- **Why here:** the file is over budget (3,975 against 3,500) and its title already carries
  the `and` warning sign — "Captioning post-processing **&** LLM providers". They are two
  subsystems: a provider row is CRUD plus a client, configured in Settings and consumed by
  one branch of the caption dispatch chain, while everything else in the file is about what
  the caption *text* goes through afterwards. The seam is where this session's work stopped:
  the empty-caption guard belongs entirely to the post-processing half, and the
  `finish_reason` logging entirely to the provider half. Leaves ~3,255 and ~720 — the
  destination has room for the growth this arc will keep sending it, and the remaining
  captioning half is still near budget, so a second seam (§ The captioning model picker,
  ~820 words, which is really about *pickers* and their two predicates) is the next one to
  take if ~3,050 proves too tight.
- **Watch for:** the two halves are cross-linked in both directions — the post-processing
  half's § Progress and failures names `max_retries=0` and `timeout_s`, both defined in the
  provider half, and the provider half's § Captioner points back at the empty-caption
  bullet. Turn each into an explicit `docs/dev/…` pointer rather than leaving a bare
  `§ Section` reference. `docs/dev/settings.md` and `docs/dev/comfy-prompts.md` both cite
  `docs/dev/captioning.md` for provider configuration; grep `captioning.md` across `docs/`
  and `backend/` before finishing, and give both files a Documentation Map row with fresh
  `Words` cells.

## docs/dev/versioning-service.md

- **Moves:** the `restore_snapshot` bullet with its Pass 0–3 sub-bullets, the five
  PM-026 bullets after it (complete-or-abort safety snapshot, Pass 1 all-or-nothing,
  Pass 3's failure contract, the `result_data` outcome, measured phash) and the
  `duplicate_dataset` read-side-twin bullet
- **New file:** docs/dev/versioning-restore.md
- **Why here:** the file is one `## Service` heading over a flat bullet list, and nearly
  half of it is restore alone — a four-pass algorithm plus its failure contract, read
  whenever a restore misbehaves. What stays (the two hooks, `_store_row_bytes`, snapshot
  creation, diff, the mirror bullets, the injection table) is what every *overwrite path
  elsewhere* needs. Leaves roughly 1,550 (restore) and 2,000 words, both under the 60% target —
  the latter before GPT-026 added ~330 words of detections-mirror bullets beside the labels
  one; if the stay half is then over 60%, the labels and detections mirror bullets are the
  next to move, since each spans snapshot, restore and diff. Give both files `##` headings
  per subject while splitting; this one has none below `## Service`.
- **Watch for:** CLAUDE.md's preamble to the invariants says two invariants "point at
  restore's Pass 2 and Pass 3" in this file, and `docs/dev/versioning.md`,
  PM-025 and PM-026 cite it for Pass 0c, the safety snapshot and the failure contract —
  grep `versioning-service.md` across `docs/`, `CLAUDE.md` and `backend/` (an alembic
  migration docstring cites it too) and repoint whichever ones mean restore. Add a
  Documentation Map row for the new file.

## docs/workspace.md

- **Moves:** § Starting up (435 words, now including the LAN opt-in bullet that links
  README's § Access from other devices), § Restarting & shutting down (336) and § Database
  backups (246)
- **New file:** docs/server.md (the user half of docs/dev/backend-infrastructure.md and
  docs/dev/database.md)
- **Why here:** those three are one subject, the server process's life: launching it,
  stopping or restarting it, and what it does to the database at startup. The rest of the
  page is the app's own furniture: dialogs, the top bar, meters, Split View, Logs, the File
  Browser and Booru. It tripped the budget when § Restarting & shutting down gained the
  shutdown grace for running jobs (GPT-012). The seam leaves ~935 and ~1,575 words. The
  remainder sits just over the ~1,500 target, so if it must go lower, § File Browser (557)
  is the next self-contained move, to docs/file-browser.md, mirroring docs/dev/file-browser.md.
- **Watch for:** README links three of the moving anchors (`#starting-up`,
  `#restarting--shutting-down`, `#database-backups`) and needs a Docs-table row for the new
  file. `docs/features.md` takes a row, not prose. `docs/dev/database.md` and
  `docs/dev/backend-infrastructure.md` cite `docs/workspace.md` for the backup and Logs
  horizon; repoint only the backup one. § Restarting & shutting down links `#logs`, which
  stays behind, so that link must become `workspace.md#logs`.

## docs/dev/bulk-image-jobs.md

- **Moves:** § Painting detections out (watermark removal) → docs/dev/inpaint.md, and
  § Detection-driven cropping (crop to detected subject) → docs/dev/detection-crop.md.
- **New files:** docs/dev/inpaint.md and docs/dev/detection-crop.md (the user-facing side of
  both is in docs/detection.md, so neither mirrors a user doc of its own name).
- **Why here:** the file grew around one idea, "bulk jobs that rewrite files", and the two
  detection-driven jobs became subsystems of their own: each has its own router, request
  model and scope rules. It tripped the budget when § Batch resize and aspect crop gained the
  stage, flush, swap, commit order (GPT-010). Moving both leaves ~1,400 words behind and
  ~1,130 and ~1,210 in the new files, all under the ~2,100 target. One move alone leaves
  ~2,500, which is still over.
- **Watch for:** the **Remapping detections through replace-mode crops** bullets sit inside
  § Detection-driven cropping but serve all four crop sites, batch crop included. Keep them
  in bulk-image-jobs.md (or give them their own `##`) rather than carrying them out with the
  detection worker, because § Batch resize and aspect crop and docs/dev/image-detail.md
  point at them. `docs/dev/bulk-ops.md` § Bulk jobs that rewrite files lists
  `POST /detection/crop` as living here. CLAUDE.md's Map row for this file names
  `CropToDetectionForm`, `batch_inpaint` and the LaMa paint mask in its trigger, so those
  words move to the two new rows.

## docs/dev/backend-infrastructure.md

- **Moves:** § Request guard: Host and Origin, § Frontend serving (production), § App-level
  exception handlers and § Server control endpoints → docs/dev/http-edge.md.
- **New file:** docs/dev/http-edge.md (no user doc of its own; the user side of the LAN
  opt-in is README.md and docs/workspace.md).
- **Why here:** the file is two subsystems: what wraps every request (middleware, the SPA
  catch-all, the exception handlers, the shutdown/restart routes) and the job queue
  (shutdown grace, cancellation, SSE, retention, `GET /jobs`). It tripped the budget when the
  request guard landed (GPT-007). The seam leaves ~1,720 words in the new file and ~1,940
  behind, both under the ~2,100 target. § CI workflows stays; it is the odd one out either way.
- **Watch for:** § Server control endpoints and § Shutdown: stopping the job queue point at
  each other — the restart loop calls `JobQueue.stop()` — so both need a cross-file pointer.
  PM-015 and PM-031 cite § Frontend serving and § Request guard; CLAUDE.md's Path traversal
  bullet names the SPA catch-all; the Map row's trigger words split across the two rows.
