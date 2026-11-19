# Panes and routing: the app shell

This file covers the shell every page renders inside: the Sidebar's active-dataset rule, the
split-view pane manager (`paneStore`, `PaneContext`, the `pane/` components and their `App.tsx`
integration), and route-level code splitting including the six-site checklist a new routed page
must satisfy. The state a page itself uses is in `docs/dev/frontend-core.md` and its constants in `docs/dev/frontend-constants.md`,
job progress in `docs/dev/frontend-jobs.md`, job-completion cache invalidation in `docs/dev/job-invalidation.md`, and the CSS tokens and
modal conventions in `docs/dev/styling.md`.

### Layout

**Sidebar** uses `useMatch("/datasets/:datasetId/*")` (not `useParams`) to detect the active dataset, because the Sidebar renders outside the `<Routes>` tree and `useParams` would always return `{}` there.

### Split view pane manager

Allows the main content area to be split into any number of nested panes, each independently showing any page with its own dataset selection.

**Data model** (`frontend/src/store/paneStore.ts`):

```
PaneLeaf  { type: "leaf"; id: string; view: PaneView }
PaneSplit { type: "split"; id: string; direction: "horizontal"|"vertical";
            sizes: [number, number]; children: [PaneTree, PaneTree] }
PaneView  { page: PageType; datasetId?: string; imageId?: string;
            videoId?: string; subfolder?: string; sourceVideoId?: string; filter?: string;
            compareIds?: string[]; compareFrom?: "dups"; chatId?: string }
```

All tree mutations (`splitNode`, `closeNode`, `updateLeafView`, `updateSplitSizes`, `updateFirstLeaf`) are pure functions — the store holds a single immutable `layout: PaneTree` root. `syncFromRoute(view)` updates only the first leaf (left-to-top traversal) when URL navigation occurs, preserving all other panes.

**Context & hooks** (`frontend/src/contexts/PaneContext.tsx`, `frontend/src/hooks/`):

| Hook | Purpose |
|---|---|
| `usePaneDatasetId()` | Returns `ctx?.view.datasetId ?? useParams().datasetId` — works both inside and outside pane mode |
| `usePaneImageId()` | Same pattern for `imageId` |
| `usePaneVideoId()` | Same pattern for `videoId` |
| `usePaneGallerySubfolder()` | Same idea for `subfolder`, except the routed-mode source is `useSearchParams().get("subfolder")` rather than a route param — a subfolder is a filter, not an identity, so there is no route segment for it. **Not a `??` chain, unlike the three rows above**: inside a pane it returns `ctx.view.subfolder` and stops. `usePaneNavigate`'s `go` sets the pane view *or* calls `navigate(url)`, never both, so in split view the URL still carries whatever was routed before the split — falling through to it would apply one pane's deep link to its neighbour, and applying one resets that gallery to page 1. `""` is a real value (the dataset root); `undefined` means no link asked for anything. `GalleryPage` applies it once per *change* so a deep link never fights the sidebar — see `docs/dev/video/ui.md` |
| `usePaneGallerySourceVideo()` | The lineage sibling of the row above, with the same pane-wins-outright rule: `ctx.view.sourceVideoId` inside a pane, `useSearchParams().get("source_video_id")` outside. Gallery-only, a deep-link target for "every frame this video produced". Unlike `subfolder`, `""` carries no meaning — the value is an opaque uuid. `routeToView` parses the pathname only, which is exactly why the query string carries it in routed mode. See `docs/dev/video/ui.md` |
| `usePaneGalleryFilter()` | The gallery's `?filter=` deep link (M12) with the same pane-wins-outright rule — `ctx.view.filter` inside a pane, `?filter=` outside — plus `consume()`, which strips it from the URL (`replace`) or clears it from the view once applied. Unlike its two siblings it is consumed, because it replaces every gallery filter and would otherwise re-apply on the next mount. See `docs/dev/gallery/state.md` § The `?filter=` deep link |
| `usePaneCompareIds()` | The Compare page's id list: `ctx.view.compareIds` inside a pane, the comma-separated `?ids=` outside, with the same pane-wins-outright rule as the two rows above. Unlike them it is the page's identity, not a one-shot deep link: `ComparePage` writes the list back as panes are removed or reordered. See `docs/dev/gallery/compare.md` |
| `useOpenBeside()` | Opens a page in the **neighbouring** pane, `neighbourLeafId(layout, paneId)`: the first leaf of the other half of the split holding the caller, which then becomes active. With one pane, or outside split view, it is `go`. Compare uses it so the gallery that asked stays on screen, and Compare's own Open button uses it for the detail view |
| `usePaneNavigate()` | Returns `{ go(url, view, opts?), back(fallbackView) }`. **Inside a pane**: calls `paneStore.setView(paneId, view)`. **Outside**: calls `navigate(url, { replace: opts?.replace })`. `opts.replace` is therefore a no-op inside a pane, which has no history of its own; `ImageDetailPage`'s cross-page arrow navigation passes it so the browser history does not fill with one entry per image. All intra-app navigation that may occur inside a pane MUST use this hook; raw `navigate()` calls change the URL and trigger `RouteSyncer` which only updates pane 1. |

