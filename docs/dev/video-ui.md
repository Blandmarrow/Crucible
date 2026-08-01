# Video frontend surfaces

Covers every screen that shows or acts on a video: the gallery's `VideoStrip` and its
selection, `VideoDetailPage`, the extraction history panel, and the frame-lineage line on
`ImageDetailPage` with the gallery filter it deep-links into. The two-step
`ExtractFramesModal` those screens open — with the `useVideoExtractJobs` re-attach hook —
is in `docs/dev/video-extract-ui.md`, and its `CropOverlay`/`TrimBar` controls are in
`docs/dev/video-extract-controls.md`. The backend
behind all of it is in `docs/dev/video.md` (model, storage, ingest),
`docs/dev/video-endpoints.md` (the `/videos` routes) and
`docs/dev/video-extract.md` (the probe/extract endpoints and the `video_extract` job).

`ReextractFramesForm` and its three entry points — two of which sit on surfaces described
below, the lineage line and the extraction history panel — are in
`docs/dev/video-reextract-ui.md`, with pass 2's backend in `docs/dev/video-reextract.md`.

`frontend/src/utils/duration.ts::formatDuration(ms)` → `"4:12"`, `"1:02:33"`, `"—"` for
NULL. Every video surface formats through it, because NULL is *unknown* and must never
render as `0:00` — that would turn a missing header into a claim about the video.
`videosApi.posterUrlVersioned(id, updatedAt)` mirrors `imagesApi.thumbnailUrlVersioned`:
the poster URL is keyed by id alone, so a regenerated or renamed poster would otherwise
serve stale from cache.

## VideoStrip

**`components/gallery/VideoStrip.tsx`** — a collapsible horizontal strip above the image
grid, keyed on `["videos", datasetId]` (already invalidated after upload and rescan).
Renders `null` when the dataset has no videos, so an image-only dataset looks untouched.
Collapse state persists per dataset under `VIDEO_STRIP_COLLAPSED_KEY`, and is **re-read
from that key whenever `datasetId` changes**, in the same render-adjust as the selection
clear below. `GalleryPage` is not remounted on a dataset change, so a lazy `useState`
initializer alone would show dataset A's collapse state on dataset B — and stickily, since
`toggle` writes back the key it read. Cards show the poster (or a `Film` glyph), a duration
badge and the filename, and open the detail view via `usePaneNavigate`.

A poster that 404s is remembered **by URL, not by mount**: `VideoCard` stores the failed
`posterUrlVersioned(...)` string rather than a boolean, so the glyph shows for that URL
only. An extraction backfills a poster and bumps `updated_at`, which changes the URL, and
the card retries without a reload. The comparison is the reset — no render-adjust needed.

It is mounted in `GalleryPage` **outside** the `<DndContext>` and outside the grid's
scroll container, and that placement is load-bearing: inside the context the cards would
join the grid's collision detection and its subfolder drop targets, and inside the
container they would sit under the drag-to-upload handler.

**Selection is local `useState<Set<string>>`, deliberately not `selectionStore`.** That
store is image-typed down to `datasetByImageId`, so mixing video ids into it would corrupt
`SelectionToolbar`'s cross-dataset breakdown and every bulk-op call site that reads it. The
shift-click range algorithm is ported from `GalleryPage.handleSelect` — anchor plus
last-range-end, so dragging the end backwards deselects what it passes — but resolved
against the strip's own `videos` order and its own set. The port also changed the *trigger*,
deliberately: the strip has no `isCheckbox` distinction at all, so shift on the card body
extends the range as well as shift on the checkbox. In the grid the card body is a plain
toggle and only the checkbox ranges (`docs/dev/gallery.md`). `docs/video.md` documents the
strip's behaviour for users. With a selection, the header grows
`N selected · Extract frames · Clear`, and the selection is cleared when `datasetId`
changes (adjusted during render, so a stale selection is never painted; the anchors are
left alone because an id from the previous dataset simply misses `indexOf`).

### Deleting from the strip

