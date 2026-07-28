# Video frontend surfaces

Covers every screen that shows or acts on a video: the gallery's `VideoStrip` and its
selection, `VideoDetailPage`, the two-step `ExtractFramesModal` with `CropOverlay` and
`TrimBar`, the extraction history panel, the frame-lineage line on `ImageDetailPage`, and
the job re-attach hook they share. The backend behind them is in `docs/dev/video.md`
(model, storage, `/videos` endpoints) and `docs/dev/video-extract.md` (the probe/extract
endpoints and the `video_extract` job).

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
Collapse state persists per dataset under `VIDEO_STRIP_COLLAPSED_KEY`. Cards show the
poster (or a `Film` glyph), a duration badge and the filename, and open the detail view
via `usePaneNavigate`.

It is mounted in `GalleryPage` **outside** the `<DndContext>` and outside the grid's
scroll container, and that placement is load-bearing: inside the context the cards would
join the grid's collision detection and its subfolder drop targets, and inside the
container they would sit under the drag-to-upload handler.

**Selection is local `useState<Set<string>>`, deliberately not `selectionStore`.** That
store is image-typed down to `datasetByImageId`, so mixing video ids into it would corrupt
`SelectionToolbar`'s cross-dataset breakdown and every bulk-op call site that reads it. The
shift-click range algorithm is ported from `GalleryPage.handleSelect` — anchor plus
last-range-end, so dragging the end backwards deselects what it passes — but resolved
against the strip's own `videos` order and its own set. With a selection, the header grows
`N selected · Extract frames · Clear`, and the selection is cleared when `datasetId`
changes (adjusted during render, so a stale selection is never painted; the anchors are
left alone because an id from the previous dataset simply misses `indexOf`).

**The card is a `<div role="button" tabIndex={0}>`, not a `<button>`.** A checkbox is an
interactive control and cannot legally nest inside a button, so Enter/Space are
re-implemented by hand — the same shape `ImageCard` uses. The checkbox reuses
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

Route registration follows the six-site pattern in `docs/dev/panes-routing.md`
(§ Route-level code splitting). The `/datasets/:id/video/:vid` regex in `routeToView` sits
**above** the generic `dsPageMatch`, same hazard as the image regex: the generic pattern
also matches and would yield an invalid `page: "video"`. `video-detail` is deliberately
absent from `PaneHeader.PAGE_OPTIONS`, exactly as `image-detail` is — the dropdown cannot
supply a `videoId`.

## ExtractFramesModal

