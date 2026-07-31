# Pass 1 extraction controls

Covers the three pointer/geometry controls step 1 of the extraction modal is built from —
`CropOverlay`, `TrimBar` and the shared `NumberField` they both render — plus the e2e spec
that pins their contracts. These are read when a control misbehaves: a rect that will not
snap, a handle that jumps on press, a typed number that arrives as something else. The
modal's own lifecycle (its two steps, the probe query, `ExtractProgressList`, the job
re-attach) is in `docs/dev/video-extract-ui.md`, and the endpoints that receive what these
controls produce — `clamp_crop`, the trim semantics, the batch-wide write — are in
`docs/dev/video-extract.md`. `frontend/src/utils/duration.ts::formatDuration(ms)`, which
`TrimBar` labels its handles with, is in `docs/dev/video-ui.md`. `NumberField`'s one-line
entry in the shared-component index is in `docs/dev/frontend-core.md`; the full contract is
here.

## CropOverlay

**`CropOverlay`** — `{ src, frameW, frameH, rect, onChange }`. Draws the sample frame, four
shaded mattes outside the rect (not one outlined box: the matte is what shows how much is
being thrown away) and four draggable edge handles. Pointer events with
`setPointerCapture`, never mouse events, so a drag that leaves the element keeps tracking
and still ends. Handles move in **frame** coordinates (`scale = displayedWidth / frameW`,
re-measured by a `ResizeObserver`), clamp to the frame and **snap to even numbers** so the
rect shown is the rect stored. The handles are
`aria-hidden`; the paired numeric x/y/w/h inputs beneath are the keyboard path, because
there is no honest ARIA pattern for a 2-D rect — which is why they render `NumberField`
(below) rather than a bare input: the naive per-keystroke clamp made *the* a11y path
silently lie about what was typed. `clampRect` is projected onto the single field being
edited (`clamp={(n) => clampRect({ ...active, [field]: n })[field]}`) so both of that
component's props stay honest about the cross-field bounds. Editing a field while `rect`
is null still creates a crop from the full frame; that is how this path creates a rect, and
only the *no-typing* case changed. **Use detected** re-applies `probe.crop`
and **Clear crop** sets it to null, which sends `clear_crop: true` — the disambiguation
`VideoExtractRequest` exists for. The rect sent is only a proposal: the server normalizes
again and stores that, so a later re-extraction replays the stored value.

It is not a byte-for-byte mirror of `video_frames.clamp_crop`, only an agreeing one. This
snaps to the *nearest* even (`Math.round(n / 2) * 2`) where the server floors to even
(`_even_down`), which is invisible only because the client never emits an odd value in the
first place. It also enforces a client-side `MIN_SIDE = 16` per side, which has no server
counterpart — `clamp_crop` merely returns `None` when a side collapses — and the
full-frame-to-no-crop reduction lives on the server and in the modal, not here.

## TrimBar

**`TrimBar`** — `{ durationMs, startMs, endMs, onChange, disabled, disabledNote }`. The
modal passes `disabledNote="This container will not seek, so trimming is unavailable"`;
without one the disabled label falls back to *"Trimming is unavailable for this container"*. Note the backend's
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
`trimTouched` (`docs/dev/video-extract-ui.md` § ExtractFramesModal, the no-op guard) and
minted a fresh probe query key, costing an
8-sample re-probe for a stray click. It takes focus explicitly instead — `preventDefault`
suppresses the focus shift, so without that call a mouse user could never reach the arrow
keys. Those keys `preventDefault` for `ArrowLeft`/`ArrowRight` **only**, so Tab still moves
focus — that is the slider contract rather than a fix for an observed scroll: measured, it
prevents nothing visible, because the arrows scroll horizontally and the modal body has no
horizontal overflow at any tested viewport.

**Step size is 500 ms per press, 5 s with Shift** (`MIN_SPAN_MS` is also 500), on both
handles. Both are `role="slider"` with `aria-valuenow`/`aria-valuetext`, and the end handle
reports the *position* (`duration - trim_end_ms`) rather than the trim amount — which is what
the e2e assertion reads.

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

## NumberField

**`components/common/NumberField.tsx`** — `{ value, clamp, onCommit, …inputProps }`, the
shared number input both controls above are built from — ten *fields* across two components:
step 2's six spinners, which take an `intClamp(lo, hi)` that also rounds since the schema is
`int` and a typed `1.5` used to reach the API, plus the crop's x/y/w/h, which are one
syntactic `<NumberField` inside a `map`. Nothing else in `src/` renders one. Every field here used to re-clamp `Number(e.target.value)` on
each keystroke, which rewrites the prefix you are still typing: `2048` into **Long edge**
arrived as `8192` — `"2"` clamps up to 64 and the remaining three digits append — and the
crop's even-snap turned `150` into `250`. So the raw string is held in a `draft` and clamped
on blur, with one refinement: **it commits live whenever clamping would be the identity**,
so a consumer that paints from the value (the crop mattes) keeps moving for every keystroke
that is not a lie. Five details are load-bearing:

- **Commit is a no-op when `draft === null`.** This is what stops a focus-and-tab with no
  typing from firing `onCommit` and tripping `cropTouched` into the batch-wide `NULL` wipe.
- **It commits on unmount**, via a latest-ref + empty-deps effect. React fires no blur for a
  focused element it removes, and **Next** swaps the whole step-1 subtree, so without it a
  typed crop width was silently discarded.
- **A stale draft is dropped when `value` changes underneath**, using the render-time-adjust
  idiom the modal already uses for `lastProbe`. `CropOverlay` calls `preventDefault`
  on its handles' `pointerdown`, which suppresses the focus shift — so an input keeps focus
  *and* its draft while an edge drag rewrites the rect.
- **No per-field `|| 1024` fallback** anywhere in the commit path. Those are artifacts of
  `Number("") === 0` and make a typed `0` indistinguishable from an empty field; empty or
  unparseable reverts to the current `value` instead. Note `type="number"` reports `""` for
  anything it does not consider a valid float (`-`, `1e`, `1.2.3`), which lands in that same
  branch.
- **Enter commits the draft in place**, via `onKeyDown` — part of the draft contract rather
  than a nicety, since these live in a dialog with no form submit, so without it the only way
  to commit is to move focus. Both `onBlur` and `onKeyDown` coming in through `…inputProps`
  are still forwarded, and run after the internal handlers.

## End-to-end coverage

`frontend/e2e/video-extract.spec.ts` covers six things: the draft contract, the pointerdown
fix, the `NULL` wipe, the arrow-key floor, the derived-`cropTouched` round trip (*narrowing
the crop and putting it back sends no crop* — narrow the rect, restore it, assert no `crop`
key), and the replace label counting only the subfolder the job will delete
(`docs/dev/video-extract-ui.md` § Step 2). All but the arrow-key floor read the submitted
request body or the rendered label; the floor reads `aria-valuenow`. Three things there are
deliberate:
values are typed with `pressSequentially`, since `fill()` dispatches one input event
carrying the whole string and passes against the broken code; the trim-handle click passes
an off-centre `position`, because the handle straddles the track's left edge at 0 ms and a
centred click lands at exactly 0 ms, which the no-op guard absorbs; and the assertions are
on key *presence* (`not.toHaveProperty('crop')`), since an untouched control is sent as
`undefined` and dropped in serialization. Each was checked against the unfixed code — a bare
tab through the crop fields submitted `{x: 0, y: 0, w: 128, h: 96}`, the full frame.