The selection bar carries `N selected · Extract frames · Delete · Clear`, and `Delete`
opens a `ConfirmDialog` stating the Phase 0 contract — the videos and their posters go, the
extracted frames keep their files and lose only the link back. Deliberately **without** a
frame count: `VideoDetailPage` gets one from `GET /videos/{id}/frames-summary`, but a bulk
confirm would need one request per video to say the same sentence. The mutation is a
sequential loop over `videosApi.delete` — there is no bulk route, a strip-sized selection is
a handful of clips, and each `DELETE` runs its own `refresh_stats`. It never rejects: both
halves come back, successes are counted into one toast and the first failure's
`apiErrorDetail` is shown verbatim so a 409 from a locked file (`docs/dev/video-endpoints.md`
§ Locked files) reaches the user with its wording intact. What failed stays selected. The
invalidations mirror `VideoDetailPage`'s `deleteMutation` exactly — `["videos", datasetId]`,
`["datasets"]`, `["dataset-stats", datasetId]`, `["images", datasetId]`, `["image"]`,
`["video-frames"]`, plus `removeQueries(["video", id])` and
`clearPersisted(videoExtractJobKey(id))` per deleted id.

**The Delete key belongs to the image selection whenever there is one.** The strip's
`keydown` effect carries `SelectionToolbar`'s guards (text fields and contentEditable, any
open modal) plus two of its own: a focused `VIDEO` element owns its keys, and the effect
stands down entirely when `useSelectionStore((s) => s.count) > 0`. That last rule is what
keeps one keypress from opening two confirms — with both kinds selected, Delete keeps its
existing image meaning. `VideoDetailPage` carries the same binding and the same precedence
rule, since a split view can have both pages live at once. `frontend/e2e/video-delete.spec.ts`
pins both the button and the precedence.

**The card is a `<div role="button" tabIndex={0}>`, not a `<button>`.** A checkbox is an
interactive control and cannot legally nest inside a button, so Enter/Space are
re-implemented by hand. This is **not** `ImageCard`'s shape — that is a bare `<div>` with no
`role`, no `tabIndex` and an unlabelled checkbox wrapper. `VideoStrip` invented a more
accessible one, which is fine, but do not cite the grid as its precedent. It also carries an
explicit `aria-label={video.filename}`: with `role="button"` the name comes from contents, so
without it the card computes as *"Select clip.mp4 0:02 clip.mp4"*. The checkbox reuses
`GalleryCheckbox` at `uiPrefsStore.galleryCheckboxSize` so the strip and the grid read as
one selection model, and it `stopPropagation`s so selecting does not navigate.

## VideoDetailPage

**`pages/VideoDetailPage.tsx`** — player, metadata grid, inline rename, read-only
provenance via `LicenseBadge`, the extraction history, an "Extract frames" button and
delete. No crop, upscale, LUT, detection or caption: those belong to the frames. Prev/next
needs none of the gallery's nav-context plumbing — `["videos", datasetId]` is a single
unpaginated query, so the page indexes into it directly and `gallery-nav-*`, `injectNavId`
and the boundary prefetches do not apply. The arrow-key handler carries the usual
active-pane and text-field guards **plus** one for `VIDEO` focus (the browser binds arrows
to seek there) and one for each open modal.

**Extracted frames** reads `["video-frames", videoId]` (`GET /videos/{id}/frames-summary`)
and renders one row per subfolder group, newest extraction first, each deep-linking the
gallery at that subfolder. The whole section is hidden at `total === 0` — an empty panel
implies something failed. A `JobProgressBar` appears above the button whenever a
`video_extract` job is live for this video, with no modal open, because the job is derived
from `jobStore` rather than owned by the window that started it.

The delete confirmation states the Phase 0 contract explicitly, now with a count from the
same summary: *"N extracted frame(s) keep their files but lose their link back to this
video."* It falls back to the count-free wording at zero. `DELETE /videos/{id}` never
touches `Image` rows and that is not what a user expects, so the number is what makes the
sentence a fact rather than a claim.

### Releasing the player, and the unplayable overlay

