# Keep/cut rating

`Image.aesthetic_rating` is the human decision about an image: **4 = Keep, 3 = Probably,
2 = Probably not, 1 = Cut**, NULL = not yet rated. It is authored data — nothing computes it
and nothing recomputes it — and it reaches gallery, filters, export, statistics and
versioning, which is why it has a file of its own rather than a paragraph in each.

Read `docs/dev/scores-stale.md` for the `record_in_place` contract this shares, and
`docs/dev/image-filters.md` for the listing contract `rating_filter` joins.

## The scale, and why higher is better

Four buckets, not five: an odd number gives a middle bucket, and a middle bucket is where
everything lands. No neutral option forces a call to one side.

Decision language, not quality language — "Keep", not "Great". It is far easier to answer
consistently because it is a call you already make. The cost is that it is *contextual*:
"Keep" in a weak dataset is worse than "Cut" in a strong one, so a rating is only calibrated
within the dataset it was given in.

**4 is the best.** Every other numeric column in the app is higher-is-better,
`SORT_OPTIONS` reads "Aesthetic ↓" for best-first, and an inverted scale would make
"Rating ↑" mean best-first and read wrong beside its neighbour. It also matches the star
rating in every photo tool — press the number, more is better — and it keeps one direction
across the feature. The cost is that "press 1 for Keep" is unavailable; `0` clears the
rating instead, as in Lightroom.

The vocabulary lives once, in `frontend/src/constants/rating.ts`: labels, colours, the
`RATING_UNRATED` sentinel and `encodeRatingFilter`. Seven modules import it — the card, the
chip row, the toolbar and its modal, the detail page, the export filters and the stats panel
— and a dataset where one screen says "Keep" and another says "Best" is a dataset nobody
trusts.

## Two staleness bits, not one

`Image.rating_stale` says the pixels were rewritten in place after the human judged them.
It is written by `utils.record_in_place` — the single writer of `processing_history`,
`scores_stale` and this — and only for a row that actually carries a rating, on exactly the
reasoning that governs `scores_stale`: a row nobody judged has no judgement to invalidate.

It is **not** a reuse of `scores_stale`, because the *clear* predicates diverge. A quality
run that re-measured clears `scores_stale`; nothing but a human looking again clears
`rating_stale`, and the sole clear site is `POST /images/bulk-rating`. One bit would let a
routine re-score declare a stale judgement current — which then feeds labels about deleted
pixels into anything that learns from the column.

Assigning a rating clears the bit even when the value is unchanged: looking again is the
event the bit records. Clearing the rating to NULL clears it too — no rating, nothing stale.

## Travel rules

The rating **travels** on cross-dataset move and copy. Moving a file does not change your
opinion of it, so it behaves like `caption_text`.

Derivatives — crop, crop+upscale, upscale, LUT, crop-to-detection — **do not** inherit it. A
derivative has different pixels and deserves its own call, and inheriting would put an
unjudged image into a training set under the user's name. Those five paths carry provenance
via `**copy_provenance(...)`, which returns exactly five keys and so cannot leak a rating;
`test_no_derivative_path_inherits_a_rating` in `backend/tests/test_video_lineage_mirrors.py`
is what stops an explicit kwarg being added alongside.

Enrolment into the rebuild-path guards is `info={"carried": True}` on the column, a sibling
of the `info={"qualifies": ...}` mechanism. Deliberately **not** `qualifies`, which means
"this column says how to read a score" and is asserted to name a real `*_score`; a rating
qualifies nothing — it *is* the datum. And deliberately not named `*_score`, because
`utils.score_columns` is suffix-derived and pinned to exactly ten names.

`batch_move_dataset` needs no change to carry it: that path is an in-place `sa_update`
naming only the columns it changes, so an unnamed column travels by construction. It is the
one "travels" site that looks unhandled, and carries a comment saying so.

## The filter shape, and its one departure from precedent

`rating_filter` is a **single** param: a JSON array of ints 0–4 where `0` means unrated.
`GET /images/`, `/count` and `/ids` inherit it through `ImageFilterParams`; the parse is
`utils.parse_rating_filter_param`, and anything outside 0–4 is a 400 rather than a silently
narrowed filter.

This diverges from `license_filter`, which is a *pair* (`license_filter` +
`license_missing`) and 400s on a blank entry. License splits them because `""` is ambiguous
— it is also a legitimate free-text license value. `0` is unambiguously outside the stored
1–4 domain, and the chip row genuinely needs OR across "some tiers **or** still unrated",
which two independent params can only AND. That OR is the feature: "Keep or unrated" is a
real review pass.

Sorting is separate again. `aesthetic_rating` is in `_ALLOWED_SORT_FIELDS` and
`_NULLS_LAST_SORT_FIELDS`, but **not** in `_ALLOWED_SCORE_FIELDS`, which also drives the
score filters and the Stats histograms. Unrated sorts last in *both* directions: "not
judged" is no answer, not a tier below Cut.

## Writing it

