# Pass 2 re-extraction UI

Covers `ReextractFramesForm` and the `ReextractFramesModal` that wraps it, the three entry
points that open it, the `jobStore` adoption that lets a reopened dialog re-attach to a
running job, and the `TopBar` invalidations a finished `video_reextract` fires. This is the
UI counterpart of `docs/dev/video-reextract.md`, which documents the contract, the
`/videos/reextract` endpoints, `render_at_timestamps`, the `video_reextract` job and the
extension change these controls sit in front of; the § Tests section there indexes this
half's e2e coverage too. Pass 1's own modal stands in exactly this relation to its backend —
`docs/dev/video-extract-ui.md` and `docs/dev/video-extract.md` — and the two UIs are
deliberately unlike each other in the two places noted below. The user-facing account of
re-extraction is `docs/video.md`.

## ReextractFramesForm

`ReextractFramesForm` follows `UpscaleForm`/`CropToDetectionForm` — props
`{ datasetId, imageIds?, videoId?, subfolder?, onSuccess?, onCancel? }`, owning its own API
call, job ids and invalidation. On mount it calls the preview endpoint and renders the
accounting grouped by reason, so 300 identical skips read as one line. The accounting branch
is **error → no data → content**, never `isLoading → content`: TanStack v5's default
`networkMode: "online"` leaves an offline tab `pending` *and* `paused` — `isLoading` false
with `data` undefined — which sent the old chain into the content branch and dereferenced
`preview!`, taking out the pane's ErrorBoundary. `!preview` subsumes the loading state
anyway. Controls: a
JPEG/PNG radio, an optional max long edge (empty = native) and an optional job label. The
stale-scores note sits above the submit button and is repeated in the completion toast built
from `result_data`. Max long edge is validated client-side against the server's `ge=64,
le=16384` (empty stays valid, meaning native): submit is disabled with the bound shown
inline, rather than letting `30` reach the API and return a raw 422 toast. Job tracking uses
`SelectionToolbar`'s `detectJobIds` array shape — one job per video, so several ids.

## Re-attaching to a running job

**Closing mid-run is safe, and re-attach needs no persisted key.** An adoption effect folds
every live `video_reextract` job whose `video_id` is one of the *preview's* `groups[].video_id`
into `jobIds`, so a reopened dialog gets the progress pill, the `result_data` toast and the
invalidations back. The preview writes nothing, which is what makes the scope's videos known
before anything is started. Two shapes here are load-bearing:

- **Adopted into state, not derived.** The completion effect reads `activeJobs.get(jobId)` and
  needs the id to *stay* tracked once the job goes terminal; a `trackedIds` list filtered on
  non-terminal would drop it at the exact moment the toast, the invalidations and `onSuccess`
  are owed — `ExtractProgressList`'s vanish-on-complete failure (`docs/dev/video-extract-ui.md`).
  There is no loop either: the completion effect only removes terminal ids and adoption only
  adds non-terminal ones.
- **No persisted key**, unlike pass 1's `` `video-extract-job-${videoId}` ``. Pass 2 emits per
  frame, so a reconnected SSE stream repopulates `jobStore` within a frame; pass 1 needed a
  stored id because its `detecting`/`replacing` phases are long and silent. The "three
  sanctioned exceptions" framing in `docs/dev/persistence.md` therefore stands unchanged.

Two accepted consequences, noted in the code rather than engineered away: adoption waits one
preview round trip (the pill appears a moment after open), and an adopted job completing fires
`onSuccess`, which closes the modal at all three call sites — the pre-existing behaviour.

## ReextractFramesModal

`ReextractFramesModal` wraps it for all three entry points: `useModalBehavior` (Escape, Tab
cycling, focus return, `role="dialog"`), the overlay and `.card` panel, a `title` and an
optional `headerExtra` slot — `SelectionToolbar`'s dataset breakdown, `VideoDetailPage`'s
`{filename} · {subfolder}` line. A component rather than a hook call per page because the
hook must not be called conditionally and every entry point renders behind a flag;
`useModalBehavior`'s docstring rules out a *generic* wrapper, so a feature-specific one is
the sanctioned shape. Backdrop-click closing stays off, matching `ExtractFramesModal`
(`docs/dev/video-extract-ui.md`) — the sibling on the same page — because a stray overlay
click should not dismiss a dialog with a run in flight. Escape and a header ✕ *do* close at
any time, and the Cancel button becomes `Close` rather than going disabled while running:
all three are safe now that the form re-attaches. Escape used to be the only exit mid-run,
and taking it destroyed the only tracking that existed.

## The three entry points

Three entry points, all opening that one modal:

- **`SelectionToolbar`** — rendered unconditionally like the other thirteen actions rather
  than gated on lineage. The store holds ids only (`selectedIds` + `datasetByImageId`) and a
  selection can span pages and datasets, so any client-side lineage gate would be wrong for
  exactly the selections that matter; the preview endpoint does the honest accounting
  instead. The flag joins `anyModalOpen` so the Delete-key handler stays suppressed.
- **`ImageDetailPage`** — a `re-extract` button on the existing lineage row, scoped to that
  one image. Its flag joins `showCropDetect` in `formModalOpen`, which suppresses **both**
  window-level key handlers: without it ArrowLeft/Right navigated the page underneath the
  open dialog, and since the form is passed `imageIds={[imageId]}` from the route the
  preview silently re-queried for a different image while the dialog still named the old
  one.
- **`VideoDetailPage`** — a per-row action on the extraction-history panel, scoped by
  `{videoId, subfolder}`, which is the only scope that panel has and the reason the request
  accepts it. `null` is closed and `""` is the dataset root, a real subfolder.

## What TopBar invalidates

`ExtractFramesModal` and `useVideoExtractJobs` (`docs/dev/video-extract-ui.md`) are **not**
involved: that hook filters `job_type === "video_extract"` and carries the persisted-id
machinery pass 2 does without, per the asymmetry above. `TopBar` carries `video_reextract` in
both `LIVE_IMAGE_JOB_TYPES` and `IMAGE_MODIFYING_JOB_TYPES`, and on a terminal event
invalidates the singular `["image"]` key — otherwise an open detail pane keeps showing the
triage dimensions and thumbnail — plus `["video-frames", video_id]`, because the dialog can be
closed mid-run and nothing else would refresh `VideoDetailPage`'s extraction-history panel,
plus `["duplicates", dataset_id]`, because the job re-derives `phash` from the
full-resolution frame and a stale duplicate grouping is the one thing pass 2 *does* change
about a curation decision the user already made. Still no subfolder or `["video", id]`
invalidation: pass 2 creates no subfolder and touches no `Video` row, it only rewrites the
frames that row lists. The membership in `LIVE_IMAGE_JOB_TYPES` fills `["images", ds]`
mid-run; unlike `video_extract` it adds no `["subfolders", id]` there, for the same reason.
The full invalidation table is in `docs/dev/frontend-jobs.md`.

`TERMINAL_JOB_STATUSES` comes from `constants/jobs.ts`, shared with `TopBar` and
`useVideoExtractJobs` — three call sites is past the threshold for a local copy, and a copy
that misses a fourth status is how one of the three silently disagrees.