`playerReleased = showDeleteConfirm || renameMode` **unmounts** the `<video>` and shows the
poster in its place. Unmounting rather than clearing `src`: an empty `src` leaves the
element attached and fires a spurious `error` event into the overlay below, while unmounting
aborts the request outright. That request is the problem — Firefox holds it open under
`preload="metadata"` and Starlette keeps the file open for the whole body send, so on
Windows the app's own handle is what makes the delete or rename fail
(`docs/dev/postmortems/PM-021-served-file-handle-blocked-windows-delete.md`). The backend
retry covers only the teardown race; this covers the rest. Cancelling the dialog remounts
the player. `FileBrowserPage`'s `PreviewPanel` takes the same `released` prop, set whenever
any modal is open (`docs/dev/file-browser.md`).

`utils/videoPlayback.ts` is the shared classifier. `playbackErrorMessage(el, {filename,
codecLabel})` reads `el.error.code` and returns **null for `MEDIA_ERR_ABORTED`** — the
release above produces exactly that, and an overlay there would accuse the app of a failure
it performed itself — a network message for code 2, and for decode/unsupported the real
explanation: the browser has no decoder, and probing, posters, shot detection and frame
extraction all still work on the file. Firefox renders those two codes as *"the file is
corrupt"*, which is its own string and not a fact about the bytes. `VideoDetailPage` stores
the result on `onError` (reset on `video.id` change) and renders
`components/video/UnplayableOverlay.tsx` over the poster; the sibling
`browserPlaybackHint(codecLabel, filename)` predicts the same failure from stored metadata
and renders a note under the Video Info panel's Codec row, so the warning is visible *before*
anyone presses play. The hint flags `.mkv`/`.avi` containers and the codecs no browser
carries (HEVC, ProRes, MPEG-4 part 2, Motion JPEG, Windows Media, Theora); AV1 is
deliberately absent, since current browsers decode it in `.mp4`/`.webm` and an `.mkv`
carrying it is already caught by the container rule.

Route registration follows the six-site pattern in `docs/dev/panes-routing.md`
(§ Route-level code splitting). The `/datasets/:id/video/:vid` regex in `routeToView` sits
**above** the generic `dsPageMatch`, same hazard as the image regex: the generic pattern
also matches and would yield an invalid `page: "video"`. `video-detail` is deliberately
absent from `PaneHeader.PAGE_OPTIONS`, exactly as `image-detail` is — the dropdown cannot
supply a `videoId`.

## Frame lineage and the gallery deep link

`ImageDetailPage` renders a *"From **clip.mp4** · 4:31 · shot 12"* row whenever
`image.source_video_id` is set, linking to the video detail view. The filename comes from a
`["video", source_video_id]` query — the same key `VideoDetailPage` uses, so following the
link is a cache hit — enabled on the id being present. When the video is deleted the id
goes NULL and the row disappears; the timestamp and shot index survive on the row but have
nothing to caption. Without this line, a frame moved out of its extraction subfolder can no
longer say where it came from.

The history panel links into the gallery through **`PaneView.subfolder`**
(`docs/dev/panes-routing.md`) and `usePaneGallerySubfolder()` — the pane view inside a pane,
`useSearchParams().get("subfolder")` outside, and never a `??` chain across the two:
`usePaneNavigate`'s `go` sets the view *or* navigates, never both, so a URL param left over
from before the split would hand this pane its neighbour's deep link and reset this gallery
to page 1. A subfolder is a filter, not an identity, which is why there is no route segment
for it. `GalleryPage` applies it during render, recording the last applied value so it
fires on arrival and on a *change* of the incoming value but never fights a user who then
clicks a different subfolder in the sidebar. `undefined` means "no link asked for
anything", which is why the record is the value and not a boolean: `""` is a real target.

**The lineage filter** is that mechanism's sibling, answering what the subfolder link
cannot: curation moves and re-files frames, so the extraction subfolder stops being a
handle, while `Image.source_video_id` does not move. `PaneView.sourceVideoId` and
`usePaneGallerySourceVideo()` mirror `subfolder` / `usePaneGallerySubfolder()` exactly,
except the routed-mode query param is `source_video_id` and `""` carries no meaning (an opaque
uuid). `GalleryPage` holds it as `frameVideoId` and applies an incoming link during render
against an `appliedVideo` record — **clearing `activeSubfolder` when it does**. That is
load-bearing: arriving via `?source_video_id=` leaves `linkedSubfolder` undefined, so a
subfolder restored from `gallery-state-${datasetId}` would silently intersect the filter and
show an empty grid. Lineage spans subfolders; that is the point. The backend half — the
`source_video_id` param's plain indexed equality and its truthiness gate — is one row of
`docs/dev/image-filters.md`.