**Components** (`frontend/src/components/pane/`):

- `PaneContainer` — recursive renderer; splits use `react-resizable-panels` `Group`/`Panel`/`Separator` with `orientation` prop. Installed version exports `Group`, `Panel`, `Separator` — NOT `PanelGroup`/`PanelResizeHandle`. `onLayoutChanged` receives `{ [panelId]: number }` keyed by `id` prop on each `<Panel>`. The leaf content wrapper is `display: flex; flexDirection: column` so that pages whose root div uses `flex: 1, overflowY: "auto"` (StatsPage, QualityPage, CaptioningPage, ExportPage, DatasetsPage) correctly fill the pane height and show a scrollbar. Pages that use `height: "100%"` instead (GalleryPage, FileBrowserPage) also work because `height: 100%` resolves against the flex container's definite height.
- `PaneHeader` — 32 px header per pane: page-type `<select>`, dataset `<select>` (for pages in `NEEDS_DATASET`), split-H / split-V / close buttons.
- `PageRenderer` — switch over `view.page` → renders the matching lazy page component from `pages/lazyPages.ts`, wrapped in its own `<Suspense>` (see § Route-level code splitting).

#### A pane's dataset is part of a page's identity

Changing a pane's dataset in `PaneHeader` changes `view.datasetId` **without unmounting the
page** — the one gesture in the app that does. A page seeding per-dataset state in `useState`
initializers therefore keeps the *previous* dataset's state, while any persist effect with
`datasetId` in its dep array happily re-fires and writes that state under the **new**
dataset's key. The old dataset's remembered state is destroyed and the new one's is never
read.

Three pages carry `key={view.datasetId}` in `PageRenderer` for that reason, so a dataset
change arrives as a fresh mount:

| Page | What leaked without the key |
|---|---|
| `GalleryPage` | the whole `gallery-state-${datasetId}` blob — page, sort, every filter |
| `TagConsolidatePage` | dataset A's analyzed tag-merge mapping, appliable to B (data corruption) |
| `VersionsPage` | A's branch id written under B's key, and A's snapshot left in an open confirm dialog while the mutation targets B |

**The other six dataset pages must not be keyed.** `CaptioningPage`, `QualityPage`,
`StatsPage`, `ExportPage`, `BulkEditPage` and `ComfyPage` already re-seed through a
`prevDatasetId` effect (e.g. `ExportPage.tsx` ~L301-330) and a remount would *harm* them:
each holds an `activeJobId`/`detectJobId` in local state with no re-adoption path, so keying
them drops a running job's progress bar and the `invalidateQueries` its completion fires.
`ImageDetailPage`/`VideoDetailPage` seed nothing per-dataset and must not be keyed either —
their arrow navigation changes `imageId` under a constant `datasetId`.

Accepted costs of the three keys: a rescan or import started from the gallery loses its
completion toast if the pane switches away mid-job (today those effects fire against the
*wrong* dataset, so the key is a strict improvement); `autoRescannedRef` resets, so
auto-rescan-on-open can re-fire when returning to a dataset in the same pane, which is
already what routed mode does on every visit; an in-flight `tag_consolidate` analyze is
orphaned rather than applied to the wrong dataset.

`App.tsx` needs no equivalent. In routed mode every dataset link derives its id from the
current match, the only cross-dataset jump is from `DatasetsPage` (a different route element,
so the gallery has already unmounted), and a hand-typed URL is a full reload — nothing
changes only `:datasetId` under a live page. `frontend/e2e/gallery-restore.spec.ts` pins the
gallery case.

**App integration** (`frontend/src/App.tsx`):

- `MainContent` renders `<PaneContainer node={layout}>` when `paneStore.enabled`, otherwise the normal `<Routes>` tree.
- `RouteSyncer` (child of `BrowserRouter`) uses `useEffect` on `location.pathname` to call `syncFromRoute()` when pane mode is active — keeps the primary pane in sync with sidebar/URL navigation.
- Toggle: `<Columns2>` icon button in `TopBar` calls `paneStore.toggleEnabled()`.

#### The layout survives a reload

`paneStore` saves `enabled`, the tree and the active pane under `PANE_LAYOUT_KEY`
(`docs/dev/persistence.md`), debounced 250 ms because a divider drag updates the sizes on
every frame and flushed on `pagehide`, so a change just before a reload or a Restart is
kept. It restores them when the store is created. It was added for M12 phase 2e,
so a chat pinned beside the work comes back after a reload (`docs/dev/analyst/chat-ui.md`
§ Open beside). Three rules:

