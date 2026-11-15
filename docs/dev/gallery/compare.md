# The Compare page

`ComparePage` shows two to four images side by side, or two in one frame (A/B), with
linked zoom and pan, or up to 100 as a grid that exports as a contact sheet (#130). This
file covers how it is reached and routed, the batch read behind it, the linked
viewport, the pane, A/B, the grid and `POST /images/contact-sheet`, the actions in the
view, and what was left unbuilt. The user-facing page is `docs/compare.md`; the rulings
are decision 20 (`docs/dev/decisions/20-compare-view.md`).
The pane system it plugs into is `docs/dev/panes-routing.md`.

## A page reached from images, never from the sidebar

`/compare?ids=a,b,c` is a routed page tied to no dataset, since the selection it opens
from can span datasets. It has no sidebar entry and is not in `PaneHeader`'s
`PAGE_OPTIONS` (decision 20): with no ids to hand, a picker could only open an empty page.
`PaneHeader` shows it, and the detail pages, as a **disabled** current option
(`UNPICKABLE_LABELS`), so a pane's selector names its page instead of falling back to
"Datasets".

**The id list is the page's identity.** `usePaneCompareIds` (`hooks/usePaneDatasetId.ts`)
reads `view.compareIds` inside a pane and `?ids=` outside one, with the pane-wins-outright
rule of its two gallery siblings. Removing or reordering a pane writes the new list back
through `usePaneNavigate().go(…, { replace: true })`, so a reload, a bookmark or a copied
URL always shows the set on screen. `PageRenderer` does **not** key the page on the list:
a remount on every removal would throw away the zoom.

`routeToView` parses the pathname only, so a pane synced from a routed `/compare` (split
view switched on while on the page) gets `{page: "compare"}` with no ids and shows the
empty state. Accepted: re-pressing Compare fills it.

## Opening it beside the gallery

The selection toolbar's **Compare** button (2 to `COMPARE_MAX` = 100 selected,
`utils/compare.ts`) passes `[...selectedIds]`, which is selection order, through
`useOpenBeside` (`hooks/useOpenBeside.ts`):

- In split view it sets the view of `neighbourLeafId(layout, paneId)`
  (`store/paneStore.ts`): the first leaf of the other half of the split holding the
  calling pane. The gallery stays on screen, and the opened pane becomes active **in a
  microtask**: the click then bubbles to the calling pane's `PaneContainer` wrapper,
  whose `onClick` makes *itself* active, so a synchronous `setActivePaneId` lost and the
  opened page's key guards (the detail view's arrows and Delete) stayed dead until the
  user clicked it. `PaneContainer` marks the active leaf `data-active="true"` so the
  e2e spec can assert it.
- Outside split view it behaves like `go`: replace the page in place, so Back returns to
  the gallery, whose persisted state (`docs/dev/gallery/state.md`) restores page, sort
  and filters. **With split view on but one pane**, it replaces that pane's view, and
  Back does not return: pane views are not browser history.

A pane's **Open** button uses the same hook for the detail view, so a comparison in the
right pane opens detail on the left rather than replacing itself.

## The batch read: `POST /images/by-ids`

`imagesApi.byIds` posts `{image_ids}` (1–100, `ImagesByIdsRequest`) and gets
`CompareImage` rows in the order asked; duplicates collapse to their first place and an
id with no row drops out, so a tab that outlives an image still loads the rest.
`CompareImage` is `ImageListItem` plus `aesthetic_model` and `dataset_name`, a separate
schema so the gallery's per-row payload does not grow.

