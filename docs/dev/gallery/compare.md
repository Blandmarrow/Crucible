# The Compare page

`ComparePage` shows two to four images side by side, or two in one frame (A/B), with
linked zoom and pan, or up to 100 as a grid that exports as a contact sheet (#130). This
file covers how it is reached and routed, the batch read behind it, the actions in the
view, and what was left unbuilt. The linked viewport, the pane and A/B moved to
`docs/dev/gallery/compare-viewer.md`; the grid and `POST /images/contact-sheet` moved to
`docs/dev/gallery/contact-sheet.md`. The user-facing page is `docs/compare.md`; the
rulings are decision 20 (`docs/dev/decisions/20-compare-view.md`).
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

**Back** (`goBack`) is `navigate(-1)` when React Router's `history.state.idx` says there
is an entry to return to; every edit to the set uses `replace`, so that entry is the page
that opened Compare. A pane has no history, and a bookmark or new tab has `idx` 0, so
both go to the first image's Score images page (`from=dups`) or gallery instead.

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

**The detail page's Compare menu** (`ImageDetailPage`'s header, a `MenuButton`,
`testId` `detail-compare-menu`) opens through the same hook, with no `from`, so Keep
never shows. It has no hotkey: a single key would collide with the user's label hotkeys
(`docs/dev/labels-ui.md` § Hotkeys). Its two entries:

- **With selection** sends the open image first, then `selectionStore`'s ids in pick
  order with the open image removed, so a selected current image is not counted twice.
  Disabled below two ids or above `COMPARE_MAX`.
- **With neighbours** sends `[prevId, imageId, nextId]` minus the nulls: the arrow keys'
  own ids (`docs/dev/gallery/nav.md`), boundary pages included. It is disabled with fewer
  than two (no nav context, a foreign one, or a one-image view) and **while an edge
  page's prefetch is loading** (`isLoading` of the `atEnd`/`atStart` queries), so it
  never opens two images where three are about to be known. `isLoading` rather than "no
  data yet": a failed boundary query ends loading, and the two ids in hand still compare.

Back from such a comparison is `navigate(-1)` to the detail page, by the rule in § A
page reached from images; in a pane it falls back to the gallery like any other.

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
  buttons it would enable on the ComfyUI page and as a third entry in the detail page's
  menu (whose selection and neighbours entries need no tray, § Opening it beside the
  gallery).
- **Label hotkeys** on the focused pane: the detail page's handler would have to move into
  a shared hook with every guard it carries (`docs/dev/labels-ui.md` § Hotkeys).
- **Videos** side by side with linked playback (frames are images, so they work now),
  and **automatic alignment** of crops and offsets.

Two limits stand: the browser draws each image, so EXIF orientation in PNG and WebP
depends on it, the open question the crop box has (roadmap § Later); and Difference on
two images of different sizes compares resampled pixels, so fine noise shows as change.

## Tests

`frontend/e2e/compare.spec.ts`, one journey per bullet (the A/B journey is in `docs/dev/gallery/compare-viewer.md`, the grid ones in `docs/dev/gallery/contact-sheet.md`):

- **Side by side**: select three, press Compare, wheel over one pane and see all three
  grow, Fit restores them, remove one and see the URL follow and survive a reload, and
  the two-image floor.
- **Split view**: Compare from the left gallery fills the right pane, which becomes the
  active one, while the left keeps its tiles; then a pane's Open puts the detail view on
  the left, and a caption saved there appears in the comparison. This is also
  `neighbourLeafId`'s test, since the frontend has no unit runner.
- **From the detail page**: select the first and third image there, stand on the second;
  With selection opens it first and then the two in pick order, With neighbours opens all
  three in the arrow keys' order, and in a fresh tab both entries are disabled. Each
  arrow press waits for the counter (`Image N of 3`), since a key pressed while the next
  image loads is lost.
- **Labels and delete**: a label ticked in a pane's dialog shows under the pane; a pane's
  delete removes the row (404 after) and the gallery shows one tile fewer.
- **Keep this one**: a group of three, opened from the Quality page with Keep on every
  pane; a Move left keeps it, a removal takes it away; reopened, keeping the second
  pane's image leaves exactly that one. The group is **staged with `page.route`**, not
  scanned: the Quality page's list and the duplicate flags on Compare's rows are answered
  as a Technical scan writes them, while the resolve is real. A scoring run imports
  torch whatever its boxes say, and CI's e2e has none (roadmap § Later). The label journey deletes its label in a
  `finally`: the vocabulary is app-wide and `labels.spec.ts` expects its own label to be
  the only one.

`backend/tests/test_images_by_ids_http.py` covers `POST /images/by-ids`.
