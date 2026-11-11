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

## docs/dev/comfyui/queue.md

- **Moves:** the row endpoints of § Backend that M6 §8b and §8c grew: the folder's exemption
  and `set-subfolder`, the `linked` modes, "A live run locks its plan's folders and rows",
  `rows/place`, `rows/copy`, the folder tree's endpoints and plan duplicate.
- **New file:** docs/dev/comfyui/rows-api.md, with a row in the hub `docs/dev/comfyui.md`;
  queue.md keeps the data model, `comfy_service.py`, ComfyClient, plan CRUD, the library and
  the gotchas.
- **Why here:** §8c took it to 3,141 of 3,500 words (2026-11-05). Every §8 stage added a row
  endpoint to one section; §8d added none, and the 2026-11-05 review fixes' guards took it
  over budget, to ~3,530.
- **Watch for:** `prompt_origin`'s writer table names `rows/copy`; `docs/dev/comfyui/page.md`,
  `library-dnd.md` and `structured.md` point at `queue.md` § Backend for these endpoints, so
  grep `queue.md` across docs/dev and repoint each that means a row endpoint.

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

`docs/dev/database.md` → `docs/dev/dataset-folders.md` was executed on 2026-11-11 on
`docs/split-database-statistics`, and was **re-cut on execution**. The recorded seam (the five
dataset-folder paragraphs) measured 880 words of 3,297, which would have left ~2,420 behind,
69% of budget. § The datasets root's one spelling (~560 words) went with it: it is the same
folder-on-disk subsystem, the sweep's case fold and refusal point into it, and it was already
half cross-referencing the moved paragraphs. That left ~2,050 (database) and ~1,440
(dataset-folders). The entry's Watch-for was right; it missed `docs/dev/shared-utilities.md`
(the `on_disk_spelling` entry), `docs/dev/postmortems/PM-034-sqlite-like-is-case-blind.md`,
`docs/dev/roadmap-archive/m6-comfy-automation.md` and the roadmap's pending-splits count, all
of which named the root-spelling section.

A batch of thirteen splits was executed on 2026-11-04 on `feat/m6-comfy-automation`, after
an audit found ten files at 90%+ of budget with no entry here. Four had recorded seams
(`docs/dev/comfyui/automation.md`, `docs/runs.md`, `docs/dev/quality/style-similarity.md`,
`docs/dev/image-processing-models.md`), and **all four were re-cut on execution**, because
the recorded seam moved too little to reach 60%. automation.md needed § The stages out as
well (`docs/dev/comfyui/run-stages.md`) and took the whole Runs page into
`docs/dev/comfyui/run-start.md`. runs.md also gave up § Repeating until every folder is
full (`docs/repeating-runs.md`). style-similarity's § Other models measured 754 words, not
the ~950 recorded, so the other measurement sections went with it into
`docs/dev/quality/style-evaluation.md`. image-processing-models' five job paragraphs were
867 words, not ~1,250, so the output-mode paragraphs, LUT job lines and the ImageDetailPage
outcome bullet joined them in `docs/dev/image-edit-job.md`. The unrecorded seams:
run-loop.md → `docs/dev/comfyui/run-gate.md` + `docs/dev/comfyui/run-budgets.md` (the seed
check went with the budgets, not the gate); provenance → `docs/dev/provenance-api.md` and
`docs/licenses.md`; frontend-core → `docs/dev/frontend-constants.md`; frontend-jobs →
`docs/dev/job-invalidation.md` (moving invalidation out beat moving reporting out, which
only reached 62%); environment-setup → `docs/dev/server-start.md` (remainder accepted at
64%); the video hub → `docs/dev/video/storage.md`; and datasets-page, which first gained
`##` headings, → `docs/dev/dataset-operations.md`. `MAX_CLAUDE_WORDS` went to 6,500 for the
six new Map rows. The lesson matches the earlier re-cuts: a recorded word count goes stale
as fast as the file grows, so re-measure the seam before moving anything.

The old flat docs/dev/comfy-structured.md → `docs/dev/comfyui/structured-llm.md` **and**
`docs/dev/comfyui/structured-expansion.md` was executed on 2026-11-03 on
`feat/m6-comfy-automation`, ahead of M6 §3, and was **re-cut on execution**. Re-measured at
3,160 words, the recorded seam (§ The outline call, 575 words) would have left ~2,585 behind,
74% of budget. So § The spec and § Expansion went too, along the module line: the pure
`blueprint_expand.py`, with no DB and no HTTP, against the blueprint tables, fill accounting,
orphans, Apply and Top up that stayed. The recorded filename held for the LLM half, which
§3's rewrite pass joins. With eight ComfyUI files the family also went behind a hub in the
same change (decision 2's "past about three files"): `docs/dev/comfyui.md` became the hub and
its old content `docs/dev/comfyui/queue.md`, and the five other files moved into
`docs/dev/comfyui/` under shorter names. The entry's Watch-for was right; it missed only
`backend/services/blueprint_expand.py`'s docstring and `backend/ml/prompt_generator.py`'s
banner comment, which now point at the two new halves.

`docs/workspace.md` → `docs/server.md` **and** `docs/file-browser.md` was executed on 2026-10-29
as an M3 task, the second of the user-docs queue, and was **re-cut on execution**, taking the
entry's own fallback. Re-measured at 2,696 words, the recorded seam (§ Starting up,
§ Restarting & shutting down and § Database backups out to `docs/server.md`) would have left
~1,670 behind, 67% of budget, so § File Browser went to its own page as well, mirroring
`docs/dev/file-browser.md`. That left ~1,150 (workspace), ~1,110 (server) and ~610 (file
browser). The entry's Watch-for was right but missed CLAUDE.md's `§ Restarting & shutting
down` pointer, which the checker cannot see; the re-cut added the `#file-browser` links in
README, `docs/datasets.md` and `docs/features.md`.

