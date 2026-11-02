# Keep/cut rating

`Image.aesthetic_rating` is the human decision about an image: **4 = Keep, 3 = Probably,
2 = Probably not, 1 = Cut**, NULL = not yet rated. It is authored data — nothing computes it
and nothing recomputes it — and it reaches gallery, filters, export, statistics, settings and
versioning, which is why it has a file of its own rather than a paragraph in each. This file
covers the column, how it travels, how it is filtered, written, keyed, exported and
versioned. The append-only event log behind it, the Phase 0 metrics and the Aesthetic Rating
page are `docs/dev/rating-metrics.md`.

Read `docs/dev/quality/scores-stale.md` for the `record_in_place` contract this shares, and
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
rating in every photo tool: more is better. The cost is that a Keep on the `1` key is
unavailable under the default bindings; `0` clears the rating instead, as in Lightroom. The
keys are rebindable (§ The keys), so that is a default, not a rule.

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

Derivatives — crop, crop+upscale, upscale, LUT, crop-to-detection and inpaint's copy mode —
**do not** inherit it. A derivative has different pixels and deserves its own call, and
inheriting would put an unjudged image into a training set under the user's name. Those six
paths carry provenance via `**copy_provenance(...)`, which returns exactly five keys and so
cannot leak a rating. `test_no_derivative_path_inherits_a_rating` in
`backend/tests/test_video_lineage_mirrors.py` stops an explicit kwarg being added alongside,
and it walks `run_inpaint` as well as the other five: inpaint reached `main` after the
rating was designed, so a new derivative path has to be added to that test's list by hand.

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
judged" is no answer, not a tier below Cut. The gallery side — the chip row, its persisted
state and the sort options — is `docs/dev/gallery/filters.md`.

## Writing it

`POST /images/bulk-rating` is the only writer. It copies `bulk-provenance` exactly — the
`_apply_bulk_filters` scope triple, the cross-dataset `ensure_not_busy` loop, chunked
`sa_update` — and sets `aesthetic_rating` and `rating_stale=False` in one `.values()`.
`rating: null` clears; there is no sentinel, because JSON already carries the two meanings a
nullable 1–4 column needs, and `0` is rejected at the schema (422).

The scope read is chunked too. A client's explicit id list used to resolve through one
`Image.id.in_()` over the whole request body — the B3 class, since nothing bounds a request
body. It now reads in `chunked` batches over a list deduped with `dict.fromkeys`. The dedupe
only matters once the read is chunked: one `IN` collapses a repeated id for free, but two
copies landing in different chunks would come back twice and log two events for one
decision. The test fails with the dedupe removed.

There is no single-image PATCH. None exists for any field but provenance, and the keyboard
paths post a one-element id list.

## The keys

The five keys are settings, not constants. `threshold_settings` carries one column per
action — `rating_key_keep`, `rating_key_probably`, `rating_key_probably_not`,
`rating_key_cut`, `rating_key_clear` — defaulting to `4`, `3`, `2`, `1`, `0`. They sit
server-side beside the label hotkeys they can clash with, so both sets follow the install.
The charset is the label hotkeys' `[a-z0-9]`, one character, and `""` unbinds a key (the
row's not-set sentinel; the PATCH skips a `None`). Two ratings may not share a key, and that
is checked on the **merged row**, not the body, because a one-key PATCH can collide with a
key already saved. A refused PATCH saves nothing: it is a 422 after a rollback.

`frontend/src/hooks/useRatingKeys.ts` reads them from the shared `["settings","thresholds"]`
query that Settings writes, and returns `byKey` (pressed key to rating, `null` meaning
clear), `keyFor`, a `hint` string and `loaded`. It is **empty until the settings load**,
never a fallback to the default digits: once someone has rebound a key, a default guessed at
mount would write the wrong rating for the press that lands first. Every hint and tooltip
that used to name a digit now reads `keyFor`.