**`components/video/ExtractFramesModal.tsx`**, props `{ datasetId, videos: Video[],
onClose }` — batch-capable from the start; the two entry points (the strip's selection
action and the detail page's button) differ only in the array they pass. It follows the
newer modal idiom: a fixed overlay around `<div className="panel">` with `panel-h`/`panel-b`,
spreading `useModalBehavior({ onClose, label: "Extract frames" })`. `closeOnBackdrop` stays
**off** — this modal holds unsaved probe decisions, and a stray overlay click would discard
a crop the user spent time dragging.

**Step 1 — Source.** Probes `videos[0]` only (a batch applies one parameter set across the
series, so the header says *"Previewing X — these settings apply to all N videos"*), via
`POST /videos/{id}/probe` on a `["video-probe", id, trimStart, trimEnd]` key. Trim changes
are debounced ~400 ms before they reach that key, so a handle drag is not a seek storm. The
last successful result is kept in state and adjusted during render, so a 504 leaves the
previous samples on screen and only raises a toast. The filmstrip renders each sample's
`data:image/jpeg;base64,…` `data_url`; clicking one promotes it into the `CropOverlay`.
Below it: the deinterlace toggle (disabled with the server's own 503 text when
`capabilities.deinterlace === false`), the `TrimBar`, and a warnings block rendering
`probe.warnings` verbatim plus the `interlace`/`telecine` booleans — telecine carries the
honest note that it is *detected, not corrected*, since only bwdif ships. `crop_confidence`
sits next to **Use detected** so a 30 % agreement does not read as certainty, and
`samples_failed`/`truncated` surface as inline notes rather than toasts, because partial
results are normal for a broken tail.

**Step 2 — Extract.** Pick (`frames_per_shot`, `pick`, `candidates`), output (`long_edge`,
mode, subfolder) and `sensitivity`, with `min_shot_ms` / `detector_frame_skip` / `max_shots`
inside a collapsed `<details>` — those are the cost-cliff levers, not first-run controls.
The mode radio defaults to **new_subfolder**; when the summary reports previous frames the
labels carry the numbers (*Add to `{last}`* / *New subfolder* / *Replace (deletes N previous
frames)*, the last styled destructively, and reading *"deletes each video's previous
frames"* for a batch, where one number would be wrong). `capabilities.shot_detection ===
false` shows a standing warning that frames will be sampled at fixed intervals — the
uniform-fallback disclosure is a user-facing contract (`docs/dev/video-extract.md`), and the
job announces it over SSE too. The subfolder control follows `CropToDetectionForm`'s
tri-state shape (a `<select>` over `["subfolders", datasetId]` plus a `__custom__` sentinel
and a free-text input); the automatic option omits `subfolder` entirely so the router
derives the slug. It is hidden in `replace` mode, where the router ignores it and uses the
previous subfolder.

**Running view.** Submit → `videosApi.extract(body)` → one row per returned job: filename,
target subfolder, a phase-labelled `JobProgressBar`, and a cancel ✕ wired to
`jobsApi.cancel` (which is `DELETE /jobs/{id}` — there is no POST route). `skipped` entries
render as an amber "already extracting" row. Closing is safe and the panel says so.

### CropOverlay and TrimBar

**`CropOverlay`** — `{ src, frameW, frameH, rect, onChange }`. Draws the sample frame, four
shaded mattes outside the rect (not one outlined box: the matte is what shows how much is
being thrown away) and four draggable edge handles. Pointer events with
`setPointerCapture`, never mouse events, so a drag that leaves the element keeps tracking
and still ends. Handles move in **frame** coordinates (`scale = displayedWidth / frameW`,
re-measured by a `ResizeObserver`), clamp to the frame and **snap to even numbers**,
mirroring `video_frames.clamp_crop` so the rect shown is the rect stored. The handles are
`aria-hidden`; the paired numeric x/y/w/h inputs beneath are the keyboard path, because
there is no honest ARIA pattern for a 2-D rect. **Use detected** re-applies `probe.crop`
and **Clear crop** sets it to null, which sends `clear_crop: true` — the disambiguation
`VideoExtractRequest` exists for. The rect sent is only a proposal: the server normalizes
again and stores that, so a later re-extraction replays the stored value.

**`TrimBar`** — `{ durationMs, startMs, endMs, onChange, disabled }`. Note the backend's
semantics: `trim_end_ms` is **milliseconds cut off the tail**, not an end position
(`end = duration_ms - trim_end_ms`), so a clip whose duration is corrected later keeps
trimming the same amount of tail. This renders the right handle at `duration - trim_end_ms`
and converts back on the way out. Disabled with an explanation when
`duration_source === "unknown"` — the backend already warns that the tail trim is
unavailable for a non-seekable container, and a control that does nothing is worse than
none.

### Re-attaching to the job

**`hooks/useVideoExtractJobs.ts`** — `useVideoExtractJobs(videoIds, startedJobIds)` returns
a `Map<videoId, JobProgress>` of live extractions, and both the modal and
`VideoDetailPage` use it, so they show the same bar for the same run with no coordination.
Three details are load-bearing, all inherited from `GeneratePromptsModal`
(`docs/dev/comfy-prompts.md`):

- **No `useJobSSE`.** `useAllJobsSSE` in `TopBar` already holds every event; a
  component-scoped subscription would re-create the component↔job coupling this pattern
  exists to remove.
- The job is **derived from `jobStore`, never mirrored into state** — matched on
  `job_type === "video_extract" && video_id === v.id` and not terminal, with the ids from
  the extract response as the fallback for the window before the job's first emit (the
  queue's own pending/running events carry no `video_id`).
- The persisted id per video, `` `video-extract-job-${videoId}` `` (a sanctioned
  component-local key — see `docs/dev/persistence.md`), is read by the **recovery** effect,
  which is declared *before* the persist effect. Effects run in declaration order, so
  recovery sees the stored id before the persist write can replace it with `null`.

`extractPhaseLabel(job)` turns `progress.phase` into a stage label, because the generic
done/total counts frames and says nothing during the long detection phase.

## Frame lineage and the gallery deep link

`ImageDetailPage` renders a *"From **clip.mp4** · 4:31 · shot 12"* row whenever
`image.source_video_id` is set, linking to the video detail view. The filename comes from a
`["video", source_video_id]` query — the same key `VideoDetailPage` uses, so following the
link is a cache hit — enabled on the id being present. When the video is deleted the id
goes NULL and the row disappears; the timestamp and shot index survive on the row but have
nothing to caption. Without this line, a frame moved out of its extraction subfolder can no
longer say where it came from.

The history panel links into the gallery through **`PaneView.subfolder`**
(`docs/dev/panes-routing.md`) and `usePaneGallerySubfolder()`, written exactly like
`usePaneVideoId` except the fallback is `useSearchParams().get("subfolder")` rather than a
route param — a subfolder is a filter, not an identity, so there is no route segment for
it. `paneGo` writes both, so pane state wins in split view and the query string covers the
routed case. `GalleryPage` applies it during render, recording the last applied value so it
fires on arrival and on a *change* of the incoming value but never fights a user who then
clicks a different subfolder in the sidebar. `undefined` means "no link asked for
anything", which is why the record is the value and not a boolean: `""` is a real target.

## Elsewhere

`DatasetsPage` shows a video pill in the card footer and a `N vid` entry in the compact
row, both hidden at zero and both changed together. `FileBrowserPage` renders a `<video
controls preload="metadata">` in its preview panel for a `media_kind === "video"` entry
and skips the image-only `["fs-image-meta", path]` query for it.

`frontend/e2e/video-extract.spec.ts` drives the whole path — strip, checkbox, detail view,
both modal steps — and closes **without submitting**, the same "never click the expensive
button" convention as `quality.spec.ts`. Its mp4 fixture is inlined as base64 in
`e2e/helpers.ts::mp4Buffer()`, following `pngBuffer`'s precedent.
