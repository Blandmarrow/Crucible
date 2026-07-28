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
are debounced ~400 ms before they reach that key, so a handle drag is not a seek storm; each
entry holds ~350 KB of base64 and a drag mints several, which is why the query carries an
explicit `gcTime: 60_000` instead of the 5-minute default. The last successful result is kept
in state and adjusted during render, so a 504 leaves the previous samples on screen and only
raises a toast. The filmstrip renders each sample's `data:image/jpeg;base64,…` `data_url`;
clicking one promotes it into the `CropOverlay`. Below it: the deinterlace toggle, the
`TrimBar`, and a warnings block rendering `probe.warnings` verbatim plus the
`interlace`/`telecine` booleans — telecine carries the honest note that it is *detected, not
corrected*, since only bwdif ships. `crop_confidence` sits next to **Use detected** so a 30 %
agreement does not read as certainty, and `samples_failed`/`truncated` surface as inline
notes rather than toasts, because partial results are normal for a broken tail.

**A probe is a preview, not a prerequisite.** `POST /videos/extract` needs none, so `Next` is
gated on `probeQuery.isPending && !probe` — mirroring the "Sampling the video…" note, and
settling immediately on error because the query is `retry: false`. Gating it on `!probe`
instead made a video that will not sample permanently un-extractable. A note names exactly
what is lost (crop preview, detected matte, interlace/telecine warnings); capability warnings
are unaffected because they come from their own route.

**Capabilities come from `["extract-capabilities"]`** (`GET /videos/capabilities`, long
`staleTime`) in preference to `probe.capabilities`, for the same reason. `effectiveDeinterlace`
is then derived — `caps.deinterlace === false ? "" : deinterlace` — and drives both the
checkbox's `checked` and the request body. Without it a row carrying `"bwdif"` from an earlier
run submits it from behind a *disabled* checkbox and takes the endpoint's 503: the one case
`extract_frames`' own `effective` check cannot cover, because the request does send the field.
A warning discloses that running now also clears the saved setting.

**Untouched decode fixups are omitted from the request.** `crop`/`clear_crop`, `deinterlace`
and both trims are seeded from the **primary** video but written by the endpoint to **every**
video in `to_run`, so sending an unchanged control wiped the rest of the batch's stored
settings — opening a batch whose primary carried no crop and pressing Extract cleared every
other video's rect. Three `*Touched` flags gate the fields; the API is built for this, since
`None` means "leave the row alone" for all four. The single exception is a coerced
`deinterlace`, which is sent even untouched — that is the only way off a stale `"bwdif"` this
host cannot run. The batch header line says so: each control applies to the batch *only if you
change it*.

