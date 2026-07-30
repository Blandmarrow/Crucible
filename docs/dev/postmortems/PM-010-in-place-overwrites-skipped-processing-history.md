# PM-010: in-place overwrites never recorded `processing_history`

### Symptom

A video frame that had been cropped in place through `POST /images/{id}/crop` came back from
the pass-2 preview (`POST /videos/reextract/preview`) as **eligible**, counted in the
"N frames will be re-extracted" line with no skip reason. Running the re-extraction would
have re-seeked the recorded timestamp and overwritten the file with the original full frame,
silently discarding the crop — with no warning, no undo prompt, and a job result reporting
it as a successful rewrite.

The same applied to any frame that had been through `POST /images/{id}/resize`, the
`crop_upscale` job's replace mode, `POST /images/batch/resize` or `POST /images/batch/crop`.

Caught before the destructive path shipped, by writing pass 2's skip rule and then checking
what actually populates the column it reads.

### Root cause

`Image.processing_history` was a **convention with no single writer and no consumer**. Its
model comment stated the rule — "any re-extraction pass must skip or warn on a frame with a
non-empty `processing_history`" — but nothing enforced who had to write it.

Upscale, LUT and detection-crop each recorded an entry, in three separate hand-rolled
list-concat blocks. Five paths in `routers/images.py` that overwrite an image file in place
never did: `resize`, replace-mode `crop`, the `crop_upscale` replace job, `batch_resize` and
`batch_crop`.

Because **nothing read the column**, the drift was completely invisible. There was no
failing test to write, no wrong number on a page, no log line — an unrecorded edit and a
recorded one were indistinguishable in every surface the app had. The convention degraded
silently from the day the second path was added.

Pass 2 then made the column load-bearing: it became the skip guard deciding whether a frame
still *is* the frame that was extracted. At that moment the missing entries stopped being a
cosmetic gap. They did not cause a failure — they silently **permitted** a destructive
rewrite, which is the worse of the two directions a guard can fail in.

### Generalizable rule

**When you add the first real consumer of a column that until now only recorded history,
audit every producer before trusting it.** A convention nothing reads is a convention nothing
maintains; the moment code starts branching on it, its historical values are unaudited data,
not a record. Enumerate the write sites from the code, not from the comment that claims what
they should do.

Two follow-ons for this repo:

- **Flag any new path that overwrites an image file in place and records nothing.** What it
  must produce is a list-concat reassignment, never `.append()` (CLAUDE.md § Key invariants,
  "never mutate a loaded JSON column in place"), plus the `updated_at` bump. Reach for
  `images._record_in_place` if the path is in that router; the four writers outside it are
  hand-rolled (see § Fix), so grepping for the helper alone will not find them.
- **A rule stated only in a comment is not enforced.** The model comment named the invariant
  correctly for as long as it was decorative. If a rule matters, it needs a single choke
  point, a test, or both.

### Why it wasn't caught the first time

The rule lived in `backend/models/image.py`'s comment and was enforced nowhere. Four of the
five paths predate the comment, so there was never a moment at which someone was asked to
comply with it. No test asserted the column's contents after any in-place operation, and
none could have failed usefully — the column had no reader, so its value was correct by
vacuous definition.

Noted while writing this up, and worth keeping: **`POST /images/batch/crop` and
`/images/batch/resize` are unreachable.** `POST /images/{image_id}/crop` is declared first,
so FastAPI matches it and reads `batch` as an image id; nothing in the frontend calls either
endpoint. Two of the five paths therefore could not have been exercised by any test, HTTP or
otherwise — which is a second, independent reason the gap was invisible, and a reminder that
route shadowing hides code from coverage as effectively as it hides it from users.

### Fix

Commit `7f5d895`. `images._record_in_place(img, op, **params)` gave the five silent paths in
`routers/images.py` a record — but note what it did **not** do, because the original wording
here ("the single writer … one implementation rather than four") sent reviewers looking for a
choke point that was never built. That commit touched `routers/images.py` alone. The helper is
the single writer *within that module*, called from its five sites (`:954`, `:1012`, `:1089`,
`:1295`, `:1330`); the other writers were never converted and are still hand-rolled
list-concat blocks — `routers/lut.py:211`, `routers/upscaling.py:211`,
`routers/detection.py:1077` and `routers/videos.py:1574` (added later, for `reextract`). So
**five implementations**, not one. Nothing is behaviourally wrong: all five use list-concat
reassignment plus an `updated_at` bump, which is what the invariant requires. But a reviewer
checking a new path cannot grep for one call, which is why § Root cause's description of "hand-rolled
list-concat blocks" still reads as a description of the tree today rather than of the tree
before the fix.

The consumer is `_resolve_reextract_targets` in `routers/videos.py`, which skips a frame
whose `processing_history` holds any op **other than** `reextract` with the reason
`"already edited in place"`. The `reextract` exclusion is load-bearing: without it pass 2
refuses to run a second time, because its own entry looks like third-party editing. Anything
that is not a dict counts as an unknown edit and skips.

See `docs/dev/video-reextract.md` § Target resolution.

### Status & date

MITIGATED — the five paths are fixed and the helper is the choke point, but nothing
structurally prevents a *new* in-place path from skipping it; only review catches that. The
two unreachable batch endpoints remain unreachable and untestable over HTTP. Found in code
review of the `experimental-video-support` branch, not in production.
Last reviewed for staleness: 2026-07-28.
