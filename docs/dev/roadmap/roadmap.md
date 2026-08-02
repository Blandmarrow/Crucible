# Roadmap — aesthetic rating, the model picker, the learned head

Scoped in design sessions on 2026-08-01 and 2026-08-02, not yet implemented. The 2026-08-02
session collapsed what were three separate threads (V2.5, the marker layer, the learned
style head) into **one feature shipping in three stages**, and added a manual rating column
that was in none of them. Two threads remain independent: DINOv3 (§5) and the token UI (§6).

**Lifecycle**: this file is transient, like the video arc's `roadmap.md` (retired in
`dd53b13`) and the detection/SAM 3 roadmap (retired in `f31a9dc`). When a stage lands, move
its durable rationale into the relevant `docs/dev/` topic file and delete the stage from
here; delete the file when everything is done. It sits in a subfolder deliberately —
`scripts/check_docs.py` globs `docs/dev/*.md` non-recursively, so like
`docs/dev/postmortems/`, files here carry no Documentation Map row and no word budget.

Nothing here is decided by the code yet. Read `CLAUDE.md` and the topic files named per
stage before implementing, and prefer the repo's conventions over the sketches below.

## What this feature is

You rate images **Keep / Probably / Probably not / Cut**. That rating is a real column on
`Image`, useful on its own for filtering, sorting and exporting. Later, a Bradley-Terry head
over already-cached embeddings learns to predict it, extending your judgement to everything
you have not rated by writing the *existing* `aesthetic_score` column.

The head does the same job LAION's scorer does today. That is why it is not a new kind of
number — it is a third **producer** of one that already exists, which is what collapses the
three threads and deletes the entire new-score-column checklist for the head itself.

## Sequencing

| # | Stage | Depends on | Notes |
|---|---|---|---|
| 1 | **Rating column** (§1) | — | Ships alone. Useful without any ML. |
| 2 | **Aesthetic model picker** (§2) | — | Marker layer + V2.5. Independent of §1. |
| 3 | **The learned head** (§3) | 1, 2 | The expensive one. |
| — | Token UI (§6) | — | Small, self-contained, unblocks gated models. |
| — | DINOv3 (§5) | 2, 6 | Lowest priority, still deferred. |

Stage 1 leads because it is a hard prerequisite for stage 3 *and* stands on its own: if it
turns out nobody rates anything, that is discovered for the price of a column rather than
after building a trainer.

## Decisions overturned on 2026-08-02

Recorded so they are not reintroduced by someone reading an older draft:

- **The head gets no new score column.** It writes `aesthetic_score` with a marker. The
  previous plan's `_ALLOWED_SCORE_FIELDS` / `dataset-stats` / `score-values` / TypeScript
  wiring applies to the *rating* column instead, not to the head's output.
- **The head is not in `_UNREFRESHABLE_SCORE_COLUMNS`.** With a picker, a quality run *can*
  refresh it — it runs whichever model the dataset selected.
- **The "never a filter" rule is gone**, because sharing the column makes it unenforceable:
  `aesthetic_min` and `rankForKeepBest` read `aesthetic_score` and therefore consume the
  head's output whether or not anyone wires them. The protection moves from the column to
  the consumer — see § The marker is a safety device.