Settings → Gallery edits them in `components/settings/RatingKeysPanel.tsx`, one
`HotkeyCaptureButton` per action, saved on each capture. The button refuses a key another
rating action owns (`ownerOf`) and only **warns** about a label hotkey on the same key
(`warnOf`, non-blocking). Refusing would make the order of binding decide the outcome. The
Labels panel passes its own `warnOf`, so the clash is named from both screens.

**A label wins a shared key on the detail page.** Label hotkeys accept any of `a-z` and
`0-9`, so a label bound to a digit used to toggle the label *and* write a rating on one
keypress. The detail page's rating handler now returns early when label hotkeys are enabled
and `byHotkey` has the key. The gallery has no label hotkeys, so nothing yields there. The
ruling: [decision 4](decisions/04-rating-keys-and-old-snapshots.md).
Both rating handlers skip Shift and auto-repeat, as the label hotkeys do: every write is an
event in the self-agreement log, so a held key flooded it.

Keys act on the current selection in the gallery and on the current image in
`ImageDetailPage`. Both use the same guard shape — pane (`paneCtx.paneId !== activePaneId`),
modal, and INPUT/TEXTAREA/SELECT/`isContentEditable`. The two answer the modal half
differently. The gallery asks the DOM for `[role="dialog"]` rather than enumerating state
flags: `SelectionToolbar` opens modals the page never learns about, and `useModalBehavior`
puts that role on every panel in the app. `ImageDetailPage` cannot ask the DOM — its detect
overlay is a bare `fixed inset-0` div carrying no dialog role — so it reads a single hoisted
`anyModalOpen` covering every overlay the page renders. That one set is shared by the rating
effect, the Delete-key effect and the label-hotkey effect, which had a local copy of its own;
two hand-written lists in one file are what drifted apart and let `3` rate the image behind
an open Run Detection. An empty selection produces a toast, never a silent no-op — the keys
are invisible, so nothing happening reads as "broken" rather than "select something first".

**What a write refreshes is fixed at submit time.** The toolbar's Rate dialog sends the
whole selection, which can span split-view panes, so the mutation takes the touched datasets
as variables (the shape delete and move-to-dataset use) rather than refreshing only this
pane's. The detail page passes the image and dataset in the variables too: reading them from
the render closure refreshed the image an arrow press had moved to, not the one rated
(`docs/dev/frontend-jobs.md`). Writes are queued per image like the label hotkeys, so two
quick presses on one image land in the order they were made.

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
asserts the preview and a real export separately for exactly that reason. The filter table
is `docs/dev/export.md`.

## Versioning

The rating is authored *mutable* data, so it is mirrored on `VersionImageState` **and**
present in both `_DIFF_COLS` and `_DIFF_COMPARE_FIELDS` — the immutable carve-out there is
for frame lineage alone. A human re-rating between two snapshots is precisely a difference
the diff exists to show. The mirror's own rules are `docs/dev/versioning/mirrors.md`
§ Mutable qualifiers.