`POST /images/bulk-rating` is the only writer. It copies `bulk-provenance` exactly — the
`_apply_bulk_filters` scope triple, the cross-dataset `ensure_not_busy` loop, chunked
`sa_update` — and sets `aesthetic_rating` and `rating_stale=False` in one `.values()`.
`rating: null` clears; there is no sentinel, because JSON already carries the two meanings a
nullable 1–4 column needs, and `0` is rejected at the schema (422).

There is no single-image PATCH. None exists for any field but provenance, and the keyboard
paths post a one-element id list.

Keys `1`–`4` and `0` act on the current selection in the gallery and on the current image in
`ImageDetailPage`. Both use the same guard shape — pane (`paneCtx.paneId !== activePaneId`),
modal, and INPUT/TEXTAREA/SELECT/`isContentEditable`. The gallery asks the DOM for
`[role="dialog"]` rather than enumerating state flags: `SelectionToolbar` opens modals the
page never learns about, and `useModalBehavior` puts that role on every panel in the app.
An empty selection produces a toast, never a silent no-op — the keys are invisible, so
nothing happening reads as "broken" rather than "select something first".

## Export

Two filters, each mirroring an existing one exactly. `rating_min` behaves like
`aesthetic_min`: an unrated image has no value to compare and is **excluded**.
`exclude_ratings` behaves like `exclude_flags`: naming a tier drops it, and an unrated image
is never named, so "exclude Cut" leaves the untriaged alone. The export body takes tiers
1–4 only — `0` is the gallery filter's unrated sentinel and would give one number two
meanings across two screens, so it is a 400.

**The include-filter hazard is real and was accepted knowingly.** "Rating ≥ Probably" over a
dataset where 214 of 1,970 images are rated exports 214 images and reports success. The
mitigation is that the filter states its own population *before* it runs, not that the
filter is withheld: `preview_export` returns an `unrated_count` / `unrated_will_export`
advisory pair alongside the `excluded_by_rating` exclusion row, and `ExportPage` renders it
whenever the include filter is on. The same habit puts an unrated total beside the gallery's
chip row.

`preview_export`'s exclusion evaluation is a **duplicate** of `_is_excluded`, not a call to
it, so both rating clauses land twice and must stay in step. `backend/tests/test_rating_http.py`
asserts the preview and a real export separately for exactly that reason.

## Versioning

The rating is authored *mutable* data, so it is mirrored on `VersionImageState` **and**
present in both `_DIFF_COLS` and `_DIFF_COMPARE_FIELDS` — the immutable carve-out there is
for frame lineage alone. A human re-rating between two snapshots is precisely a difference
the diff exists to show.

**A restore reverts ratings.** The alternative was an exemption, and it is not exempt: a
rating is authored data exactly like `caption_text`, which already reverts, and restore's
contract is "the dataset as it was". Exempting one authored column makes that contract
per-column. The carve-out is also not free — a rating that survived while the file rolled
back would describe pixels that no longer exist, and clearing it would make restore a second
writer of `rating_stale`. There is no "preserve my ratings" checkbox either; that is the
carve-out wearing a control, and it can produce a dataset state that never existed. The loss
is recoverable through the pre-restore auto-snapshot, which is checked by default.

`GET /datasets/{id}/versions/{version_id}/rating-impact` states the blast radius first. It
is genuinely new: `diff_versions` reads `VersionImageState` on both sides, and there is no
version-vs-*current* comparison anywhere else. One join of the version's state rows against
`images` on `image_id`, comparing `func.coalesce(col, 0)` on both sides — `IS DISTINCT FROM`
is unavailable on SQLite, and `0` is safe because it is outside the 1–4 domain. It returns
`will_change`, `will_clear` (rated after the snapshot, so the restore clears them) and
`extras_rated`, counted apart because under `handle_extra_images="remove"` those rows are
**deleted**, not reverted.

`RestoreConfirmModal` renders the count beside the auto-snapshot checkbox, never in the
amber box — that box is about files that are *unrecoverable*, whereas a reverted rating is
recovered by exactly that checkbox. It does not render at zero (a warning that is always
present is furniture), and the styling escalates when the count is non-zero and the user has
unchecked the safety snapshot.

## Where things live

| Concern | Code |
|---|---|
| Columns, index, mirror | `backend/models/image.py`, `backend/models/versioning.py`, migration `c3b8e1d7a52f` |
| The stale bit's only writer | `backend/utils.py::record_in_place` |
| Filter parse | `backend/utils.py::parse_rating_filter_param` |
| Write endpoint, filter clause, sort | `backend/routers/images.py` |
| Export filters and the advisory | `backend/services/export_service.py`, `backend/routers/export.py` |
| Distribution | `dataset_service._aggregate_dataset_stats` → `DatasetStats.rating_distribution` |
| Restore impact | `backend/routers/versioning.py::rating_impact` |
| Vocabulary | `frontend/src/constants/rating.ts` |
| Badge, chips, keys | `ImageCard.tsx`, `GalleryPage.tsx` |
| Bulk and single controls | `SetRatingModal.tsx`, `ImageDetailPage.tsx` |
| Tests | `backend/tests/test_rating_http.py`, `test_scores_stale.py`, `test_video_lineage_mirrors.py`, `test_versioning_restore.py`, `frontend/e2e/rating.spec.ts` |
