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


## docs/workspace.md

- **Moves:** § Starting up (435 words, now including the LAN opt-in bullet that links
  README's § Access from other devices), § Restarting & shutting down (336) and § Database
  backups (246)
- **New file:** docs/server.md (the user half of docs/dev/http-edge.md and
  docs/dev/database.md; the shutdown grace it describes is in docs/dev/backend-infrastructure.md)
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