- **Pairwise labeling is cut.** Tier sort only; see § Why tier sort.
- **Scope is global-with-override**, not per-dataset.
- **Labeling is not the expensive part any more.** The previous estimate ("an 800-pair
  labeling UI, likely more work than everything else combined") assumed pairwise. Tier sort
  over a rating column that already exists is a fraction of it.

## What was verified, and what was not

Cheap to get wrong twice, so recorded explicitly.

**Verified by reading code, the HF API, or package metadata:**

- Current aesthetic scorer is LAION's `sac+logos+ava1-l14-linearMSE` MLP over CLIP
  ViT-L-14 (`backend/ml/aesthetic_scorer.py`, `download_weights`).
- The `aesthetic` `ModelEntry` is a **three-tenant object**: its `clip` key also serves
  zero-shot watermark scoring and `clip_embedding` extraction
  (`backend/routers/quality.py`). No aesthetic swap removes CLIP — which is why LAION is
  free to keep in the picker.
- The weights cache file is already named `aesthetic_predictor_v2_5.pth`
  (`backend/ml/model_manager.py`) while holding LAION v2 weights — the same filename the
  real V2.5 uses. Rename before adding V2.5 or the two collide.
- Aesthetic Predictor V2.5 is SigLIP-so400m-patch14-384 plus an MLP head, AGPL-3.0, pip
  package `aesthetic-predictor-v2-5`, last released 2024-12-18.
- Crucible is AGPL-3.0 (`LICENSE`, full FSF text), so V2.5 is licence-compatible.
- `accelerate>=0.27` is **already declared** in `backend/requirements.txt`, so V2.5's only
  real dependency needs a floor bump, not a new entry.
- `google/siglip-so400m-patch14-384` is `apache-2.0`;
  `openai/clip-vit-large-patch14` (what we use today) declares **no licence at all**.
- `Eugeoter/waifu-scorer-v3` takes **768-dim input** — read from its safetensors header —
  i.e. CLIP ViT-L/14 embeddings, the backbone already loaded. Head is
  768→2048→512→256→128→32→1 with BatchNorm. Licence `openrail`.
- `shadowlilac/aesthetic-shadow-v2` returns **401** — withdrawn by its author. Surviving
  mirrors are `cc-by-nc-4.0` or declare nothing.
- All `facebook/dinov3-*` repos are **`gated: manual`**; an unauthenticated `config.json`
  fetch returns 401. Licence is `other` (custom DINOv3 Licence), not Apache-2.0.
- DINOv3 ViT-B/16 is `hidden_size=768`, `num_hidden_layers=12`, `num_register_tokens=4`,
  `patch_size=16` (read from an ungated ONNX mirror) — **byte-identical blob geometry** to
  the current DINOv2-base.
- `Image.id` is a **uuid4 string**, not a content hash. `phash` is a *perceptual* hash that
  deliberately collides on near-duplicates.
- `scikit-learn` is **not** a declared dependency — present only transitively via
  `sentence-transformers`, which requires torch. `backend/requirements-ci.txt` is
  deliberately torch-free. `numpy` and `scipy` **are** declared.
- `OpenAIProvider.api_key` already stores a secret in the DB, edited from the Settings
  page, and `OpenAIProviderOut` returns only `api_key_masked` — never the plaintext.
- **Nothing in Crucible auto-creates a snapshot.** `versioning_mode: "auto"` selects the
  copy-on-write *storage* strategy, not automatic snapshotting; `DatasetVersion.source` is
  `Literal["manual", "pre_restore", "branch_init"]`. A rating session therefore cannot
  flood the version list, and no "should ratings trigger snapshots" question arises.

**Not verified — check before relying on it:**

- `waifu-scorer-v3`'s exact layer ordering (ReLU/Dropout placement between the layers its
  tensor shapes reveal). No reference implementation was found. Still held back.
- Whether V2.5 or DINOv3 is actually better *on this user's footage*. Both claims are
  upstream marketing, unmeasured here.
- DINOv3's benefit for a **global CLS-token** style embedding. Its headline gains are on
  dense tasks; Crucible only uses the CLS token.
- The claim that tier sort is "3-4× faster per image" than pairwise, which an earlier draft
  of this file asserted. Per *decision* the two are comparable. The real argument for tier
  sort is information per decision, not speed — see § Why tier sort.

## 1. Stage 1 — the rating column

Read `docs/dev/gallery.md`, `docs/dev/image-filters.md`, `docs/dev/bulk-ops.md`,
`docs/dev/export.md` and `docs/dev/versioning-service.md`.

**Decided — four buckets, decision language.** `Keep / Probably / Probably not / Cut`,
stored as a nullable small int 1–4 in `Image.aesthetic_rating`.

- **Four, not five**, deliberately: an odd number gives a middle bucket, and a middle bucket
  is where everything lands. No neutral option forces a call to one side.
- **Decision language, not quality language** ("Keep", not "Great"). It is far easier to
  answer consistently because it is a call you already make. The cost is that it is
  *contextual* — "Keep" in a weak dataset is worse than "Cut" in a strong one — which is
  what the anchor set in §3 exists to absorb. If §3 is never built, that cost never
  materialises, because a rating you read yourself needs no cross-dataset calibration.