Both this route and `GET /images/` go through `_stamped_items` in
`backend/routers/images.py`: one select with `generation_metadata` deferred (raiseload),
the effective license from a per-dataset lookup (the list route's rows all share one
dataset; by-ids' do not), `label_ids` in one query, and the metadata flag. One helper, so
the two cannot drift. Tests: `backend/tests/test_images_by_ids_http.py`.

The query key is `[COMPARE_IMAGES_KEY, ...ids]` (`constants/queryKeys.ts`). It cannot sit
under `["images", datasetId]`, since one comparison spans datasets, so **no writer
invalidates it**. Instead `ComparePage` subscribes to the query cache and refetches,
batched to one per tick, whenever any `["images", …]` or `["image", …]` query is
invalidated. Every image writer already does one of those (the detail view's crop,
resize, caption and provenance saves, TopBar's watcher for image-modifying jobs), so a
crop made in the detail view beside a comparison shows up in it, and a new writer needs
to do nothing for Compare. Without this the page served the old `updated_at`, and so
the old pixels through `fileUrlVersioned`, until a remount.

`keepPreviousData` plus a filter to the current list keeps the other panes drawn while
a removal refetches. While those placeholder rows are showing, the page shows Loading
rather than "none of these exist", since a new set swapped in place filters the old
rows down to nothing. Ids are deduped before the request, as the server does, so a
hand-edited `?ids=a,a,b` cannot draw two panes with one React key. The page counts ids
the server did not return and says so in the hint row.

## Linked zoom: `useLinkedViewport`

`hooks/useLinkedViewport.ts` holds one view for every pane in **normalized** coordinates:
the picture point at the pane's centre (`cx`, `cy`, 0..1) and a zoom relative to fit
(`z`, 1..`ZOOM_MAX` = 24). Normalized, so a source and its 2× upscale show the same region
by default (decision 20). **Match pixels** passes the first image's size as `ref` to
`geometry`, so every pane takes that image's scale and a larger image draws larger.

The view lives **outside React state**. A wheel gesture changes it dozens of times a
second; each pane subscribes and writes its own `<img>` width, height and transform on
the next animation frame, and only the toolbar's readout re-renders
(`useViewportZoom`, a `useSyncExternalStore`). `useViewportPane` binds one pane:

- the **wheel** listener is attached by hand with `passive: false`, because React
  registers `onWheel` as passive and a passive listener cannot stop the page scrolling;
- **drag** pans with pointer capture; **double-click** toggles fit and 100% at the point,
  where `z / scale` is the zoom that draws that image at 100% in either link mode;
- with the pane focused, `+`/`−`/`0` and the arrow keys;
- a `ResizeObserver` re-lays the pane when split view or the window resizes it.

`set` clamps the view and snaps any zoom at or below 1 back to centred fit, so zooming
out near an edge never leaves the picture hanging to one side.

`useViewportPane` returns a `measure()` that computes the pane's geometry **now**. The
toolbar's **100%** asks the current first pane at click time (each pane registers its
`measure` with the page). It used to read a geometry cached by pane 1's last layout,
which a **Move left** or a removal does not trigger, so 100% could scale by the image
that had been first.

## The pane

`components/compare/ComparePane.tsx` shows the thumbnail at once and swaps in the full
file when it has decoded (`decoding="async"`), so four large PNGs never leave four blank
panes. The swap waits for the first **layout** (`placed`), not the load: between the
two the full image has no size or position, and showing it flashed it unscaled for a
frame. React owns only its `visibility` and `imageRendering`; the layout writes the
width, height and transform, which React never touches. It places the image from its **natural** size, as the browser draws it with EXIF
orientation applied, never from the stored `width`/`height`, which only the strip shows.
A file that fails to load says so in place.

`MetaStrip` marks the best value in words (**most px**, **best**), never by colour alone.
Scores are compared only when every scored image used one aesthetic model, the rule the
Quality page applies to *Keep best* (`bestMarks` in `ComparePage.tsx`). The "shown at N%"
figure is written by the pane's layout callback, not rendered.

`COMPARE_PREFS_KEY` (`constants/storage.ts`) remembers the mode, A/B's way of showing B,
Match pixels and Pixelated per browser; see `docs/dev/persistence.md`.

## A/B

`components/compare/ABView.tsx` draws two images in one frame under the same viewport.
It binds the frame with `useViewportPane` for image A only; its `onLayout` writes A's
geometry onto B as well, so B always fills **A's rectangle**. Images of one shape line up
exactly; another shape is stretched, and the foot says the overlay is approximate. A
crop is not aligned. Match pixels has no meaning with one frame, so the toolbar hides it.

- **Swipe** clips B with `clip-path: inset(0 0 0 Lpx)` and A with
  `inset(0 (w − L)px 0 0)`, where `L = W × split − tx`: `inset()` is measured in each
  image's own box, before its transform. Clipping A too keeps it from showing through a
  transparent B. The divider and handle are written from the same layout; the handle
  reads the current split when it remounts. The handle is a `role="slider"` button.
  **Its arrow keys reach the frame first**: the frame's key handler is a *native*
  listener on the pane, which runs before any React handler on the handle, so a
  synthetic `stopPropagation` there came too late and the picture panned whenever it
  was zoomed. `useViewportPane`'s `onKey` therefore ignores keys aimed at a button,
  link or field inside the pane.
- **Flip** shows B only while Space or the button is held, and hides A meanwhile (a
  transparent B again). The Space listener carries the guards every pane key handler
  needs: `isTextEntryTarget`, the active-pane check (so Space in the gallery beside it
  never flips), and **no focused button or link**, where Space means "activate". A
  window `blur` and the listener's own cleanup clear it, so neither a key released
  elsewhere nor leaving Flip mid-hold can leave B stuck on.
- **Difference** is `mix-blend-mode: difference` on B: unchanged pixels go black. No
  server work.

**Nothing claims to show B before B has loaded.** With B still downloading or failed,
every mode fell back to A's pixels under a "B" label, which reads as "A and B are
identical". The labels say *loading* or *could not be loaded*, the flip waits for B
(its button is disabled until then), and a failed A says so in place, as a failed pane
does in Side by side.

The A and B picks are **ids** held in page state (`abPick`), falling back to the first
two images when a pick leaves the set; choosing the image already on the other side
swaps the two. `ABView` is keyed on the pair, so a new pair remounts it while the shared
viewport keeps the zoom. It registers its `measure` under A's id, so **100%** in A/B
means A at its real pixels.

## Grid and the contact sheet

**Which mode a set opens in** is worked out during render, React's pattern for state
that follows a prop (`seenKey` in `ComparePage.tsx`): a set that arrives from
*outside* the page (a Compare button, a URL) opens as Grid when it holds more than
`COMPARE_SIDE_MAX`, and otherwise in the last mode chosen. A list the page wrote
itself through `setIds` (a pane removed or moved, the ticked few, Back to all) is
recognised by `ownKey` and keeps its mode. It used to be recognised by being a subset
of the last list, which treated three images picked in the gallery out of a grid of
ten as a pane removal and left them in Grid; in split view the page is not remounted
between presses, so that case is real. Grid is never stored in `COMPARE_PREFS_KEY`,
since it follows from the set's size, not from a habit.

`components/compare/CompareGrid.tsx` shows thumbnails, not the files: a grid of a
hundred large PNGs would decode hundreds of megabytes for cells a few hundred pixels
wide. Up to four cells tick; **Compare ticked** remembers the full list in `backTo`
(the first one only, so ticking again from a smaller grid cannot lose it) and replaces
the ids with the ticked ones, and **Back to all N** puts the full list back. The grid's
"auto" columns are `autoGridColumns` in `utils/compare.ts`, the same formula as the
server's `auto_columns`, and a chosen count is capped at the number of images as the
server caps it, so the sheet follows the grid on screen.

**`POST /images/contact-sheet`** (`backend/routers/images.py`, drawing in
`backend/services/contact_sheet.py`) returns the sheet as a download and writes nothing
to disk (decision 20). It is the first route that returns *generated* image bytes, and
the first to set `Content-Disposition`; the filename is the dataset name (or `compare`)
reduced to `[a-z0-9-]`, never interpolated raw. In order:

1. Ids are deduped; rows with none drop out; none at all is a 404.
2. **Every `file_path` is gated with `safe_dataset_path` before anything opens it**, and
   one refused row fails the request with 403. This route returns pixels it read, so an
   ungated path would be an arbitrary-file *read* (CLAUDE.md § Path traversal guard).
   A gated path with no file, or one that will not decode, draws a "missing" cell.
3. The output size is computed by `sheet_size` from the first row's **stored**
   width and height, and refused with 422 past 16,000 px on the long side **or** past
   `MAX_SHEET_PIXELS` (64M px) in area, before any image is opened. The side cap alone
   let 100 images at 1024 px cells through, about 12,700 × 15,100 px: ~576 MB of RGB on
   a pool thread that no cancel can stop.
4. `render_contact_sheet` runs on the default pool (`run_in_executor`), never the GPU
   thread. `_open_upright` opens each image (`draft` for JPEG speed), applies the EXIF
   transpose and converts to RGB or RGBA, **closing each step's image as soon as the
   next exists**, so a large file is decoded in memory once, not two or three times.
   It does not use `image_service._safe_from` for that reason: `_safe_from` leaves the
   caller's original open beside the transposed copy and leaks the image it converts
   from. The region crop follows the transpose. The cell is then **filled**, enlarging
   too, so a small zoomed region is drawn at cell size; enlarging uses nearest-neighbour
   when the request's `pixelated` is set (the page's toggle), and shrinking always
   filters. An RGBA image is pasted through its own alpha, so no mask or converted copy
   is made. Text is `ImageFont.load_default(size=…)`, so no font file ships.

