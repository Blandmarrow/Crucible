# #130 — Better comparisons

**Status: transient (2026-11-15).** The design plan for issue #130. Its place in the
order is in [../roadmap.md](../roadmap.md) (M11, Tier 4). The user's rulings are
[decision 20](../decisions/20-compare-view.md); § Open questions says which points are
settled and which are proposals still standing as defaults. When a phase lands, move its durable rationale into
the topic file that owns the code and delete the phase from here. Delete the folder when
everything is done or dropped.

## The problem

The issue asks for "a properly designed way to compare multiple images side by side and
create comparison sheets of multiple images", and says the details should be explored.
Today the only way is split view: open two panes and point each at an image. That is a
general layout tool, not a comparison tool. Each pane zooms on its own (the detail page has
no zoom at all), every image needs its own clicks, and nothing can be exported.

The jobs a comparison serves in this app:

1. **Pick the keeper from near-duplicates.** The Quality page's duplicate groups show
   thumbnails at 256 px, which is too small to judge sharpness or artefacts.
2. **Judge an edit.** An upscale, LUT, inpaint or crop in copy mode leaves the source and
   the result side by side in the dataset. The question is whether the result is better,
   and that is answered zoomed in on the same spot in both.
3. **Compare generations.** ComfyUI runs put many takes on one prompt into a dataset. The
   question is which seed or setting wins.
4. **Share or keep a record.** A contact sheet of a selection, with names and scores, to
   look at outside the app or attach to an issue.

## What exists to build on

Checked on 2026-11-15.

| Piece | Where | What it gives |
|---|---|---|
| Full-size file by id | `GET /api/v1/images/{id}/file`, `imagesApi.fileUrl` in `frontend/src/api/images.ts` | Any image from any dataset, gated by `safe_dataset_path` |
| The selection | `frontend/src/store/selectionStore.ts` | Module-global, already spans datasets (`datasetByImageId`) |
| The selection toolbar | `frontend/src/components/gallery/SelectionToolbar.tsx` | Where a "Compare" button goes |
| Duplicate groups and their resolve route | `QualityPage.tsx` § Duplicate groups; `POST /quality/duplicates/resolve` takes `keep_ids` and `delete_ids` | A "Keep this one" action needs no new route |
| Panes | `docs/dev/panes-routing.md` | A routed page can sit beside the gallery |
| Zoom and pan | nowhere | No component and no library; `react-image-crop` is the only image dependency |
| Pillow 12.3 | `backend/requirements.txt` asks for `>=11.3` | `ImageFont.load_default(size=…)` draws scalable text with no font file to ship |

Two things the page needs do not exist yet:

- **No read of several images by id.** `GET /images/` is scoped to one dataset and
  `GET /images/{id}` reads one. Phase 1 adds `POST /images/by-ids`, returning a
  `CompareImage` (an `ImageListItem` plus `aesthetic_model` and `dataset_name`, neither of
  which any image schema carries today) in the order asked.
- **No route returns generated image bytes.** Every binary route is a `FileResponse`, and
  nothing sets `Content-Disposition`; the contact sheet route is the first.

Two more do **not** exist, and both matter to job 2:

- **No lineage for copy-mode edits.** A `_up2x` copy has no column naming its source, so the
  app cannot offer "compare with original" by itself. The user picks both images.
- **No route serves an old version's bytes.** Replace-mode edits keep the old file in
  `{ds}/.versions/objects/` (`docs/dev/versioning/hooks.md`), but nothing serves it, and
  the Versions page's `DiffModal` compares metadata only.

## The design

One new page, **Compare**, with three modes, plus one new backend route for sheets.

### The page

A routed page at `/compare?ids=a,b,c&mode=side`, not tied to a dataset, since the
selection can span datasets and the file route needs only an id. Opening it is one click:

- **Selection toolbar → Compare** (2 or more selected). Up to 4 opens *Side by side*; more
  opens *Grid*.
- **Quality page → each duplicate group → Compare.** Opens the group side by side, the
  highest-ranked image first.
- Later, perhaps: a "Compare with…" button on the detail page or on the ComfyUI page's
  results (§ Not in this plan).

**No sidebar entry, and not in a pane's page picker.** Compare is reached from images,
the way the detail page is: a sidebar link would open an empty page and send you back to
the gallery to choose. Like `image-detail`, it stays out of `PaneHeader`'s
`PAGE_OPTIONS`, since a pane picker has no ids to hand it (`docs/dev/panes-routing.md`
§ Route-level code splitting).

