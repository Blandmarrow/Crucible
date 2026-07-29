# Pass 1 extraction UI

Covers the two-step `ExtractFramesModal` — its source/probe step with `CropOverlay` and
`TrimBar`, its extract step, the `ExtractProgressList` rows — and `useVideoExtractJobs`,
the hook that re-attaches a reopened window to a running job. This is the UI counterpart
of `docs/dev/video-extract.md`, which documents the probe/extract endpoints and the
`video_extract` job behind these controls; read the two together, since almost every
decision here mirrors one there. The surfaces the modal's two entry points live on — the
gallery's `VideoStrip` and `VideoDetailPage` — are in `docs/dev/video-ui.md`, along with
`frontend/src/utils/duration.ts::formatDuration(ms)` (used by `TrimBar`) and the
`frontend/e2e/video-extract.spec.ts` coverage note in its § Elsewhere. Pass 2 and its
`ReextractFramesForm` are in `docs/dev/video-reextract.md`. The re-attach pattern the hook
inherits from — `GeneratePromptsModal` — is in `docs/dev/comfy-prompts.md`.

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

**A change that changes nothing marks nothing touched.** The crop's three writers — the
overlay's `onChange`, **Use detected** and **Clear crop** — all route through one
`applyCrop(r)`, which sets `cropTouched` only when a null-safe `sameRect` says the rect
actually moved; the `TrimBar` `onChange` compares both values the same way. This is not
defensive tidiness. `cropTouched` decides whether `crop` is sent at all, the modal shows the
**full frame** when no crop is set (`active = rect ?? full`), and `clamp_crop` maps a
full-frame rect to `None`, which `extract_frames` then writes as `crop_* = NULL` to every
video in the batch. So a spurious touch — a tab through a crop field with no typing, a drag
that jitters back to where it started, an arrow press on a handle already at 0 — wiped the
whole batch's stored rects. The guard sits on the flag, not only on the control that would
have tripped it.

