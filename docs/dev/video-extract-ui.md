# Pass 1 extraction UI

Covers the two-step `ExtractFramesModal` — its source/probe step, its extract step, the
`ExtractProgressList` rows — and `useVideoExtractJobs`, the hook that re-attaches a
reopened window to a running job. The pointer/geometry controls step 1 is built from
(`CropOverlay`, `TrimBar`, `NumberField`) are in `docs/dev/video-extract-controls.md`.
This is the UI counterpart
of `docs/dev/video-extract.md`, which documents the probe/extract endpoints and the
`video_extract` job behind these controls; read the two together, since almost every
decision here mirrors one there. The surfaces the modal's two entry points live on — the
gallery's `VideoStrip` and `VideoDetailPage` — are in `docs/dev/video-ui.md`, along with
`frontend/src/utils/duration.ts::formatDuration(ms)` and the
`frontend/e2e/video-extract.spec.ts` coverage note in its § Elsewhere. Pass 2 and its
`ReextractFramesForm` are in `docs/dev/video-reextract.md` and
`docs/dev/video-reextract-ui.md`. The re-attach pattern the hook
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
  returns every live extraction in the app, so the membership check is load-bearing). A live
  payload is *meant* never to overwrite a `result` row, which knows strictly more — but
  `mergeRows` compares against `prev`, the remembered Map, rather than against the row it may
  have just written in the same pass, so on the **first** pass that sees a video both entries
  find `old === undefined` and the live row wins. Filed as **V-84**; the faithful form is
  `next?.get(r.videoId) ?? prev.get(r.videoId)`. Narrow and self-healing (it needs the worker's
  first `video_id`-carrying emit in the same React commit as `setResult`, and the next render
  re-upgrades the row), so the visible cost is a transient missing `→ subfolder` label, never a
  lost row — but do not read the code comment there as a guarantee it enforces. "Result if present,
  otherwise liveJobs" breaks a mixed batch: submit A+B+C with A already busy and A's live bar
  vanishes the instant the response lands, leaving only an amber `skipped` line for the video
  working hardest. A `skipped` entry that produced a row is suppressed.
- **A row persists once seen**, accumulated in a `useState` Map (`seenRows`) that is
  **adjusted during render**: `mergeRows(seenRows, incomingRows)` returns the *same* Map when
  nothing changed, so the `setSeenRows` beside it is a no-op unless a new row arrived. A ref
  cannot serve here — the accumulated Map is what the list renders from, and a ref written
  during render schedules no re-render, which the code's own comment says. It is the same
  render-time-adjust idiom the file uses for `lastProbe` and `effectiveSubSelect`.
  `useVideoExtractJobs` filters
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