**Where it opens.** Without split view, Compare replaces the gallery and the browser's
Back returns to it; the gallery's remembered state (`docs/dev/gallery.md`) brings back
the page, sort and filters. In split view it opens in the **neighbouring pane**, so the
gallery stays put and you can change the selection and press Compare again. The
neighbour is the first leaf of the other half of the split that holds the gallery's pane.
With split view on but only one pane, it opens in that pane, as without split view. This
needs one new pure function in `frontend/src/store/paneStore.ts` (say `neighbourLeafId`),
since nothing there finds another pane today, with unit cases for a pane whose sibling is
a leaf, one whose sibling is a split, and a lone pane. The detail-view button on each
pane follows the same rule.

A page rather than a modal, because a URL survives a reload and can be bookmarked, and
because a pane beside the gallery is the "fast iteration" the issue asks for. The cost is
the seven-site checklist in `docs/dev/panes-routing.md` § Route-level code splitting,
with `imageIds` carried in the query string the way `sourceVideoId` is. A uuid list of
100 ids is about 3,700 characters, under any URL limit; that sets the grid's cap.

Ids that no longer exist (deleted, or a URL from the other install, since dev and prod share
an origin) drop out with one line saying how many were skipped. They never fail the page.

### Mode 1: Side by side (2 to 4 images)

Columns for 2 or 3, a 2×2 grid for 4. **Zoom and pan are linked across every pane**: the
mouse wheel zooms around the cursor, dragging pans, double-click toggles fit and 100%, and
every pane follows.

The link is kept in *normalized* coordinates: one shared centre (0 to 1 on each axis) and
one zoom factor relative to "fit". So a 1024 px source and its 2048 px upscale show the same
region of the picture, which is what job 2 needs. A **Match pixels** toggle switches to one
screen pixel per image pixel in every pane instead, which shows the real resolution gap.
A **Pixelated** toggle (`image-rendering: pixelated`) shows true pixels past 100% instead
of the browser's smoothing, for judging upscales.

Each pane has a strip under it: filename, size in pixels, file size, format, aesthetic
score with its model, the dataset when the set is mixed, and the caption folded to one
line. Where the panes differ on a number, the best value is marked, never coloured red and
green (light mode, and colour blindness).