**Timing** (dev PC, 2026-11-15): 100 images at 2048×1536, half PNG and half JPEG, every
text line: 1.9 s (JPEG out) to 2.3 s (PNG out) at 256 px cells, 2.4 to 3.1 s at 512 px.
So it stays a plain request, not a background job, by the plan's own test; a sheet
that grows past a few seconds is the trigger to revisit.

**Export current view** (Side by side only) measures pane 1 at click time and sends its
visible rectangle as `region`, `[x0, y0, x1, y1]` in fractions of each image, with the
shown panes in one row and the page's Pixelated setting. One region serves every pane,
which is exact when the images share a shape and Match pixels is off (then every pane
shows the same fraction); otherwise the other panes' crops differ a little from the
screen. The size line under each cell is the whole image's, not the crop's.

`ContactSheetDialog` saves the blob through an `<a download>`; with
`responseType: "blob"` an error body is a Blob too, so `imagesApi.contactSheet` parses
its JSON `detail` back into the error for `apiErrorDetail`. It cannot be closed while a
sheet is drawing: the request cannot be called back, and a closed dialog still
downloaded and toasted "Saved".

## Acting on images

Decision 20 puts three actions in the view. All of them write through code other pages
already own, so Compare adds no new write path:

- **Labels** open `CompareLabelsDialog`, a `ModalShell` around `LabelCheckList`, not
  `LabelPicker`'s popover: a pane clips its overflow, which is the case
  `docs/dev/menus-popovers.md` warns an absolute panel gets cut off in. It writes through
  `useAssignImageLabels(imageId)`, the detail page's per-image queue, and flips its ticks
  at once, replaced by the server's list whenever that arrives. The label hotkeys are not
  wired here.