- **One-shot deep-link targets are not saved**: `subfolder`, `sourceVideoId` and `filter`
  are each applied once per change, so restoring one would re-apply it over what the user
  has chosen since. Identities are saved: `datasetId`, `imageId`, `videoId`,
  `compareIds`, `chatId`.
- **A saved tree is checked node by node and dropped whole if anything is off**: each
  node's shape, the id fields' types, `compareIds` as a list of strings, and a page this
  build does not know (`isPageType`, whose record of every `PageType` fails the
  build when a new page is missing). A dev and a production install on one origin share the
  key, and may not know the same pages.
- **The first pane still follows the route.** `RouteSyncer` overwrites the first leaf from
  the URL as usual, so after a reload it shows what the address bar says, and the other
  panes show what they did. The route carries no chat id, so `updateFirstLeaf` keeps the
  leaf's `chatId` when the sync leaves it on the same page and dataset, and `RouteSyncer`
  seeds it from `chatStore.openChat` for the routed chat page as split view turns on.

**Settings and Logs step outside split view.** They have no `PageType`, so with split
view on they could not be reached at all (`routeToView` maps them to Datasets), and a
restored layout would keep it that way across reloads. `MainContent` renders the routed
tree for `/settings` and `/logs` whatever `enabled` says (`isRoutedOnly`), and `RouteSyncer`
does not sync from them; the panes are back on the next dataset page.

A saved dataset that has since been deleted, or one from another install sharing the
origin, is the page's to handle, as when a dataset is deleted under an open pane. The pane
header's dataset select shows "— missing dataset —" for it rather than the first name in
the list, and a dataset switch clears the pane's `chatId`, since a chat belongs to one
dataset.

`openBeside(fromPaneId, view)` is the store's "show this next to that" action: the
neighbouring pane (`neighbourLeafId`) when there is one, otherwise a new pane split off
`fromPaneId` at 62/38, and with split view off it turns it on with a placeholder left pane
that `RouteSyncer` fills from the route. It returns the pane it used. **It never uses the
first pane**: the sidebar drives that one, so anything opened there would be replaced by
the next click. With no `fromPaneId` (a caller outside any pane, like the sidebar) it opens
beside the first pane, not the active one; a neighbour that is the first pane means a split
instead. `useOpenBeside`
predates it and only reuses or replaces; `useOpenChatBeside` is its first caller.

### Route-level code splitting

Every page is `React.lazy`-loaded through **one** module, `frontend/src/pages/lazyPages.ts`, so each page ships as its own chunk instead of being pulled into the initial bundle. Its module docstring carries the full rationale.

Two rules, neither of which any tool enforces:

- **Add a page by adding a line to `lazyPages.ts`** — never by importing the page directly in a consumer. A direct `import GalleryPage from "./pages/GalleryPage"` compiles, lints and tests clean while silently folding that page back into the index chunk. The only symptom is a fatter `index` bundle in `npm run build` output.
- **Both consumers import from that module**, never from a second copy. `App.tsx` (single-view `<Routes>`) and `components/pane/PageRenderer.tsx` (split view) must resolve a given page to the *same* lazy component identity, or toggling a pane between single- and split-view remounts the page from a second chunk and loses its state.

**A *routed* page touches six sites**, not just `lazyPages.ts` — the checklist, as `video-detail` exercised it: `pages/lazyPages.ts`; `App.tsx`'s lazy import and its `<Route>`; `PageRenderer`'s switch; `contexts/PaneContext.tsx` (the `PageType` union, plus any id the page needs — `videoId`); `routeToView` in `App.tsx`, where a `/datasets/:id/…` sub-route regex must sit **above** the generic dataset-page match, which would otherwise also match; and the id accessor, `usePaneVideoId` in `hooks/usePaneDatasetId.ts`. Miss one and the page works in single view but not in a pane, or the reverse.

A page that must also be **pickable** from a pane's page selector touches a seventh:
`PaneHeader`'s `PAGE_OPTIONS` (and `NEEDS_DATASET`, if it takes one) — which is why
`docs/dev/workspace.md` names `PaneHeader` as a site for `BooruPage`. The checklist gets away
with six because `video-detail` and `image-detail` are deliberately absent from both: you
reach them by clicking a card, never by choosing them from a dropdown with no id to hand.
`compare` is the third such page, and the first routed page outside `/datasets/…` that
is not pickable either. `PaneHeader` shows all three as a **disabled** current option
(`UNPICKABLE_LABELS`), so a pane's selector names its page rather than displaying its
first option, "Datasets". A new unpickable page adds its label there.

Each consumer owns its own `<Suspense>`: `App.tsx` wraps the whole `<Routes>` tree, while `PageRenderer` wraps per pane — a per-pane boundary is deliberate, so a pane still fetching its chunk shows its own fallback instead of blanking a sibling pane that is already rendered.