**Decided — it is authored, mutable data.** Therefore mirrored on `VersionImageState` *and*
present in both `_DIFF_COLS` and `_DIFF_COMPARE_FIELDS`, the same treatment `scores_stale`
gets and for the same reason. `test_video_lineage_mirrors.py` fails CI without the mirror.

**Decided — `rating_stale` is its own bit, never `scores_stale`.** They have different clear
predicates: `scores_stale` is cleared by a quality run that actually re-measured, whereas a
stale *rating* can only be cleared by a human looking at the image again — no job can do it.
Sharing the bit lets a routine re-score mark stale ratings trustworthy, which then feeds
labels about deleted pixels into §3's fit. `utils.record_in_place` stays the single writer
of both, so the existing invariant in `CLAUDE.md` is extended, not broken.

**Decided — travel rules.** The rating travels on cross-dataset move and copy (moving a file
does not change your opinion of it, so it behaves like a caption). Derivatives — crop,
upscale, LUT, crop-to-detection — do **not** inherit it: a derivative has different pixels
and deserves its own call, and inheriting would put an unjudged image into §3's training set
under the user's name. This makes `aesthetic_rating` a new entry in the field-by-field
rebuild paths that `CLAUDE.md` § Key invariants describes, with the opposite disposition to
`source_video_id`.

Sites in this stage:

| Site | What lands |
|---|---|
| `Image.aesthetic_rating`, `Image.rating_stale` | Migration, model, `VersionImageState` mirror, both diff lists |
| Gallery | Card badge, filter chips, sort option, keyboard <kbd>1</kbd>–<kbd>4</kbd> on the selection |
| `SelectionToolbar` | Bulk assign across a selection and across select-all-matching-filters |
| `ImageFilterParams` | Filter param shared by `GET /images/`, `/count` and `/ids` |
| Export | Include **and** exclude by rating |
| Stats | Rating distribution and the unrated count |

