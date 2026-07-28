# PM-008: one progress counter carrying two meanings across phases

### Symptom

Extracted frames did **not** appear in the gallery while a `video_extract` job ran, and the
new subfolder did not appear in the sidebar — both only materialized when the job finished.
That contradicted the modal's own footer (*"Frames appear in the gallery as they are
written"*), `docs/gallery.md`, and the `TopBar` comment describing the branch that was
supposed to make it happen.

The `TopBar` running-job pill made the same defect visible directly: it counted up to
`94,102 / 173,318` during detection and then snapped back to `8 / 384` when extraction
started.

The failure **inverts with input size**. A clip whose detection finishes inside one 0.5 s
tick emits no detection progress at all, so the high-water mark stays at zero and live
invalidation works — which is every fixture in the test suite and every e2e video. A real
multi-shot film fails every time.

### Root cause

`_run_extraction`'s `done` meant two different things in two phases of the same job:

- `_detect_with_progress` emitted `done = progress.frames_read` — frames *decoded*, climbing
  into the hundreds of thousands on a long file.
- The extraction loop emitted `done = (shot_pos + 1) * frames_per_shot` — frames *planned*,
  restarting from a small number.

`TopBar`'s live gallery invalidation keeps a per-job monotonic high-water mark
(`captionDoneRef`) and fires only on `currentDone > prevDone`. So it fired twice a second
throughout detection, when nothing had been written, and then stayed silent for the entire
extraction phase — the only phase that produces rows — because the decoded-frame count had
already ratcheted the mark far past anything extraction could emit.

Neither counter was wrong for its own phase. The defect is that they were the same field.

### Generalizable rule

**A progress counter carries one meaning for the whole job**, and for anything in
`LIVE_IMAGE_JOB_TYPES` that meaning must be *rows a refetch would see* — not work performed,
not items planned. A high-water-mark gate turns `done` from a display value into a contract:
a job whose `done` changes meaning between phases silently disables its own live
invalidation, and nothing fails.

Three follow-on rules:

- **A phase that writes nothing reports `done: 0, total: 0` explicitly, never by omission.**
  `jobStore` merges partials by job id, so an omitted key inherits whatever the client last
  held. Readable per-phase detail belongs in `message`, which is what
  `tag_consolidation_service` already does for its non-per-item phases and what the ETA rides
  on here.
- **Count commits, not iterations.** Rows become visible to another session at a commit, so
  a worker that commits every N writes must step `done` every N writes too — emitting per
  item would refetch identical data N−1 times out of N. Keep the smooth *bar* by passing the
  per-item fraction separately, as `_emit`'s `fraction` argument does.
- **A pill rendering `{done} / {total}` must gate on `total > 0`**, not on `percent >= 0`.
  This class of bug is not confined to video: `ml/download_progress.py`'s `emit_sync`
  hardcodes `done: 0, total: 0` while supplying a real percent, so an HF model download had
  been showing a moving bar beside a meaningless `0 / 0` for as long as it has existed.

### Why it wasn't caught the first time

The two emits were written in different phases of the same review-clean commit, and each
reads correctly in isolation — the bug exists only in the relationship between them and a
consumer in a different language three files away. The only automated coverage that could
have caught it (`video-extract.spec.ts`, `test_video_extract_http.py`) runs against short
fixtures, where detection emits nothing and the defect cannot manifest.

The invariant also had no written home. `docs/dev/video-extract.md` documented the phase
bands and `video_id` in detail but described `done` only as part of a generic payload, and
`docs/dev/frontend-jobs.md` described the live branch as firing "on each `done` increment"
without saying what `done` had to mean for that to work.

### Fix

- `backend/routers/videos.py`: all four `detecting` emits pin `done=0, total=0`; the
  extraction emit carries the committed count (`written − written_since_commit`) while
  `_emit`'s `fraction` stays on planned frames. The loop's local was renamed `planned` so the
  two quantities cannot be confused at the call site.
- `TopBar.tsx`: the `{done} / {total}` span is gated on `(total ?? 0) > 0`.
- `test_video_extract_http.py::test_every_progress_payload_names_its_video` asserts the
  invariant over a whole real run — no `detecting` payload with a non-zero `done`, `done`
  non-decreasing, `done <= total` during extraction — rather than the field names.
- Written down in `docs/dev/video-extract.md` § the `video_extract` job and
  `docs/dev/frontend-jobs.md` beside the live branch.

### Status & date

MITIGATED — the invariant is now stated, tested for `video_extract` and gated in the pill,
but nothing structurally stops the *next* multi-phase worker from overloading its own
counter; only review catches that. `BackgroundJob.done_items` remains unwritten by every job
in the repo (so `LogsPage` shows `0/N` for everything), which is a separate, pre-existing
gap. Found in code review of the `experimental-video-support` branch, not in production.
Last reviewed for staleness: 2026-07-28.
