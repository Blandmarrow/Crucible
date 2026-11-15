# The Compare page

`ComparePage` shows two to four images side by side with linked zoom and pan (#130). This
file covers how it is reached and routed, the batch read behind it, the linked viewport,
and the pane. The user-facing page is `docs/compare.md`; the design and the phases still
to come (A/B, the grid and contact sheet, actions) are in `docs/dev/compare-plan/README.md`
until they land; the rulings are decision 20 (`docs/dev/decisions/20-compare-view.md`).
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

`COMPARE_PREFS_KEY` (`constants/storage.ts`) remembers Match pixels and Pixelated per
browser; see `docs/dev/persistence.md`.

## Tests

`frontend/e2e/compare.spec.ts`: select three, press Compare, wheel over one pane and see
all three grow, Fit restores them, remove one and see the URL follow and survive a reload,
and the two-image floor; and in split view, Compare from the left gallery fills the right
pane, which becomes the active one, while the left keeps its tiles; then a pane's Open
puts the detail view on the left, and a caption saved there appears in the comparison. `neighbourLeafId` has no unit test, since the
frontend has no unit runner; the split-view journey is its test.