**The include-filter hazard is real and was accepted knowingly.** "Only Keep" over a dataset
where 214 of 1,970 images are rated exports 214 images and reports success — nothing errors,
and the export is 89% short of what was meant. This is precisely the failure the earlier
"never a filter" rule existed to prevent. The mitigation is that the filter must state its
own population against the unrated count *before* it runs ("214 match · 1,715 unrated and
therefore excluded"), not that the filter is withheld.

## 2. Stage 2 — the aesthetic model picker

Read `docs/dev/scoring.md` and `docs/dev/ml-models.md`.

**Decided**: a marker column (`aesthetic_model`) recording which model produced each stored
`aesthetic_score`. NULL means the legacy LAION scorer. The picker lives on the Score images
page and offers LAION, Aesthetic Predictor V2.5, and (after §3) the user's head.

**Decided**: do **not** force a global re-score. Extend the existing `score_coverage`
pattern to report coverage *per model*, and offer to re-score rows whose marker differs from
the current selection. Mixed scales become visible and fixable rather than silent.

**Decided — a quality run with "Aesthetic" checked runs the dataset's selected model.**
Otherwise the picker is decorative and a routine re-score silently reverts head scores to
LAION. A row lacking the embedding the selected model needs is **reported unscored**, never
guessed — the failure contract in `docs/dev/scoring.md` already says NULL rather than a
fabricated number.

**Decided — V2.5 stays in.** It is the only item here with a real VRAM cost, and it earns it
as a stronger baseline than LAION to measure the head against. Costs that are not optional:
SigLIP so400m is ~400M params **on top of** the existing CLIP, so grow `_evict_lru(3500)`,
the `vram_mb=3500` floor, and the registry row (`"LAION Aesthetic Predictor"`) in
`backend/ml/model_manager.py`. Its score distribution is not comparable to LAION's.

### The marker is a safety device, not bookkeeping

Sharing `aesthetic_score` means the head reaches two **destructive** consumers for free, on
datasets where half the rows may still carry a LAION number on a different scale:

- `aesthetic_min` in `backend/services/export_service.py` — omits images, invisibly.
- `rankForKeepBest` in duplicate resolution — **deletes** images, which is sharper.

Since no carve-out is available, every consumer that discards or deletes must read the
marker and refuse or warn on a mixed set. The StatsPage histogram and gallery sort are the
benign consumers and need only to render the marker.

## 3. Stage 3 — the learned head

Source: a handoff spec supplied by the user (Bradley-Terry linear head over frozen
embeddings). Read `docs/dev/image-similarity.md`, `docs/dev/scores-stale.md` and
`docs/dev/panes-routing.md`.

### Where it lives

A new **top-level** routed page, "Aesthetic Rating", beside Datasets and File Browser — not
a per-dataset one, because a head trained from labels pooled across datasets cannot live
under a single dataset. The per-dataset half is the picker from §2, a panel on the existing
Score images page. One new routed page, not two; note the *seventh* site in
`docs/dev/panes-routing.md`'s checklist, since this page is pickable from `PaneHeader`.

The page exists from stage 1 (rating queue, history, bulk work) and grows a **Train** tab in
stage 3.

### Why tier sort

All labeling modes feed the identical Bradley-Terry fit — it only ever consumes
`(winner, loser)` pairs — so a mode differs in how many *independent* decisions back the fit,
whether its scale drifts, and what the head can rank.

| Mode | 800 actions yields | Scale | Cannot learn |
|---|---|---|---|
| Pairwise | 800 real, independent pairs | Relative, cannot drift | — (weakness is *which* pairs get shown) |
| Tier sort | ~128,000 synthetic cross-bucket pairs from 800 decisions | Absolute, drifts | Ordering *inside* a bucket |
| Grid triage | ~154,000 synthetic from 800 binary calls | Absolute, two levels | Ordering inside keep or inside drop |

**Decided — tier sort only.** A tier assignment positions an image against every image
already tiered, while a pairwise click positions it against exactly one. Its two weaknesses
are both patchable — drift by the anchor set, within-bucket blindness by a later pairwise
pass — whereas pairwise's weakness, needing several times the labels to converge, is not.
It is also *inspectable*: a Keep bucket can be reviewed, a pile of pairwise verdicts cannot.
Add pairwise later only if accuracy plateaus below the self-agreement ceiling.

**Grid triage is not a training input.** As a source of signal it is a two-bucket judgement
wearing a grid. It has a place as a fast way to remove junk from the labeling pool.

### The anchor set

A fixed set of images — roughly ten per bucket — re-rated at the start of and **interleaved
through** every session. It does three jobs, which is why it cannot be casual:

1. Pins the **1–10 scale**, so scores are comparable across datasets.
2. Supplies a **per-session offset** absorbing both drift and the contextual slide in what
   "Keep" means.
3. Locates the **bucket boundaries** between the four labels — which is why it must contain
   images the user would genuinely place in all four.

Interleaved rather than front-loaded, because selectivity shifts mid-session and only spread
anchors catch that.

**Decided — editing the anchor set mints a new head version** (`base-v3` → `base-v4`) and
forces a re-score, with the confirm dialog stating the blast radius in rows and datasets.
The marker column keeps the old scores coherently labelled, so nothing silently changes
meaning.

### Decisions on the spec's open questions

- **Output**: the head writes `aesthetic_score` on a 1–10 scale mapped via the anchor set,
  and the frontend renders it bucketed into the same four words as authored ratings, marked
  as predicted. One stored number, one vocabulary. Predicted ratings are shown in the
  Gallery **by default**.
- **Embeddings**: start with what is already cached — DINOv2-base and CLIP. Do **not** add
  SigLIP embeddings for v1. An earlier draft claimed the three existing `embedding_type`
  modes give a free three-way A/B; the Phase 0 gate measured them and they do not —
  `combined` is DINOv2 with a slight CLIP tilt (Spearman ρ 0.98, 18 of 20 top images
  shared). **CLIP vs DINOv2 is the only real axis**, and it is a two-way A/B. See
  `docs/dev/image-similarity.md` § What the modes are actually worth.
- **Scope**: a global base head plus optional per-dataset overrides. An override **wins
  outright** where it exists (a hard switch, chosen for predictability). Because that lets a
  head fit from 300 ratings outrank one fit from 1,200, the heads table must show each
  head's held-out accuracy plainly rather than presenting an override as a promotion.
- **Queue composition**: roughly 60% least-certain, 30% spread across the range, 10%
  interleaved anchors. Pure uncertainty sampling makes every image a hard call, which is
  exhausting and leaves the head uncorrected at the extremes.
- **Cold start**: the first session has no head, so the queue is spread across LAION's
  existing scores — the full quality range immediately instead of forty mediocre frames in a
  row. LAION picks what is *seen*, never what is *said*.
- **LAION's opinion is hidden while judging and revealed after committing**, so it cannot
  anchor the rating. The running disagreement figure is the first honest signal about
  whether a personal head is worth training at all.
- **Do not add scikit-learn.** Implement PCA + Bradley-Terry in numpy/scipy (both already
  declared). This also puts the head in the same CI-testable, torch-free class as
  `similarity_scorer.py`.
- **The DINOv2 here is `dinov2-base` (768-dim)**, not `dinov2-large` (1024). The spec's
  dimension arithmetic and its overfitting argument should be restated against 768.
- **Preference data keys on `Image.id`** with `ON DELETE CASCADE`. Keying on `phash` would
  merge distinct frames of the same shot — exactly the frames the head must tell apart.
  Labels dying with a deleted image is intended: the images are gone for a reason. One side
  effect is that a head's recorded accuracy becomes unreproducible after a cull, so it is a
  snapshot taken at fit time rather than something recomputed on view.
- The "model-loading harness from the detection cascade" is `backend/ml/model_manager.py`.

### The metrics that make this honest

Four tiles, three of which are nearly free:

- **Predicts your rating** — held-out accuracy. Alone, a vanity metric.
- **Your own ceiling** — re-show ~40 already-rated images and count how often the same
  answer comes back. Contradict yourself 12.5% of the time and a head at 84% is *at
  ceiling*; more rating buys nothing. Without this tile, 84% reads as "needs work" forever.
- **LAION on the same images** — the only honest answer to *is this better than what I had*.
  If the head fails to beat it meaningfully, stop.
- **Luminance correlation** — `luminance_score` already exists, so this is a two-column SQL
  query with no decoding. A head that has quietly learned "brighter is better" shows up here
  and nowhere else.

`source_video_id` is an indexed FK that already means "same shot", which is the spec's
`source_group` constraint at no cost.

## 5. DINOv3 — deferred, opt-in only

Read `docs/dev/image-similarity.md`.

**Decided**: not as the default, and not in the same change as §3. Manual Meta approval as a
prerequisite for quality scoring is a worse regression than the model is an upgrade, and
swapping the backbone mid-experiment makes a §3 result unattributable. The repo has already
been burned by gated models — `docs/dev/detection-inference.md` records SAM 3 loading from an
ungated mirror while an HF access appeal sits pending.

**The trap, if it is ever done**: DINOv3 ViT-B/16 has the same `12 × 768` geometry, so
`_LAYER_BLOB_SIZE`'s length guard in `backend/ml/dino_scorer.py` passes for **both** models.
Existing DINOv2 blobs would be cosine-compared against DINOv3 blobs — two unrelated embedding
spaces — producing meaningless rankings with no error and no visibly wrong number. A marker
on the embedding columns is load-bearing here, not a nicety.

Register tokens sit at indices 1–4 with CLS still at 0, so `last_hidden_state[:, 0, :]` and
the `range(1, 13)` hidden-states loop both survive unchanged.

Touchpoints when it happens: `model_name` in `backend/ml/model_manager.py`,
`_LAYER_BLOB_SIZE`/`slice_layer_embedding`, the layer table in
`docs/dev/image-similarity.md`, the scorer row in `docs/dev/scoring.md`, and the
DINOv2-conditional per-layer row in `QualityPage` plus `frontend/e2e/quality.spec.ts`.

## 6. Token management UI

Read `docs/dev/settings.md`. This is the smallest thread and has an exact in-repo precedent.

**Decided — precedence**: DB value wins when non-empty, otherwise fall back to the existing
OS-env/`.env` chain. Conventional 12-factor says env beats everything; that is wrong here,
because a token typed into a field being silently overridden by an env var is the most
confusing possible failure. Show the source in the UI ("inherited from `.env`") so precedence
is readable rather than remembered.

**Decided — empty means inherit, not "no token."** Clearing the field stops overriding; it
does not blank the token. This matches the house convention: `CLAUDE.md`'s provenance rule
has `resolve_provenance` coalesce on *falsiness*, so `""` means inherit there too.

**Decided — scope**: `hf_token`, `gelbooru_api_key`, `gelbooru_user_id`. Not
`ollama_base_url` or `max_vram_mb` — those are not secrets and do not want masked handling.

**Decided — no encryption at rest**, plus one line of honest copy. Encrypting with the key
beside the ciphertext is theatre, and the DB already holds provider API keys in plaintext.

Implementation notes:

- `ThresholdSettings` is the sanctioned home — `docs/dev/settings.md` states it is the
  catch-all single-row table for app-wide server-side settings.
- Copy `OpenAIProviderOut`'s masked-out / write-only shape exactly.
- `settings` is a frozen import-time singleton, so `settings.hf_token` cannot see a DB
  change. Read through a service accessor, as `threshold_service.get_thresholds()` does.
- On save, use `os.environ["HF_TOKEN"] = value` — **assignment**, not `setdefault`, which
  will not overwrite what `backend/main.py` already put there. New downloads then pick it up
  with no restart.
- **Clearing is the awkward path**: it must actively restore the `.env`/env-var value, or
  the override lingers in the process until a restart. Worth a test.
- **Pin the mask-echo case in a test.** If the form round-trips `api_key_masked` into a
  PATCH, the literal asterisks are saved as the token and it fails invisibly.
  `OpenAIProviderUpdate` dodges this via `exclude_none=True` plus null-for-unchanged.

Security context: the API is unauthenticated and binds `0.0.0.0`, so this is a new
LAN-writable surface. It does not make things worse — `GET /filesystem/list` already takes an
arbitrary path and `/roots` enumerates drives, so `.env` is already reachable, and the
provider tab already accepts LAN-writable keys. Masked-read plus write-only is strictly
better than a plaintext GET.

User-facing docs are part of this change, per `CLAUDE.md`: `README.md` currently documents
`HF_TOKEN=hf_...` in `.env`, and `docs/dev/settings.md` says the page has seven tabs.

## Still open

Carried forward from the 2026-08-02 session; none blocks stage 1.

- How large is the anchor set beyond the ten-per-bucket floor, and does the schema commit to
  per-user or per-install? Larger is more stable and costs re-rating it every session.
- Restoring an old snapshot reinstates ratings as of that snapshot, overwriting any given
  since. Correct, and surprising the first time — decide whether the restore modal says so.
- Whether the head's uncertainty sampling should weight the boundary the user acts on
  (Probably-not / Cut) above the others.

## Traps worth restating

- A stem-keyed weights cache file named for a model it does not contain (§2).
- Two embedding spaces sharing a byte-identical blob size (§5).
- A masked secret echoed back as its own new value (§6).
- **Reusing `scores_stale` for ratings** (§1) — different clear predicates, so a routine
  re-score would mark stale ratings trustworthy.
- **Synthetic pairs counted at face value** (§3) — 800 tier decisions yield ~128,000 pairs
  carrying 800 decisions' worth of information. Fit unweighted and the standard errors shrink
  by roughly 13×: the accuracy tile looks superb and the ordering is confidently wrong.
  Weight each pair by `1 / (pairs that image contributes)`.
- **Scores compared across a disconnected comparison graph** (§3) — Bradley-Terry identifies
  scores only up to an additive constant per connected component. Rate dataset A on Monday
  and B on Tuesday with no shared images and their two means compare nothing at all. The
  interleaved anchors are what make a cross-dataset number exist.
- **A predicted rating mistaken for an authored one** (§1, §3) — the visual distinction is
  the only thing separating the training set from the model's own output. Any surface that
  loses it (a bulk operation, an export manifest, a stats query counting both) closes a
  feedback loop where the head trains on itself: accuracy climbs while judgement degrades.

Every one of these fails **silently**. None produces an exception, a 500, or a visibly wrong
number.