Each pane's corner holds: open in detail view, remove from the comparison, and swap
position. **Keep this one** appears when the set came from a duplicate group, and calls
the existing resolve route with the others as `delete_ids` behind the usual confirm. Any
pane can also **delete** its image (through the single-image delete route, unmounting the
pane's image first, PM-021) and **assign labels** (through `useAssignImageLabels`).

The zoom code is hand-written, one hook (`useLinkedViewport`) over pointer and wheel
events with CSS transforms, about 200 lines. A library such as `react-zoom-pan-pinch`
handles one pane well, but linking several is the whole point here, and a new dependency
also costs a `vet-tool` pass.

The image shows its thumbnail first and swaps to the full file when it has loaded, with
`decoding="async"`, so four large PNGs do not leave four blank panes.

### Mode 2: A/B (exactly 2 images)

Both images in one frame, fitted to the same box by normalized coordinates, with the same
linked zoom and pan. Three ways to see the difference:

- **Swipe**: a divider dragged across the frame, A on the left, B on the right.
- **Flip**: hold Space (or click) to show B in place of A. The eye catches change in a
  flip that it misses side by side.
- **Difference**: B drawn over A with `mix-blend-mode: difference`, so unchanged areas go
  black. Pure CSS, no server work.

Different aspect ratios are letterboxed, with a note that the overlay is approximate. A
crop is not aligned automatically.

### Mode 3: Grid (2 to 100 images)

Every image fitted into an equal cell, with the same strip under each, in columns the user
picks. No zoom link here. Clicking a cell (or ticking up to four and pressing Compare)
opens those in *Side by side*. The grid is also the on-screen preview of the contact sheet,
so what it shows is what the sheet exports.

### Contact sheet export

**Export sheet** in Grid mode (and in Side by side, as one row) downloads a PNG or JPEG
drawn on the server:

`POST /api/v1/images/contact-sheet` with `image_ids`, `columns`, `cell_px`, `fields` (which
lines of text go under each cell: filename, size, score, caption), `background`
(dark or light) and `format`. It answers with the image as a download and writes nothing to
disk. An optional normalized `region` crops every image to the same area first, which is
**Export current view** in Side by side: the zoomed-in detail of all four images, in one
picture.

Why the server rather than a browser canvas: it reads the full files without loading 100 of
them into the tab, it opens them the way every other path does, and the result does not
depend on the browser. Why a download and not a new image in the dataset: anything in
`images/` is training data, and a contact sheet must never end up in a training set.

The route must follow the project's rules:

- **Every `file_path` is gated with `safe_dataset_path`** before it is opened. The route
  returns pixels it read, so an ungated path is an arbitrary-file *read*, the shape
  CLAUDE.md § Path traversal guard warns about.
- Images open through `image_service._open_safe` (EXIF transpose first) and are closed as
  soon as each is pasted, since a 100-image sheet otherwise holds 100 decoded buffers.
- The drawing is CPU work on the default pool, never the GPU thread.
- Captions and filenames are drawn as pixels, so they cannot inject anything. They are
  cut to fit their cell.
- Capped at 100 images and a final size of about 16,000 px on the long side; past either,
  the route answers 422 with the limit. If timing shows 100 large files taking more than a
  few seconds, it becomes a job instead (an open question below).

## Phases

Each phase is one branch and one PR, with its user docs (a section in `docs/gallery.md`
or a new compare page under `docs/`, and a README line) and a Playwright journey.

1. **Built on `feat/130-compare-page`; its durable account is now
   `docs/dev/gallery/compare.md`, so delete this entry when it merges.**
   **The page and Side by side.** `POST /images/by-ids` and `CompareImage`, the routed
   page, the selection toolbar button, opening in the neighbouring pane
   (`neighbourLeafId`),
   `useLinkedViewport`, the metadata strip, Match pixels and Pixelated, and the persisted
   mode and toggles (a per-viewer key; read `docs/dev/persistence.md` first). e2e: select
   three, press Compare, wheel over one pane, and check all three transforms moved.
2. **A/B.** Swipe, flip and difference. e2e: drag the divider and check its position.
3. **Grid and the contact sheet.** Grid mode and the export route, with **Export current
   view**. Backend tests: a sheet of three tiny images has the expected size, a row with an
   out-of-tree `file_path` is refused, and the caps answer 422.
4. **Actions and more ways in.** The Quality page's Compare button, Keep this one, and
   delete and labels on any pane. e2e: delete from a pane and see the gallery count drop,
   assign a label, keep one of a seeded duplicate group.

Phase 1 alone answers jobs 1 to 3. Phase 3 answers job 4.

## Not in this plan

Each could follow, but each needs something this plan does not build:

- **Compare with original** for copy-mode edits needs a lineage column (the source image's
  id) on `Image`. That is a new mirrored column and every rebuild path (CLAUDE.md § Key
  invariants), so it is its own issue.
- **Compare with an earlier version** needs a route that serves a file from
  `.versions/objects/`, gated like the file route, and a picker on the Versions page.
- **A compare tray**, a list you add to from the detail page while browsing, separate
  from the selection.
- **More ways in**: a "Compare with…" button on the detail page, and one on the ComfyUI
  page's results. Each needs a way to pick the other images, which the tray would give.
- **Videos**, side by side with linked playback. Frames are images, so they work already.
- **Automatic alignment** of crops and offsets.

## Known limits

- The browser draws the image, so EXIF orientation in PNG and WebP depends on it; the same
  open question the crop box has (roadmap § Later, PR #156). Normalized coordinates keep
  the panes in step with each other either way.
- Difference mode on two images of different sizes compares resampled pixels, so fine
  noise shows as change.

## Open questions

Settled by the user on 2026-11-15 ([decision 20](../decisions/20-compare-view.md)):

1. **A page or a modal?** A page, with no sidebar entry, opened from the selection
   toolbar and the Quality page, and in the neighbouring pane in split view.
2. **Which link is the default in Side by side?** The same region of the picture; Match
   pixels is the toggle.
3. **Is the contact sheet saved anywhere?** No: download only.
5. **Which actions belong in the view?** Keep this one for duplicate groups, plus delete
   and labels on any pane.

Still proposals, standing as defaults until something says otherwise:

4. **Are the caps right?** 4 in Side by side, 100 in a grid and a sheet.
6. **Does any item under § Not in this plan belong in this issue?** Compare with original
   is the one most often wanted, and the most expensive; it stays its own issue.
7. **Sheet as a job?** A plain request, timed in phase 3, and turned into a job only if
   100 large files take more than a few seconds. That timing goes back to the user.