- **Delete** uses the single-image route, `imagesApi.delete`, so a file open in another
  program is a 409 whose detail the toast shows, and the pane comes back.
- **Keep this one** calls `POST /quality/duplicates/resolve` with the chosen image as the
  keeper and the rest as `delete_ids`, the Quality page's own route. The Quality page's
  per-group **Compare** button opens the group with `from=dups` (`PaneView.compareFrom`,
  `usePaneCompareFrom`), ordered by `rankForKeepBest` when every score is on one model
  and in the scan's order otherwise. The button shows **only while the comparison is
  still the whole group as opened** (`groupKey`, compared as a sorted id list so Move
  left keeps it): resolving part of a group would leave the removed members flagged as
  duplicates of an image the user never chose. `from=dups` alone is not trusted, since
  a bookmark outlives a re-scan that split the group: the rows' own flags must still
  say one group (every root, `duplicate_of` or the image itself, the same, and exactly
  one image unflagged, as the scan leaves the root).

**Both destructive actions release the file before asking for its deletion.** A
confirmed op is stored as `running`; the panes it deletes render a placeholder instead
of their `<img>` (`releasing`), and only the effect that runs after that render commits
sends the request. Windows refuses to delete a file still being served to the page
(PM-021, CLAUDE.md § Key invariants), so the order is the point. Afterwards every
dataset the op touched gets `invalidateDatasetContentScope` and its `["duplicates", id]`
key (PM-029: every dataset the selection touched). A Keep shows what it did in place of
the panes, with a button to open the kept image.

Three guards keep that sequence honest, each from a review of the first version:

- **One operation at a time.** While one runs, every action that changes the set
  (Delete, Keep, Move left, Remove) and the mode switch are disabled: a second op would
  mount the first one's images again mid-delete, and its captured id list would write
  the first deletion back into the URL; a mode switch would mount Grid or A/B images of
  the files being deleted.
- **Each op starts once.** Inside a pane, `usePaneNavigate`'s `go` changes identity on
  every pane-store write (any click in any pane re-renders every leaf), so with `go` in
  the effect's dependencies a click mid-request sent the request again. `go` and `from`
  are read through a ref, and a `started` ref guards the effect.
- **A late answer does not navigate.** If the page unmounted while the request was out,
  the delete's URL write is skipped, so a pane the user moved on is not pulled back.

The labels dialog keeps each toggle in `pending` until the server's list agrees with
it, so a refetch from the first of two quick toggles cannot untick the second on
screen; a failed write drops its entry.

## Not built

Each could follow #130; each needs something it did not build:

- **Compare with original** for copy-mode edits needs a lineage column (the source
  image's id) on `Image`: a new mirrored column and every rebuild path (CLAUDE.md § Key
  invariants). Its own issue.
- **Compare with an earlier version** needs a route that serves a file from
  `.versions/objects/`, gated like the file route, and a picker on the Versions page.
- **A compare tray** filled from the detail page while browsing, and the "Compare with…"
  buttons it would enable on the detail and ComfyUI pages.
- **Label hotkeys** on the focused pane: the detail page's handler would have to move into
  a shared hook with every guard it carries (`docs/dev/labels-ui.md` § Hotkeys).
- **Videos** side by side with linked playback (frames are images, so they work now),
  and **automatic alignment** of crops and offsets.

Two limits stand: the browser draws each image, so EXIF orientation in PNG and WebP
depends on it, the open question the crop box has (roadmap § Later); and Difference on
two images of different sizes compares resampled pixels, so fine noise shows as change.

## Tests

`frontend/e2e/compare.spec.ts`, one journey per bullet:

- **Side by side**: select three, press Compare, wheel over one pane and see all three
  grow, Fit restores them, remove one and see the URL follow and survive a reload, and
  the two-image floor.
- **Split view**: Compare from the left gallery fills the right pane, which becomes the
  active one, while the left keeps its tiles; then a pane's Open puts the detail view on
  the left, and a caption saved there appears in the comparison. This is also
  `neighbourLeafId`'s test, since the frontend has no unit runner.
- **A/B**: zoomed in, the divider's arrow keys move B's clip without panning; Space on a
  focused button does not flip, while on the frame it shows B only while held;
  Difference sets the blend; and the mode and its view survive a reload.
- **Grid**: five selected open as a grid, Export sheet downloads a PNG, two ticked open
  side by side, Export current view downloads a JPEG, and Back to all returns to the
  five.
- **A subset is a new set**: three picked from a grid of five in split view open side by
  side, not as a removal left in Grid.
- **Labels and delete**: a label ticked in a pane's dialog shows under the pane; a pane's
  delete removes the row (404 after) and the gallery shows one tile fewer.
- **Keep this one**: three uploads of one PNG, grouped by a technical-only scoring run
  (CPU, no model), open from the Quality page with Keep on every pane; a Move left keeps
  it, a removal takes it away; reopened, keeping the second pane's image leaves exactly
  that one, with its duplicate flags cleared. The label journey deletes its label in a
  `finally`: the vocabulary is app-wide and `labels.spec.ts` expects its own label to be
  the only one.

`backend/tests/test_contact_sheet_http.py` covers the route:

- the size helper against the real output, JPEG, the region, a small region enlarged to
  fill its cell, and Pixelated's hard edges;
- the out-of-tree 403 with the outside file untouched, a missing file's placeholder,
  and both caps refused before any `Image.open`;
- an EXIF-rotated JPEG drawn upright, and nothing written under the datasets folder;
- every image the service makes closed, over a rotated JPEG, a palette PNG and an RGBA
  PNG, tracking the image objects themselves and only calls the service makes (Pillow's
  own temporaries inside `resize` are not ours).

`backend/tests/test_images_by_ids_http.py` covers `POST /images/by-ids`.