**Step 2 — Extract.** Pick (`frames_per_shot`, `pick`, `candidates`), output (`long_edge`,
mode, subfolder) and `sensitivity`, with `min_shot_ms` / `detector_frame_skip` / `max_shots`
inside a collapsed `<details>` — those are the cost-cliff levers, not first-run controls.
The mode radio defaults to **new_subfolder**; when the summary reports previous frames the
labels carry the numbers (*Add to `{last}`* / *New subfolder* / *Replace (deletes N previous
frames)*, the last styled destructively, and reading *"deletes each video's previous
frames"* for a batch, where one number would be wrong). `capabilities.shot_detection ===
false` shows a standing warning that frames will be sampled at fixed intervals — the
uniform-fallback disclosure is a user-facing contract (`docs/dev/video-extract.md`), and the
job announces it over SSE too. The *Add to…* label is batch-aware: `framesSummary` is the
primary's history while the router resolves `previous` per video, so a batch names no folder.

**The subfolder control means different things per mode, so it renders differently per
mode.** It follows `CropToDetectionForm`'s tri-state shape (a `<select>` over
`["subfolders", datasetId]` plus a `__custom__` sentinel and a free-text input), hidden in
`replace` mode where the router ignores it. The existing-subfolder options appear in `add`
mode **only**: `new_subfolder` runs whatever name it is given through `_step_subfolder`, so
picking an existing folder there silently becomes `{name}_2`. The *Automatic* label likewise
differs — a new folder named after the video in one mode, *this video's previous subfolder* in
the other, since that is what the router does with an empty `subfolder` in each. Two residual
surprises are disclosed inline rather than engineered away: a typed name in `new_subfolder`
mode still steps across a batch (point users wanting one shared folder at `add` + a name), and
the dropdown filters `sf.path !== ""`, so the dataset root is reachable only through `add` +
Automatic.

The restriction is enforced by a **derived `effectiveSubSelect`** — a non-sentinel value
coerced to `SUB_AUTO` when `mode === "new_subfolder"` — read by both the `<select value>` and
`resolvedSubfolder()`. Not a reset inside the radio's `onChange`: derive-during-render is the
idiom this file already uses for `lastProbe` (and `VideoStrip` for `selectionFor`), it mirrors
`effectiveDeinterlace`, and it preserves the add-mode choice across a round trip. A reset is
also not reliable — a `<select>` whose value matches no option renders *blank* while state
still holds the old path, which is strictly worse than the bug.

**`ExtractProgressList`** — rows of `{ videoId, jobId, filename, subfolder? }` keyed by
**video id**, each with a phase-labelled `JobProgressBar` and a cancel ✕ wired to
`jobsApi.cancel` (`DELETE /jobs/{id}` — there is no POST route). Three things about it:

- **`rows` is a union keyed by `video_id`** — `result.jobs` first (it alone carries `filename`
  and the resolved `subfolder`), then any `liveJobs` entry belonging to this modal (the hook
  returns every live extraction in the app, so the membership check is load-bearing; and a
  live payload never overwrites a `result` row, which knows strictly more). "Result if present,
  otherwise liveJobs" breaks a mixed batch: submit A+B+C with A already busy and A's live bar
  vanishes the instant the response lands, leaving only an amber `skipped` line for the video
  working hardest. A `skipped` entry that produced a row is suppressed.
- **A row persists once seen**, accumulated in a `useRef` Map. `useVideoExtractJobs` filters
  terminal statuses, so a row derived from it has no other source and would disappear at the
  exact moment the user is watching for an outcome — while a `result` row, whose array is
  terminal-stable, settled into "Finished or no longer reporting" correctly. The asymmetry was
  only visible by letting a real run finish with the modal open. This remembers *rows*, not a
  view mode: the step content stays interactive throughout, so there is still nothing to be
  trapped in.
- The `→ subfolder` span renders only when the field is **present**. A row derived from a live
  job has none, and the old `{j.subfolder || "root"}` would print "→ root" and lie.
- The bar is `live ? Math.max(0, live.percent ?? 0) : 100`. `?? 100` for a live job rendered a
  *full* bar for a queued one, because the queue's `pending` event carries no percent and the
  queue is serial — a 3-video batch showed two 100 % bars labelled "Queued". The clamp stays:
  `JobProgressBar` interpolates `width: ${percent}%` raw, and an invalid `width: -1%` is
  dropped, leaving `width: auto`, i.e. a full bar again.

**Re-attach is a block, not a view swap.** The full-view swap stays gated on `result` alone,
which is terminal-stable; a swap on `liveJobs.size > 0` would empty the view the instant the
run finished (the hook filters terminal statuses) and dump the user into a fresh probe.
Instead, live rows not covered by `result` render the same list **above** the step content —
`GeneratePromptsModal`'s shape, the precedent `useVideoExtractJobs`' own docstring cites.
Reopening over a live job therefore lands on step 1 with a watchable, cancellable bar on top,
and a mixed batch can still be configured for the videos that are not busy: no view state, no
latch, no escape hatch, no vanish-on-complete. `VideoDetailPage`'s button is correspondingly
**not** disabled while a job runs — doing so made this path unreachable from the one entry
point that gated on it, since `VideoStrip` never did.

Two deliberate omissions, both easy to "fix" wrongly. The row's cancel ✕ makes **no**
optimistic `jobStore` write (unlike `TopBar`'s pill), because this block *is* `liveJobs` and
the optimistic write would yank the row away before the backend had cancelled. And no copy
promises a run can always be started: `POST /videos/extract` calls `ensure_not_busy`, so a
second submit landing during another video's `replace` step 409s.

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
- The persist effect writes **on transition only**, tracking the last value per id, and never
  writes `null` for an id it has not yet seen live. `jobs` is a fresh Map on every `activeJobs`
  change — i.e. every SSE event app-wide — so the unguarded form wrote localStorage for every
  watched video on every event, nearly all of them `{jobId: null}` for videos with no job. It
  also raced the recovery effect above: `VideoDetailPage` and an open modal both run this hook
  for the same video, so instance #2's null-write could land inside instance #1's in-flight
  `jobsApi.get` and erase the id it was re-attaching to. Declaration order fixes the ordering
  hazard, not this one.

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
it. `paneGo` does **not** write both: `usePaneNavigate`'s `go` sets the pane view when it is
inside a pane and calls `navigate(url)` when it is not, never both. The fallback chain inside
the hook is what covers the two cases. `GalleryPage` applies it during render, recording the last applied value so it
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
`e2e/helpers.ts::mp4Buffer()`, following `pngBuffer`'s precedent. Two further cases stay
inside that convention: the subfolder `<select>`'s option set changing with the mode (via
`data-testid="extract-subfolder"`, since the `Subfolder` label is not associated with it), and
a `page.route`-failed probe leaving `Next` enabled with the note visible. The progress list
carries `data-testid="extract-running"` so it is addressable, but no test can reach it — CI
has `shot_detection: false`, so a real run is never started. The union logic behind those rows
therefore has **no** automated coverage: this repo has no frontend unit tests (no vitest; the
gates are `tsc -b`, eslint and Playwright).
