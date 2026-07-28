# Panes and routing: the app shell

This file covers the shell every page renders inside: the Sidebar's active-dataset rule, the
split-view pane manager (`paneStore`, `PaneContext`, the `pane/` components and their `App.tsx`
integration), and route-level code splitting including the six-site checklist a new routed page
must satisfy. The state and constants a page itself uses are in `docs/dev/frontend-core.md`,
job progress and cache invalidation in `docs/dev/frontend-jobs.md`, and the CSS tokens and
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
            videoId?: string; subfolder?: string }
```

All tree mutations (`splitNode`, `closeNode`, `updateLeafView`, `updateSplitSizes`, `updateFirstLeaf`) are pure functions — the store holds a single immutable `layout: PaneTree` root. `syncFromRoute(view)` updates only the first leaf (left-to-top traversal) when URL navigation occurs, preserving all other panes.

**Context & hooks** (`frontend/src/contexts/PaneContext.tsx`, `frontend/src/hooks/`):

| Hook | Purpose |
|---|---|
| `usePaneDatasetId()` | Returns `ctx?.view.datasetId ?? useParams().datasetId` — works both inside and outside pane mode |
| `usePaneImageId()` | Same pattern for `imageId` |
| `usePaneVideoId()` | Same pattern for `videoId` |
| `usePaneGallerySubfolder()` | Same pattern for `subfolder`, except the fallback is `useSearchParams().get("subfolder")` rather than a route param — a subfolder is a filter, not an identity, so there is no route segment for it. `paneGo` writes both, so pane state wins in split view and the query string covers the routed case. `""` is a real value (the dataset root); `undefined` means no link asked for anything. `GalleryPage` applies it once per *change* so a deep link never fights the sidebar — see `docs/dev/video-ui.md` |
| `usePaneNavigate()` | Returns `{ go(url, view), back(fallbackView) }`. **Inside a pane**: calls `paneStore.setView(paneId, view)`. **Outside**: calls `navigate(url)`. All intra-app navigation that may occur inside a pane MUST use this hook; raw `navigate()` calls change the URL and trigger `RouteSyncer` which only updates pane 1. |

**Components** (`frontend/src/components/pane/`):

- `PaneContainer` — recursive renderer; splits use `react-resizable-panels` `Group`/`Panel`/`Separator` with `orientation` prop. Installed version exports `Group`, `Panel`, `Separator` — NOT `PanelGroup`/`PanelResizeHandle`. `onLayoutChanged` receives `{ [panelId]: number }` keyed by `id` prop on each `<Panel>`. The leaf content wrapper is `display: flex; flexDirection: column` so that pages whose root div uses `flex: 1, overflowY: "auto"` (StatsPage, QualityPage, CaptioningPage, ExportPage, DatasetsPage) correctly fill the pane height and show a scrollbar. Pages that use `height: "100%"` instead (GalleryPage, FileBrowserPage) also work because `height: 100%` resolves against the flex container's definite height.
- `PaneHeader` — 32 px header per pane: page-type `<select>`, dataset `<select>` (for pages in `NEEDS_DATASET`), split-H / split-V / close buttons.
- `PageRenderer` — switch over `view.page` → renders the matching lazy page component from `pages/lazyPages.ts`, wrapped in its own `<Suspense>` (see § Route-level code splitting).

**App integration** (`frontend/src/App.tsx`):

- `MainContent` renders `<PaneContainer node={layout}>` when `paneStore.enabled`, otherwise the normal `<Routes>` tree.
- `RouteSyncer` (child of `BrowserRouter`) uses `useEffect` on `location.pathname` to call `syncFromRoute()` when pane mode is active — keeps the primary pane in sync with sidebar/URL navigation.
- Toggle: `<Columns2>` icon button in `TopBar` calls `paneStore.toggleEnabled()`.

### Route-level code splitting

Every page is `React.lazy`-loaded through **one** module, `frontend/src/pages/lazyPages.ts`, so each page ships as its own chunk instead of being pulled into the initial bundle. Its module docstring carries the full rationale.

Two rules, neither of which any tool enforces:

- **Add a page by adding a line to `lazyPages.ts`** — never by importing the page directly in a consumer. A direct `import GalleryPage from "./pages/GalleryPage"` compiles, lints and tests clean while silently folding that page back into the index chunk. The only symptom is a fatter `index` bundle in `npm run build` output.
- **Both consumers import from that module**, never from a second copy. `App.tsx` (single-view `<Routes>`) and `components/pane/PageRenderer.tsx` (split view) must resolve a given page to the *same* lazy component identity, or toggling a pane between single- and split-view remounts the page from a second chunk and loses its state.

**A *routed* page touches six sites**, not just `lazyPages.ts` — the checklist, as `video-detail` exercised it: `pages/lazyPages.ts`; `App.tsx`'s lazy import and its `<Route>`; `PageRenderer`'s switch; `contexts/PaneContext.tsx` (the `PageType` union, plus any id the page needs — `videoId`); `routeToView` in `App.tsx`, where a `/datasets/:id/…` sub-route regex must sit **above** the generic dataset-page match, which would otherwise also match; and the id accessor, `usePaneVideoId` in `hooks/usePaneDatasetId.ts`. Miss one and the page works in single view but not in a pane, or the reverse.

Each consumer owns its own `<Suspense>`: `App.tsx` wraps the whole `<Routes>` tree, while `PageRenderer` wraps per pane — a per-pane boundary is deliberate, so a pane still fetching its chunk shows its own fallback instead of blanking a sibling pane that is already rendered.
