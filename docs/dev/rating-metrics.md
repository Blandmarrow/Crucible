# Rating events and Phase 0 metrics

The history behind `Image.aesthetic_rating` and the questions it answers. This file covers
`image_rating_events`, the append-only log of every rating write; the two Phase 0 metrics
computed from it (your own ceiling, and whether an existing scorer tracks your taste); the
Aesthetic Rating page that renders them; and how that page is tested. The column itself —
scale, travel rules, filter, keys, export and versioning — is `docs/dev/rating.md`, which
this file leans on. The staged plan these shipped from, and the learned head still ahead of
them, is `docs/dev/rating-plan/README.md`.

## The event log

`aesthetic_rating` is overwritten in place and `updated_at` is a generic `onupdate` that any
edit bumps, so nothing knew *when* a rating was given or that one had ever been revised.
`image_rating_events` (`backend/models/rating_event.py`, migration `d4a2c9e6b8f3`) is that
history: one append-only row per rating write.

| Column | Why it is what it is |
|---|---|
| `id` — integer autoincrement | A bulk write stamps one `datetime.utcnow()` across its whole batch, so `created_at` ties are the norm. The monotonic id is the only deterministic ordering key for "consecutive events", and the ceiling below is defined over consecutive pairs. Precedent for the integer form: `VersionImageState`, `Detection`. |
| `image_id` — FK, `ON DELETE CASCADE` | With the image gone there are no pixels the judgement was about. |
| `dataset_id` — **no FK** | Mirrors `VersionImageState.image_id`. It records the dataset the rating was *given in*, which is the frame a rating is calibrated against (`docs/dev/rating.md` § The scale). A `datasets.id` FK with CASCADE would destroy the history of an image since moved out of a deleted dataset. |
| `rating` — nullable | A clear is an event: "I withdraw the judgement" is something a human did. |
| `batch_size` | `len(ids)` of the write. It separates "I looked at this image and pressed 4" from "I swept 1,970 images to Cut", and it is the only fact here that exists solely at write time and cannot be reconstructed later — which is why it went in immediately. NULL means unknown (the backfill). |
| no `updated_at` | Append-only; an `onupdate` advertises a mutation path that must not exist. |

The migration backfills one event per already-rated image, stamped with `updated_at` — the
tightest available upper bound on when the rating was given. `created_at` is the image's
ingest time, always earlier, and would fabricate a false ordering against the real events
that follow. The backfill does not create a ceiling (one event per image means no comparable
pairs); what it buys is that the *first* deliberate re-rate produces a countable pair
instead of needing two passes.

**The table is the one Image-attached table that is deliberately not mirrored on
`VersionImageState`.** It is history of what a human did, not a state a snapshot restores,
and mirroring it would let a restore rewrite the record the statistic is computed from.

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
self-agreement would compute to 0% forever. It is also why the rating keys skip auto-repeat:
a held key is not a series of decisions.

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
— is what isolates it.

Its complement `bulk_pairs` is therefore *not a confirmed one-image write*, not *bulk*: the
backfill wrote NULL batch sizes, so on any existing install the first re-rate of a
backfilled image lands there too, and the page's label says so. The page shows counts and no
percentage below `MIN_CEILING_PAIRS` (10), and renders the "not a blind re-show" caveat
unconditionally. A system-selected, previous-answer-hidden re-show is what would turn this
into a real ceiling, and that is not built.

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

## The page's floors

**The floors live in the page, not the module.** `rating_metrics`' `None` means the
statistic is *undefined* (an empty side, zero variance, fewer than three points); "defined
but too thin to print" is a different claim, and collapsing the two would throw away the
counts the page renders in its place. So `AestheticRatingPage` carries three constants:

- `MIN_CEILING_PAIRS` — 10 pairs.
- `MIN_BOUNDARY_PER_SIDE` — 5 images **per side**, never the product: one image against
  twenty is twenty non-independent comparisons a single score decides.
- `MIN_RHO_IMAGES` — 20 rated-and-scored, where ρ's standard error is about 0.23 against
  **0.71** at the three points `spearman` merely calls defined.

Below its floor each renders an em dash plus the counts that would fill it; `pairs`, `n`,
`n_lo`, `n_hi` and `n_by_rating` are on the wire for exactly that — the last so a tier mean
over one image cannot read like a mean over two hundred. Below `MIN_RHO_IMAGES` the
*ceiling* is withheld with the ρ: from three images in three tiers it computes to 1.00 and
reads as headroom when it only means nothing tied. `n_lo`/`n_hi` are rendered **beside the
bar**, not in a `title=` tooltip no screen reader announces, and the bar is `aria-hidden`:
the number next to it is the datum.

The page is a routed, dataset-free page, so it touches the eight sites in
`docs/dev/panes-routing.md` § Route-level code splitting. A write invalidates it through
`invalidateRatingMetrics` in `frontend/src/constants/queryKeys.ts`.

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
| Event log | `backend/models/rating_event.py`, migration `d4a2c9e6b8f3` |
| Metrics (pure numpy) | `backend/ml/rating_metrics.py` |
| Metric endpoints | `backend/routers/rating.py`, `backend/schemas/rating.py` |
| The Aesthetic Rating page | `frontend/src/pages/AestheticRatingPage.tsx`, `frontend/src/api/rating.ts` |
| Metric invalidation | `frontend/src/constants/queryKeys.ts::invalidateRatingMetrics` |
| Tests | `backend/tests/test_rating_events.py`, `test_rating_metrics.py`, `test_rating_metrics_http.py`, `frontend/e2e/rating-page.spec.ts` |