**Step 2 — Extract.** Pick (`frames_per_shot`, `pick`, `candidates`), output (`long_edge`,
mode, subfolder) and `sensitivity`, with `min_shot_ms` / `detector_frame_skip` / `max_shots`
inside a collapsed `<details>` — those are the cost-cliff levers, not first-run controls.
The mode radio defaults to **new_subfolder**; when the summary reports previous frames the
labels carry the numbers (*Add to `{last}`* / *New subfolder* / *Replace (deletes N previous
frames)*, the last styled destructively, and reading *"deletes each video's previous
frames"* for a batch, where one number would be wrong). `capabilities.shot_detection ===
false` shows a standing warning that frames will be sampled at fixed intervals — the
uniform-fallback disclosure is a user-facing contract (`docs/dev/video-shots.md`), and the
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

## CropOverlay and TrimBar

**`CropOverlay`** — `{ src, frameW, frameH, rect, onChange }`. Draws the sample frame, four
shaded mattes outside the rect (not one outlined box: the matte is what shows how much is
being thrown away) and four draggable edge handles. Pointer events with
`setPointerCapture`, never mouse events, so a drag that leaves the element keeps tracking
and still ends. Handles move in **frame** coordinates (`scale = displayedWidth / frameW`,
re-measured by a `ResizeObserver`), clamp to the frame and **snap to even numbers**,
mirroring `video_frames.clamp_crop` so the rect shown is the rect stored. The handles are
`aria-hidden`; the paired numeric x/y/w/h inputs beneath are the keyboard path, because
there is no honest ARIA pattern for a 2-D rect — which is why they render `NumberField`
(see below) rather than a bare input: the naive per-keystroke clamp made *the* a11y path
silently lie about what was typed. `clampRect` is projected onto the single field being
edited (`clamp={(n) => clampRect({ ...active, [field]: n })[field]}`) so both of that
component's props stay honest about the cross-field bounds. Editing a field while `rect`
is null still creates a crop from the full frame; that is how this path creates a rect, and
only the *no-typing* case changed. **Use detected** re-applies `probe.crop`
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

**`pointerdown` does not move the handle.** The pointer handlers sit on the two handles
only, so there is no jump-to-click affordance a move-on-press would serve; all it did was
snap the handle to wherever inside its 10 px hit box the press landed, which flipped
`trimTouched` (see the no-op guard above) and minted a fresh probe query key, costing an
8-sample re-probe for a stray click. It takes focus explicitly instead — `preventDefault`
suppresses the focus shift, so without that call a mouse user could never reach the arrow
keys. Those keys `preventDefault` for `ArrowLeft`/`ArrowRight` **only**, so Tab still moves
focus — that is the slider contract rather than a fix for an observed scroll: measured, it
prevents nothing visible, because the arrows scroll horizontally and the modal body has no
horizontal overflow at any tested viewport.

**Both arrow *grow* directions are floored at `Math.max(0, …)`**, matching the pointer path.
This looked unreachable and is not: the endpoint is **looser than the component**. The
pointer path caps the tail so the remaining span never drops under `MIN_SPAN_MS`, but
`extract_frames` refuses only `start + end >= duration`, so `trim_end_ms: 1900` on a 2 s clip
is accepted and stored. Reopening on that row leaves `endPos - MIN_SPAN_MS` negative, and one
press took `trimStart` to -400 and the next submit to a raw 422 on the schema's `ge=0`. Its
sibling — the crossed-trim render below — really is unreachable that way, since the same
check is what makes `startMs > endPos` impossible to store.

**A crossed trim is warned about, never silently clamped.** `trimStart`/`trimEnd` are seeded
from the stored `Video` row while `durationMs` comes from the fresh probe, so the only way
to reach one is a clip whose duration was corrected downward — the case `duration_source`
exists for. The range fill is therefore plain arithmetic floored at 0
(`width: pct(Math.max(0, endPos - startMs))`, not a `calc()` subtraction that goes negative
and renders as overlapped handles with no fill), and the modal derives
`trimStart + trimEnd >= durationMs` — **exactly** `extract_frames`' own condition, so the
copy cannot drift from the 400 it predicts. Clamping instead would be worse either way:
without setting `trimTouched` it fixes the picture and leaves the submit still taking the
400, and with it, it writes the primary's trim across the whole batch.

**`components/common/NumberField.tsx`** — `{ value, clamp, onCommit, …inputProps }`, the
shared number input both controls above are built from (ten call sites; step 2's six
spinners take an `intClamp(lo, hi)` that also rounds, since the schema is `int` and a typed
`1.5` used to reach the API). Every field here used to re-clamp `Number(e.target.value)` on
each keystroke, which rewrites the prefix you are still typing: `2048` into **Long edge**
arrived as `8192` — `"2"` clamps up to 64 and the remaining three digits append — and the
crop's even-snap turned `150` into `250`. So the raw string is held in a `draft` and clamped
on blur, with one refinement: **it commits live whenever clamping would be the identity**,
so a consumer that paints from the value (the crop mattes) keeps moving for every keystroke
that is not a lie. Four details are load-bearing:

- **Commit is a no-op when `draft === null`.** This is what stops a focus-and-tab with no
  typing from firing `onCommit` and tripping `cropTouched` into the batch-wide `NULL` wipe.
- **It commits on unmount**, via a latest-ref + empty-deps effect. React fires no blur for a
  focused element it removes, and **Next** swaps the whole step-1 subtree, so without it a
  typed crop width was silently discarded.
- **A stale draft is dropped when `value` changes underneath**, using the render-time-adjust
  idiom this file's modal already uses for `lastProbe`. `CropOverlay` calls `preventDefault`
  on its handles' `pointerdown`, which suppresses the focus shift — so an input keeps focus
  *and* its draft while an edge drag rewrites the rect.
- **No per-field `|| 1024` fallback** anywhere in the commit path. Those are artifacts of
  `Number("") === 0` and make a typed `0` indistinguishable from an empty field; empty or
  unparseable reverts to the current `value` instead. Note `type="number"` reports `""` for
  anything it does not consider a valid float (`-`, `1e`, `1.2.3`), which lands in that same
  branch.

`frontend/e2e/video-extract.spec.ts` covers the draft contract, the pointerdown fix, the
`NULL` wipe and the arrow-key floor — the first three through the submitted request body,
the last through `aria-valuenow`. Three things there are deliberate:
values are typed with `pressSequentially`, since `fill()` dispatches one input event
carrying the whole string and passes against the broken code; the trim-handle click passes
an off-centre `position`, because the handle straddles the track's left edge at 0 ms and a
centred click lands at exactly 0 ms, which the no-op guard absorbs; and the assertions are
on key *presence* (`not.toHaveProperty('crop')`), since an untouched control is sent as
`undefined` and dropped in serialization. Each was checked against the unfixed code — a bare
tab through the crop fields submitted `{x: 0, y: 0, w: 128, h: 96}`, the full frame.

## Re-attaching to the job

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
  recovery sees the stored id before the persist write can clear it.
- The persist effect writes **on transition only**, tracking the last value per id, and never
  writes for an id it has not yet seen live. `jobs` is a fresh Map on every `activeJobs`
  change — i.e. every SSE event app-wide — so the unguarded form wrote localStorage for every
  watched video on every event, nearly all of those writes for videos with no job at all. It
  also raced the recovery effect above: `VideoDetailPage` and an open modal both run this hook
  for the same video, so instance #2's write could land inside instance #1's in-flight
  `jobsApi.get` and erase the id it was re-attaching to. Declaration order fixes the ordering
  hazard, not this one.
- **The key is removed, not nulled.** All four sites that retire it call `clearPersisted`: the
  persist effect when the live job id becomes `null`, the recovery effect when the fetched job
  is already terminal, the recovery effect's `.catch` on a **404**, and `VideoDetailPage`'s
  delete handler (the video is gone, so is any job for it). `{jobId: null}` reads identically
  to an absent key for every consumer but accumulates one dead entry per video ever extracted,
  and a stale id inside one gets re-fetched and re-404'd on every future mount.
- **The recovery effect has no cleanup, deliberately.** Every write in its `.then` is to a
  global singleton — `useJobStore`, localStorage — never to component state, so an unmounted
  or re-keyed instance triggers no React warning and loses nothing. The `dropped` flag that
  once guarded them *was* the bug it looked like a fix for: arrowing past a video mid-fetch
  discarded a response already marked recovered, and the bar stayed missing until a full
  reload. `recoveredRef` is an in-flight-**or**-settled guard — the id is added before the GET
  so two instances watching one video cannot both fetch, and deleted again on a *transient*
  failure so a later `idsKey` change or remount retries. Only a 404 is terminal
  (`utils/apiError.ts::isNotFound`).
- `startedJobIds` defaults to a module-level frozen `NO_STARTED_JOBS`, not a fresh `{}`: the
  parameter sits in the `jobs` memo's dep list, so a per-call identity would rebuild the Map on
  every `VideoDetailPage` render. `TERMINAL_JOB_STATUSES` comes from `constants/jobs.ts`,
  shared with `TopBar` and `ReextractFramesForm`.

`extractPhaseLabel(job)` turns `progress.phase` into a stage label, because the generic
done/total counts frames and says nothing during the long detection phase.
