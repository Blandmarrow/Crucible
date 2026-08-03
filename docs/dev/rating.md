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
modal, and INPUT/TEXTAREA/SELECT/`isContentEditable`. The two answer the modal half
differently. The gallery asks the DOM for `[role="dialog"]` rather than enumerating state
flags: `SelectionToolbar` opens modals the page never learns about, and `useModalBehavior`
puts that role on every panel in the app. `ImageDetailPage` cannot ask the DOM — its detect
overlay is a bare `fixed inset-0` div carrying no dialog role — so it reads a single hoisted
`anyModalOpen` covering every overlay the page renders, shared with the Delete-key effect
below it. One set, because two hand-written lists in one file are what drifted apart and let
`3` rate the image behind an open Run Detection.
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
carve-out wearing a control, and it can produce a dataset state that never existed. The
*rating* is recoverable through the pre-restore auto-snapshot, which is checked by default;
the rating **events** of an image the restore deletes are not (§ The event log).

`GET /datasets/{id}/versions/{version_id}/rating-impact` states the blast radius first. It
is genuinely new: `diff_versions` reads `VersionImageState` on both sides, and there is no
version-vs-*current* comparison anywhere else. One join of the version's state rows against
`images` on `image_id`, comparing `func.coalesce(col, 0)` on both sides — `IS DISTINCT FROM`
is unavailable on SQLite, and `0` is safe because it is outside the 1–4 domain. It returns
`will_change`, `will_clear` (rated after the snapshot, so the restore clears them) and
`extras_rated`, counted apart because under `handle_extra_images="remove"` those rows are
**deleted**, not reverted. All three count rows that exist *now* — the change/clear query
inner-joins `images`, and the extras query counts live rows only — so a rated image deleted
since the snapshot is re-created by the restore with its snapshotted rating and appears in
none of the figures.

`RestoreConfirmModal` renders the count beside the auto-snapshot checkbox, never in the
amber box — that box is about files that are *unrecoverable*, whereas a reverted rating is
recovered by exactly that checkbox. It does not render at zero (a warning that is always
present is furniture), and the styling escalates when the count is non-zero and the user has
unchecked the safety snapshot.

## The event log

`aesthetic_rating` is overwritten in place and `updated_at` is a generic `onupdate` that any
edit bumps, so nothing knew *when* a rating was given or that one had ever been revised.
`image_rating_events` (`backend/models/rating_event.py`, migration `d4a2c9e6b8f3`) is that
history: one append-only row per rating write.

| Column | Why it is what it is |
|---|---|
| `id` — integer autoincrement | A bulk write stamps one `datetime.utcnow()` across its whole batch, so `created_at` ties are the norm. The monotonic id is the only deterministic ordering key for "consecutive events", and the ceiling below is defined over consecutive pairs. Precedent for the integer form: `VersionImageState`, `Detection`. |
| `image_id` — FK, `ON DELETE CASCADE` | With the image gone there are no pixels the judgement was about. |
| `dataset_id` — **no FK** | Mirrors `VersionImageState.image_id`. It records the dataset the rating was *given in*, which is the frame a rating is calibrated against (§ The scale). A `datasets.id` FK with CASCADE would destroy the history of an image since moved out of a deleted dataset. |
| `rating` — nullable | A clear is an event: "I withdraw the judgement" is something a human did. |
| `batch_size` | `len(ids)` of the write. It separates "I looked at this image and pressed 4" from "I swept 1,970 images to Cut", and it is the only fact here that exists solely at write time and cannot be reconstructed later — which is why it went in immediately. NULL means unknown (the backfill). |
| no `updated_at` | Append-only; an `onupdate` advertises a mutation path that must not exist. |

The migration backfills one event per already-rated image, stamped with `updated_at` — the
tightest available upper bound on when the rating was given. `created_at` is the image's
ingest time, always earlier, and would fabricate a false ordering against the real events
that follow. The backfill does not create a ceiling (one event per image means no comparable
pairs); what it buys is that the *first* deliberate re-rate produces a countable pair
instead of needing two passes.

**`bulk_rating` is the sole writer**, in the same transaction as the rating itself. The
insert is an `INSERT … SELECT` sharing the `chunked()` loop and the identical
`Image.id.in_(batch)` predicate as the `UPDATE`, so the two cannot cover different sets — and
because `from_select` binds the id list plus **three** literals (`Image.id` and
`Image.dataset_id` are selected columns and bind nothing) rather than five binds per row,
`chunked`'s 10,000 default stays correct against SQLite's 32,766 ceiling and no second chunk
size has to be invented and kept in step.

**An unchanged re-rating still writes an event.** This is the load-bearing rule. `bulk_rating`
already argues it for `rating_stale` — looking again is the whole event the bit is about —
and here suppressing the no-ops would leave the log holding *only* disagreements, so
self-agreement would compute to 0% forever.