The clear runs in **both** directions: the subfolder branch likewise clears `frameVideoId`,
because a lineage filter restored from that same key would intersect the linked subfolder
and the history panel's own "N frames" row would open an empty grid — the very count it had
just named. `frameVideoId`'s `useState` therefore sits above both render-adjust blocks; from
its old position below them the subfolder branch's clear would be a TDZ read.

**Both branches also suppress the scroll restore**, via a shared `dropScrollRestore()` that
marks the restore done and queues a scroll to top for the effect to apply once the new page
has rendered (a DOM write during render would fire before the rows exist). The saved offset
belongs to the list the user left, so replaying it onto a freshly filtered page 1 lands the
user in the middle of a different result set, or nowhere at all if it is shorter. Every
other filter change routes through the same helper — `resetPage`, the search and
detection-label debounces, `handleResetFilters` — which is why the restore effect keys on the
`images` array identity rather than its length: with `keepPreviousData` a same-length result
set would otherwise leave the queued scroll armed for an unrelated later load.

A **stale-id guard**, derived during render like `appliedSubfolder`, drops `frameVideoId`
once `["videos", datasetId]` resolves without a match — otherwise a deleted video leaves a
permanently empty gallery behind a blank `<select>`, the problem `licenseFilter`'s
vocabulary bounds-check solves. That `<select>` renders only when the dataset has videos and
reuses `VideoStrip`'s query cache rather than fetching again; see `docs/dev/gallery.md`
§ Gallery filters for its state and persistence.

Two entry points feed it: a **"Show all N frames"** row above `VideoDetailPage`'s
per-subfolder rows (which keep their `?subfolder=` links — "where did this extraction land"
is a different question), and a small **"all frames"** link on the `ImageDetailPage` lineage
row, the reverse-direction affordance that lets a moved frame find its siblings.

## Elsewhere

`DatasetsPage` shows a video pill in the card footer and a `N vid` entry in the compact
row, both hidden at zero and both changed together. `FileBrowserPage` renders a `<video
controls preload="metadata">` in its preview panel for a `media_kind === "video"` entry
and skips the image-only `["fs-image-meta", path]` query for it.

`frontend/e2e/video-extract.spec.ts` drives the whole path — strip, checkbox, detail view,
both modal steps (`docs/dev/video-extract-ui.md`). Its mp4 fixture is inlined as base64 in
`e2e/helpers.ts::mp4Buffer()`, following `pngBuffer`'s precedent. Most cases stop short of
submitting, the same "never click the expensive button" convention as `quality.spec.ts` — among
them the subfolder `<select>`'s option set changing with the mode (via
`data-testid="extract-subfolder"`, since the `Subfolder` label is not associated with it), and
a `page.route`-failed probe leaving `Next` enabled with the note visible.

**One journey does submit**, and it retired the reasoning that said none could.
`an extraction runs end to end and its frame appears with lineage` clicks *Extract from 1
video*, waits on `data-testid="extract-running"` and then on the settled "Finished or no longer
reporting" label, and goes on to assert the extraction-history panel, the `?source_video_id=`
gallery deep link and the frame's lineage line — four surfaces for one extraction. The earlier
claim that CI could not reach it was wrong on its premise: `shot_detection: false` does not
block a run, it makes `detect_shots` fall back to `_uniform_shots`, which is pure arithmetic
over the clip's span and needs only opencv — which the e2e workflow installs. The 2 s fixture
yields one window and the run finishes in about a second. Nothing there asserts a shot *count*,
because a dev machine with `scenedetect` installed runs the real detector and may find more.
What still has **no** automated coverage is narrower than the old sentence claimed: the union of
`result.jobs` and `liveJobs` across a **mixed batch**, since this run is single-video and the
stubbed specs return `{"jobs": [], "skipped": []}`. This repo has no frontend unit tests (no
vitest; the gates are `tsc -b`, eslint and Playwright).