**NULL on the mirror means "not recorded", not "unrated"** — the rule `detections` follows
(GPT-026), ruled in [decision 4](decisions/04-rating-keys-and-old-snapshots.md). A snapshot taken before the rating existed carries NULL in both
`VersionImageState.aesthetic_rating` and `rating_stale`, and reading that as "unrated" made
restoring *any* snapshot anyone has today clear every rating made since. A rating is
hand-made work nothing can recompute. So an unrated image is stored as **`0`** (outside the
1–4 domain, the same fact the gallery's sentinel rests on), through two helpers in
`version_service`: `rating_to_mirror` on the way in and `rating_from_mirror` on the way out.
`rating_stale` is nullable with no server default for the same reason. What follows from it:

- Restore's Pass 2c writes neither column on a NULL row (`docs/dev/versioning/restore.md`).
- The diff compares the rating only when **both** sides recorded one, and shows `0` as unrated.
- `rating-impact` leaves NULL rows out instead of counting them as clears.
- `duplicate_dataset` from a snapshot has no current rating to keep, so there NULL reads as
  unrated.

**Otherwise a restore reverts ratings.** An exemption would make restore's contract
per-column: a rating is authored data exactly like `caption_text`, which already reverts.
It is also not free — a rating that survived while the file rolled back would describe
pixels that no longer exist. There is no "preserve my ratings" checkbox either; that is the
carve-out wearing a control. The *rating* is recoverable through the pre-restore
auto-snapshot, which is checked by default; the rating **events** of an image the restore
deletes are not (`docs/dev/rating-metrics.md` § The event log).

**A failed content write keeps the rating of the pixels still on disk.** `aesthetic_rating`
and `rating_stale` are in `_PIXEL_QUALIFIERS`, the set Pass 3's reconcile puts back
(GPT-025) when a restore plan needed content and did not get it. A keep/cut call is a
judgement of pixels, and `rating_stale` says whether it still is, so laying the snapshot's
rating over pixels it was never about would be a false call.

`GET /datasets/{id}/versions/{version_id}/rating-impact` states the blast radius first. It
is genuinely new: `diff_versions` reads `VersionImageState` on both sides, and there is no
version-vs-*current* comparison anywhere else. One join of the version's state rows against
`images` on `image_id`, comparing `func.coalesce(col, 0)` on both sides — `IS DISTINCT FROM`
is unavailable on SQLite. It returns `will_change`, `will_clear` (rated after the snapshot,
so the restore clears them) and `extras_rated`, counted apart because under
`handle_extra_images="remove"` those rows are **deleted**, not reverted. All three count
rows that exist *now*, so a rated image deleted since the snapshot is re-created by the
restore with its snapshotted rating and appears in none of the figures.

`RestoreConfirmModal` renders the count beside the auto-snapshot checkbox, never in the
amber box — that box is about files that are *unrecoverable*, whereas a reverted rating is
recovered by exactly that checkbox. It does not render at zero (a warning that is always
present is furniture), and the styling escalates when the count is non-zero and the user has
unchecked the safety snapshot.

## Where things live

| Concern | Code |
|---|---|
| Columns, index, mirror | `backend/models/image.py`, `backend/models/versioning.py`, migration `c3b8e1d7a52f` |
| The five key columns | `backend/models/threshold_settings.py`, migration `e7c4a1b9d2f6`, `backend/routers/settings.py` |
| The stale bit's only writer | `backend/utils.py::record_in_place` |
| Filter parse | `backend/utils.py::parse_rating_filter_param` |
| Write endpoint, filter clause, sort | `backend/routers/images.py` |
| Export filters and the advisory | `backend/services/export_service.py`, `backend/routers/export.py` |
| Distribution | `dataset_service._aggregate_dataset_stats` → `DatasetStats.rating_distribution` |
| Mirror helpers, restore | `backend/services/version_service.py::rating_to_mirror`, `::rating_from_mirror` |
| Restore impact | `backend/routers/versioning.py::rating_impact` |
| Vocabulary | `frontend/src/constants/rating.ts` |
| Key bindings | `frontend/src/hooks/useRatingKeys.ts`, `frontend/src/components/settings/RatingKeysPanel.tsx` |
| Badge, chips, keys | `ImageCard.tsx`, `GalleryPage.tsx` |
| Bulk and single controls | `SetRatingModal.tsx`, `ImageDetailPage.tsx` |
| Tests | `backend/tests/test_rating_http.py`, `test_rating_old_snapshots.py`, `test_settings_rating_keys.py`, `test_scores_stale.py`, `test_video_lineage_mirrors.py`, `test_versioning_restore.py`, `frontend/e2e/rating.spec.ts` |

The event log, metrics and the page have their own table in `docs/dev/rating-metrics.md`.