**A restore writes no events**, and that creates a deliberate non-invariant. An event means
"a human looked at these pixels and said this"; a rollback is not that, and replaying one
would synthesise a disagreement the user never made in the exact statistic the log exists to
produce. The cost is that after a restore `images.aesthetic_rating` **can disagree with the
last event for that image** — so nothing may derive the *current* rating from the log, and
`backend/tests/test_rating_events.py` pins it so a future "make the log authoritative"
change has to argue.

**Undoing a restore does not bring events back.** `image_id` is `ON DELETE CASCADE` and
`restore_snapshot(handle_extra_images="remove")` *deletes* `Image` rows, so an image the
restore drops loses its whole history; restoring the pre-restore snapshot re-creates it with
its original id and its snapshotted rating, and the events stay gone. Verified under
`PRAGMA foreign_keys=ON` (production's setting): 2 events → 0 → rating 4 back, events 0.
That is the log's **second** divergence direction — a rated image with *zero* events — so
nothing may assume `rated ⇒ has ≥1 event` any more than it may read the current rating out
of the log.

Copy and derivative paths mint new `Image.id`s, so events do not travel while the rating
does; carrying them would count one human decision twice.

Two writer properties the tests pin because nothing else can. `dataset_id` is read from
`Image.dataset_id` **per row**, never from the request body — an explicit id list can span
datasets, which is why the endpoint guards every dataset it touches. And the ordering takes
**three** events to observe at all: every figure over a single pair is symmetric, so a
router sorting by `rating` turns `1 → 4 → 1` into `1 → 1 → 4` and only the third event tells
them apart. The `id`-rather-than-`created_at` half of that rule is not testable at any
layer — one write makes at most one event per image, so within an image's history timestamps
never tie, and the cross-image ties it guards against are removed by grouping before
ordering matters.

## Phase 0 metrics

Two questions decide whether a learned aesthetic head is worth building, and neither needs a
head, a trainer or a labeling queue. `backend/ml/rating_metrics.py` answers both in pure
numpy — no DB, no torch, no images — which is what makes them CI-testable.
`GET /rating/summary` and `GET /rating/scorer-agreement` (`backend/routers/rating.py`) serve
them; both pool across every dataset, because a head trained from pooled labels cannot live
under one. `routers/rating.py` owns aggregate *reads* over the corpus; `images.py` keeps the
writes.

**scipy is undeclared, not absent.** `backend/requirements-ci.txt` declares `numpy>=1.26`
and no scipy — but `imagehash>=4.3` is declared, and imagehash *requires* scipy, so it is
installed on the runner regardless. That is a fact about a pHash library's dependency list,
not a guarantee: imagehash dropping scipy or moving it to an extra would break rank
correlation with no visible connection between the two. So Spearman is hand-rolled in numpy,
and `test_rating_metrics.py` cross-checks it against `scipy.stats.spearmanr` behind a
`pytest.importorskip` — it runs where scipy is present and skips cleanly where it is not,
rather than being the thing that fails a run.

**Your own ceiling** — `self_agreement`. Universe: images with ≥2 events. The headline counts
**consecutive** pairs, not first-versus-last, which hides oscillation (1 → 4 → 1 scores as
perfect agreement); first-versus-last is returned by the API and rendered nowhere.
Clears are excluded rather than counted as disagreements, because a withdrawal is not a
second opinion. It returns **raw counts, never a bare rate**, because the figure is a
diagnostic and three biases pull it in known directions: *selection* (you re-rate what you
disagree with) pushes it **down**; *anchoring* (the second look sees your old answer) pushes
it **up**; *bulk sweep* (select-all then press 1 writes events for images nobody looked at)
pushes it **up** hardest, and `singleton_*` — pairs where **both** sides had `batch_size == 1`
— is what isolates it. Its complement `bulk_pairs` is therefore *not a confirmed one-image
write*, not *bulk*: the backfill wrote NULL batch sizes, so on any existing install the
first re-rate of a backfilled image lands there too, and the page's label says so. The page
shows counts and no percentage below `MIN_CEILING_PAIRS` (10), and renders the "not a blind
re-show" caveat unconditionally. A system-selected,
previous-answer-hidden re-show is what would turn this into a real ceiling, and that is not
built.

**Does an existing scorer already track your taste** — Spearman ρ of `aesthetic_score`
against `aesthetic_rating`, **grouped by `aesthetic_model`** because LAION and V2.5 scores
are not comparable. Migration `a5e1b7c3d9f0`'s backfill *established* `aesthetic_score IS
NOT NULL ⟺ aesthetic_model IS NOT NULL` but nothing enforces it, so a scored row with no
marker gets its **own bucket** keyed `None` rather than being skipped — that is what keeps
`sum(m.n) == scored_and_rated` true, and both the schema field and the page's label are
nullable for it. `models` is a **list ordered by `n` desc**, not a dict, so a future
`head:{uuid}` producer lands in it without a schema change.

Three details are not refinements:

- **Average ranks are mandatory.** The rating vector has four distinct values over hundreds
  of rows, so every rank is the mean of hundreds of positions. Ordinal ranking would impose
  an arbitrary within-tier order fixed by row order, and the "correlation" would partly be
  measuring `images.id` ordering — silently.
- **`spearman_ceiling`.** With a four-level target the tie structure caps ρ below 1.0 no
  matter how good the scorer is. "0.31 of a possible 0.97" is the honest form; a bare 0.31
  reads as failure forever.
- **Every guard returns `None`, never NaN.** These are serialised to JSON, which cannot carry
  NaN, so a missing zero-variance guard is a serialisation error rather than a wrong number
  — and zero variance is a real early state (forty images all rated Cut).

`ordering_auc` is the Mann-Whitney statistic per adjacent-tier boundary, computed from the
same `average_ranks` helper in O(n log n) — it never materialises the |lo|×|hi| pair matrix
— with ties earning 0.5. The page draws its bar **centred on 0.5**, since 0.5 is a coin flip
and a bar from zero would make no information look like half a success.

**The floors live in the page, not the module.** `rating_metrics`' `None` means the
statistic is *undefined* (an empty side, zero variance, fewer than three points); "defined
but too thin to print" is a different claim, and collapsing the two would throw away the
counts the page renders in its place. So `AestheticRatingPage` carries three constants:
`MIN_CEILING_PAIRS` (10 pairs), `MIN_BOUNDARY_PER_SIDE` (5 images **per side**, never the
product — one image against twenty is twenty non-independent comparisons a single score
decides) and `MIN_RHO_IMAGES` (20 rated-and-scored, where ρ's SE ≈ 0.23 against **0.71** at
the three points `spearman` merely calls defined). Below its floor each renders an em dash
plus the counts that would fill it; `pairs`, `n`, `n_lo`, `n_hi` and `n_by_rating` are on
the wire for exactly that — the last so a tier mean over one image cannot read like a mean
over two hundred, which it is not derivable from `boundaries` to prevent except by an index
trick that breaks the moment a boundary changes. Below `MIN_RHO_IMAGES` the *ceiling* is withheld with the ρ — from three
images in three tiers it computes to 1.00 and reads as headroom when it only means nothing
tied. `n_lo`/`n_hi` are rendered **beside the bar**, not in a `title=` tooltip no screen
reader announces, and the bar is `aria-hidden`: the number next to it is the datum.

### Testing the page: no dataset scope means no absolute counts

Every other e2e spec scopes to a dataset it just created, so it owns its numbers. This page
has no dataset scope by design, which makes the whole shared e2e database its corpus —
`frontend/e2e/rating-page.spec.ts` therefore takes a baseline from `GET /rating/summary`
first, asserts **deltas** across its writes, then asserts the rendered tiles against the
API's own post-write values. An absolute count passes when the spec runs alone and fails the
moment a sibling spec uploads anything, which is how this one was written the first time.

The part that fails *silently* is the branch. The spec exists to prove the page refuses a
percentage below `MIN_CEILING_PAIRS`, and that branch is chosen from a corpus the spec does
not control — so as the suite grows past ten comparable pairs it would quietly start
exercising the other branch and stop testing the refusal, still green. The guard is an
explicit `expect(pairs).toBeLessThan(10)`, which turns that into a failure that names itself.
Any later spec asserting a threshold branch on this page needs the same guard; Phase 2's
labeling queue lands on this page and will.

## Where things live

| Concern | Code |
|---|---|
| Columns, index, mirror | `backend/models/image.py`, `backend/models/versioning.py`, migration `c3b8e1d7a52f` |
| Event log | `backend/models/rating_event.py`, migration `d4a2c9e6b8f3` |
| Metrics (pure numpy) | `backend/ml/rating_metrics.py` |
| Metric endpoints | `backend/routers/rating.py`, `backend/schemas/rating.py` |
| The Aesthetic Rating page | `frontend/src/pages/AestheticRatingPage.tsx`, `frontend/src/api/rating.ts` |
| Metric invalidation | `frontend/src/constants/queryKeys.ts::invalidateRatingMetrics` |
| The stale bit's only writer | `backend/utils.py::record_in_place` |
| Filter parse | `backend/utils.py::parse_rating_filter_param` |
| Write endpoint, filter clause, sort | `backend/routers/images.py` |
| Export filters and the advisory | `backend/services/export_service.py`, `backend/routers/export.py` |
| Distribution | `dataset_service._aggregate_dataset_stats` → `DatasetStats.rating_distribution` |
| Restore impact | `backend/routers/versioning.py::rating_impact` |
| Vocabulary | `frontend/src/constants/rating.ts` |
| Badge, chips, keys | `ImageCard.tsx`, `GalleryPage.tsx` |
| Bulk and single controls | `SetRatingModal.tsx`, `ImageDetailPage.tsx` |
| Tests | `backend/tests/test_rating_http.py`, `test_rating_events.py`, `test_rating_metrics.py`, `test_rating_metrics_http.py`, `test_scores_stale.py`, `test_video_lineage_mirrors.py`, `test_versioning_restore.py`, `frontend/e2e/rating.spec.ts`, `frontend/e2e/rating-page.spec.ts` |