`docs/gallery.md` → `docs/datasets.md` **and** `docs/subfolders.md` was executed on 2026-10-29
as an M3 task, the first of the user-docs queue, and was **re-cut on execution**. Re-measured at
3,227 words rather than the 2,580 the entry assumed, the recorded seam (§ Datasets and
§ Getting images in out to `docs/datasets.md`) would have left ~1,980 behind, 79% of budget.
So § Organising into subfolders, with its rename/re-nest subsection, went to its own page as
well: its two bold lead-ins became `##` headings and its `###` rename subsection was
promoted to `##`. It is the
subfolder sidebar, a subsystem of its own, mirroring `docs/dev/gallery/subfolders.md`.
§ Sorting and § Manual Image Ordering stayed with the grid, so README's
`gallery.md#manual-image-ordering` anchor still holds. That left ~1,300 (datasets), ~1,340
(gallery) and ~740 (subfolders). The entry's Watch-for was right except for
`docs/duplicates.md`, which no longer linked here.

`docs/dev/bulk-image-jobs.md` → `docs/dev/detection-crop.md` **and** `docs/dev/inpaint.md` was
executed on 2026-10-29 as an M3 task, with the seam as recorded and the remap bullets promoted
to their own `##` in the staying half. The entry's word counts were stale: re-measured at
3,946 words, the split left ~1,970 behind (56%, so little headroom; the next move out is
§ Rebuilding thumbnails), ~900 and ~1,250.

`docs/video.md` → `docs/video-extraction.md` **and** `docs/video-reextract.md` was executed on
2026-10-29 as an M3 task, taking the entry's own fallback. Re-measured at 2,723 words, the
recorded seam would have left ~1,740 in the new file, so § Re-extracting at full resolution
went to its own page rather than staying behind: pass 2 is about *curated frames*, and README
already described it in its own bullet. `docs/video.md` kept the intro, § Adding videos and
§ Browsing them (~900), and § Optional packages went with the dialog into
`docs/video-extraction.md` (~1,400). No `video.md#…` anchor existed anywhere, so the sweep
was about which of the page-level links wanted a moved half.

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
`bulk-image-jobs.md`, `versioning.md` → `versioning-service.md` (since moved into
`docs/dev/versioning/`), `export.md` → `export-licensing.md`, `detection.md` →
`detection-inference.md`, `statistics.md`'s misfiled `GET /images/` filter table → `image-filters.md`, and CLAUDE.md § Shared
utilities → `docs/dev/shared-utilities.md`.

## README.md

- **Moves:** § Features in full — the eight `###` capability groups under it (~1,150 words),
  every one of which already ends in a `→ [details](docs/…)` link
- **New file:** none. The prose does not move to a sibling; it is *deleted* and replaced by
  a short paragraph pointing at the Docs table already sitting further down the file.
- **Why here:** README was over budget (3,027 against 2,500) until the budget was raised
  to 4,000 on 2026-10-30, deferring this cut rather than dropping it; the overflow is a
  second index. § Features restates, one bullet at a time, what the twelve rows of the Docs table
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

## docs/dev/comfyui/page.md

- **Moves:** § The plan rail, the folder tree and the grouped table, plus the Rows toolbar,
  `ComfyRowsSelectionBar` and the selection-prune bullets out of § Frontend
- **New file:** docs/dev/comfyui/rows-tab.md (mirrors docs/comfy-queue.md, the Rows tab's user guide)
- **Why here:** the §8 review fixes took it to ~3,520 of 3,500 words (2026-11-05). § Frontend
  is ~2,200 words of which about half is the Rows tab (toolbar, selection bar, table,
  selection and busy rules); the rest is the page shell (routing, header, `WorkflowPinPanel`,
  the defaults strip, the run bar, `activeRuns`) and § The layout. The Rows-tab half is one
  subsystem with its own user doc, so it splits cleanly and leaves both near 60%.
- **Watch for:** `library-dnd.md`, `blueprint-tab.md`, `persistence.md` (the
  `COMFY_ROWS_VIEW_KEY` row) and the hub's routing table point at `page.md` § The plan rail,
  the folder tree and the grouped table; `styling.md` points at `page.md` for the rail and
  tree's `ContextMenu`. The `planBusy` bullet sits inside the run-bar bullet but belongs with
  the Rows tab's controls.

## docs/dev/statistics.md

- **Moves:** § The raw lists load on demand, § CSV export and § Bucket drill-down (which
  holds the **Editable histograms (HistPanel)** paragraph)
- **New file:** docs/dev/statistics-histograms.md (what a histogram panel does in the
  browser: custom edges, the raw lists, bar click-through, the CSV)
- **Why here:** M9 stage 4 took the file to ~3,300 of 3,500 words (2026-11-11), not yet over.
  Those three sections are `HistPanel` and the page's own exports; what stays is the page's
  queries, the server aggregation with the `DatasetStats` table, and the category panels.
  The 1,000-word `DatasetStats` table is why the stay-behind half is the larger one.
- **Watch for:** `scale-plan/README.md` § Stage 4 as built cites "§ The raw lists load on
  demand" by name, and three places inside the file point at it (the query table, § Frontend
  page's error-states paragraph, the Editable histograms paragraph); the first two become
  cross-file. § Backend aggregation's `score_means` row and its closing paragraph on
  `DEFAULT_EDGES` both describe what the moved sections consume.
