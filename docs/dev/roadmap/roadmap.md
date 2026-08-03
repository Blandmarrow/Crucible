# Roadmap — aesthetic rating, the model picker, the learned head

Scoped in design sessions on 2026-08-01 and 2026-08-02, not yet implemented. The 2026-08-02
session collapsed what were three separate threads (V2.5, the marker layer, the learned
style head) into **one feature shipping in three stages**, and added a manual rating column
that was in none of them. One thread remains independent: DINOv3 (§5). The token UI (§6)
**shipped on 2026-08-02** and its section is deleted per the lifecycle rule below; its
durable rationale now lives in `docs/dev/settings.md` § API Keys tab. The rating column (§1)
**shipped on 2026-08-03**, likewise deleted, its rationale now in `docs/dev/rating.md`.

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

**Section numbers are stable identities, not an order.** Everything below refers to a piece
of work as §N, and those numbers never move; the build order is this table alone, set on
2026-08-02.

| Order | Stage | Depends on | Notes |
|---|---|---|---|
| ✓ | **Token UI** (§6) | — | **Shipped 2026-08-02.** Settings → API Keys. |
| ✓ | **Aesthetic model picker** (§2) | — | **Shipped 2026-08-02.** `Image.aesthetic_model` + Aesthetic Predictor V2.5 + the two consumer guards. Now documented in `docs/dev/scoring.md`; this section is deleted per this file's own lifecycle rule. |
| ✓ | **Rating column** (§1) | — | **Shipped 2026-08-03.** `Image.aesthetic_rating` + `rating_stale`, the gallery/export/stats/restore surfaces. Now documented in `docs/dev/rating.md`; this section is deleted per this file's own lifecycle rule. |
| 3 | **The learned head** (§3) | §1, §2 | The expensive one. |
| — | DINOv3 (§5) | §2 | Lowest priority, still deferred. (§6, its other dependency, has shipped.) |

**§6 led on a code reason, not just its size**, and that reason is now settled: it removed
the last reader of `settings.hf_token` inside `backend/ml/model_manager.py`. §2 added new
model-loading code to that same module, and it inherited a module with **no** HF-token
plumbing at all — `_load_paligemma2_sync` passes no `token=` and, like the other loaders,
relies on the ambient `HF_TOKEN` that `services/secrets_service.py::sync_env` maintains.
`_load_aesthetic_v2_5_sync` did the same, and any further loader should.
(One assumption in the original §6 was wrong and is worth not repeating: the `settings`
singleton is **not** frozen — it is a plain mutable pydantic instance. Nothing may assign to
it, but that is a chosen invariant, not something the type enforces.)

**§1's argument held all the way through**: it was a hard prerequisite for §3 *and* stood
alone, so if it turns out nobody rates anything, that is discovered for the price of a
column rather than after building a trainer. §3 now has the label column it needs; whether
it is worth building is a question the ratings themselves answer.

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
  the consumer — §2 shipped exactly that for its own two producers, so §3 inherits the
  guards rather than inventing them. See `docs/dev/scoring.md` § The marker is a safety
  device, and write `head:{uuid}` into the same `Image.aesthetic_model` column.
- **Pairwise labeling is cut.** Tier sort only; see § Why tier sort.
- **Scope is global-with-override**, not per-dataset. This was decided about §3's *head*
  alone. §2's model selection was never covered by it and was settled the other way — a
  per-run choice with a sticky default, no scope concept at all, and that is what shipped.
  See `docs/dev/scoring.md` § The aesthetic model picker.
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
- The weights cache file used to be named `aesthetic_predictor_v2_5.pth`
  (`backend/ml/model_manager.py`) while holding LAION weights. **Closed by §2**, which
  renamed it to `laion_aesthetic_sac_logos_ava1_l14.pth` in its own first commit. The
  rename was free because nothing but `load_aesthetic` read that path — no DB reference, no
  migration, and `download_weights` re-fetches from the HF cache whenever the file is
  absent. (The orphaned ~5 MB file is deliberately left on disk: there is no rename
  primitive there, only a path constant, and an unlink would put a fallible unowned
  filesystem mutation inside a loader running in an executor thread.) The collision it
  guarded against turned out to be **latent rather than live**: the real V2.5 head does use
  that exact filename, but the `aesthetic-predictor-v2-5` package fetches it through
  `torch.hub.load_state_dict_from_url`, so it lands in the torch hub cache and never in
  `models_cache_dir`. See `docs/dev/ml-models.md`.
