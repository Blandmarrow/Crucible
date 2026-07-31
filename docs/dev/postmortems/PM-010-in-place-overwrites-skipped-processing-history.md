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

- **Flag any new path that overwrites an image file in place and records nothing.** Call
  `backend/utils.py::record_in_place(img, op, **params)` — since 2026-07-31 that is the one
  writer for the whole repo, and grepping for it now *does* find every site (see § Fix).
  It produces the list-concat reassignment, never `.append()` (CLAUDE.md § Key invariants,
  "never mutate a loaded JSON column in place"), plus the `updated_at` bump and
  `Image.scores_stale`.
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
`/images/batch/resize` were unreachable.** `POST /images/{image_id}/crop` was declared
first, so FastAPI matched it and read `batch` as an image id; nothing in the frontend called
either endpoint. Two of the five paths therefore could not have been exercised by any test,
HTTP or otherwise — which was a second, independent reason the gap was invisible, and a
reminder that route shadowing hides code from coverage as effectively as it hides it from
users. Fixed on 2026-07-31: the observation became PM-018, the block was moved above the
parameterized routes, and both handlers are now covered by
`backend/tests/test_batch_resize_crop_http.py`. Their `_record_in_place` calls are live for
the first time.

### Fix

Commit `7f5d895`. `images._record_in_place(img, op, **params)` gave the five silent paths in
`routers/images.py` a record — but note what it did **not** do, because the original wording
here ("the single writer … one implementation rather than four") sent reviewers looking for a
choke point that was never built. That commit touched `routers/images.py` alone. The helper is
the single writer *within that module*, called from its five sites — `batch_resize`,
`batch_crop` (op `crop_aspect`), the single `resize`, replace-mode `crop`, and the
`crop_upscale` replace job; the other writers were left as hand-rolled
list-concat blocks, one each in `routers/lut.py` (`lut`), `routers/upscaling.py` (`upscale`),
`routers/detection.py` (`crop_to_detection`) and `routers/videos.py` (`reextract`, added
later). So **five implementations**, not one — until the 2026-07-31 amendment below finally
made it one. Cited by symbol rather than by line: the nine
line numbers this paragraph carried had all drifted within three weeks, which is its own
small lesson about postmortems that enumerate call sites. Nothing was behaviourally wrong: all five used list-concat
reassignment plus an `updated_at` bump, which is what the invariant requires. But a reviewer
checking a new path could not grep for one call, which is why § Root cause's description of "hand-rolled
list-concat blocks" read as a description of the tree for the eleven weeks in between.

The consumer is `_resolve_reextract_targets` in `routers/videos.py`, which skips a frame
whose `processing_history` holds any op **other than** `reextract` with the reason
`"already edited in place"`. The `reextract` exclusion is load-bearing: without it pass 2
refuses to run a second time, because its own entry looks like third-party editing. Anything
that is not a dict counts as an unknown edit and skips.

See `docs/dev/video-reextract.md` § Target resolution.

**Amended 2026-07-31 — the choke point this entry asked for now exists.** The helper moved to
`backend/utils.py::record_in_place`, and the four hand-rolled twins (`lut`, `upscaling`,
`detection`, `videos`) were converted to call it; `images._record_in_place` remains as a thin
alias so the six call sites in that router and the prose above stay valid. Each converted
block's dict is field-for-field identical to what it built before, which matters because
`_edited_in_place` reads the `op` key as pass 2's skip guard. One implementation, not five.

Two things made the move worth doing beyond tidiness. The helper now also sets
`Image.scores_stale`, so the two columns that describe an in-place rewrite can never drift
apart — a site that recorded the edit and left the scores looking trustworthy would be the
same class of silent gap this entry is about (`docs/dev/scoring.md` § `scores_stale`). And
the enforcement § Generalizable rule called for is real: `backend/tests/test_scores_stale.py`
walks `backend/routers/*.py` and `backend/services/*.py` for any
`… .processing_history = … + …` list-concat and fails if one exists outside `utils.py`. It
matches only the `BinOp(Add)` shape, so the plain copies in `restore_snapshot` and
`dataset_service` — which carry a history rather than extend one — are excluded.

### Status & date

MITIGATED — one writer, called from every in-place site, with a structural test that fails CI
for the next path that hand-rolls the append. What remains unenforced is a path that
overwrites pixels and records **nothing at all**: the AST guard can only see a wrong append,
never a missing one, so review still owns that case.

**Amended 2026-07-31:** the sentence that used to close this section — that the two batch
endpoints remain unreachable and untestable over HTTP — is no longer true and contradicted
the § Why it wasn't caught note eleven lines above it. PM-018 moved both above the
parameterized routes on 2026-07-31; they execute, and `backend/tests/test_batch_resize_crop_http.py`
covers them.

Found in code review of the `experimental-video-support` branch, not in production.
Last reviewed for staleness: 2026-07-31.