- **`QualityPage` already persists a global, cross-dataset "workflow" blob**
  (`QUALITY_WORKFLOW_KEY`, whose own comment says "global, shared across all datasets"),
  holding `embeddingType` and `dinoLayer` — mutually exclusive *model* choices made per run
  and remembered as a default. This is the precedent §2's picker followed — `aestheticModel`
  joined that blob.
- `SCORING_OPTIONS` in `QualityPage.tsx` is a flat list of seven checkboxes with a per-row
  `vram` constant. The aesthetic row's label was the literal `"Aesthetic score · LAION"`;
  §2 dropped the model name from it and put the choice in a sub-row below the grid — **not**
  a control inside the row, which is a click-to-toggle `<label>` in a tight two-column flex
  grid. Grouping or categorising the scorer list is a real question but was not §2's; three
  producers do not need a category tree. Revisit at the fourth.
- `score_coverage` is `dict[str, int]` (`backend/schemas/dataset.py`), so per-model keys
  need no schema change and reach the StatsPage CSV export generically — but the coverage
  *panel* iterates a fixed `coverageDefs` list, which a new key must be added to.
- `rankForKeepBest` is already null-safe: scored images rank descending, unscored ones hold
  their incoming order behind all of them, and the caller disables the button outright when
  nothing in the group is scored. Mixed *markers*, not nulls, are the new failure.
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

## 3. The learned head — last

Source: a handoff spec supplied by the user (Bradley-Terry linear head over frozen
embeddings). Read `docs/dev/image-similarity.md`, `docs/dev/scores-stale.md` and
`docs/dev/panes-routing.md`.

### Where it lives

A new **top-level** routed page, "Aesthetic Rating", beside Datasets and File Browser — not
a per-dataset one, because a head trained from labels pooled across datasets cannot live
under a single dataset. The per-dataset half is the picker §2 shipped (see
`docs/dev/scoring.md` § The aesthetic model picker), a panel on the existing
Score images page. One new routed page, not two; note the *seventh* site in
`docs/dev/panes-routing.md`'s checklist, since this page is pickable from `PaneHeader`.

**§1 shipped without it.** Scope was confirmed as existing surfaces only — the gallery's
keys and chips, the selection toolbar, `ImageDetailPage` — so this page arrives with §3, and
arrives already carrying a rated corpus rather than an empty queue.

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

## Still open

Everything remaining is a §3 question. What used to lead this list — where the aesthetic
model selection lives — was settled as a per-run choice with a sticky default and shipped
with §2; it is now documented behaviour in `docs/dev/scoring.md`, not a question.

- How large is the anchor set beyond the ten-per-bucket floor, and does the schema commit to
  per-user or per-install? Larger is more stable and costs re-rating it every session.
- Whether the head's uncertainty sampling should weight the boundary the user acts on
  (Probably-not / Cut) above the others.

## Traps worth restating

- Two embedding spaces sharing a byte-identical blob size (§5).
- A masked secret echoed back as its own new value (§6, shipped) — closed structurally
  rather than by convention: the read shape nests (`{masked, source}`) while the write shape
  is plain strings, so echoing a GET into a PATCH is a **422**, not a silent save.
- **Reusing `scores_stale` for ratings** (§1, shipped) — different clear predicates, so a
  routine re-score would mark stale ratings trustworthy. Closed by giving the rating its own
  `rating_stale` bit; see `docs/dev/rating.md` § Two staleness bits, not one.
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
