# Roadmap — scoring models, learned style head, token UI

Five related threads scoped in a design session on 2026-08-01, not yet implemented. They
share one spine: a **marker/picker layer** that records which model produced a stored
number, which four of the five depend on.

**Lifecycle**: this file is transient, like the video arc's `roadmap.md` (retired in
`dd53b13`) and the detection/SAM 3 roadmap (retired in `f31a9dc`). When a thread lands,
move its durable rationale into the relevant `docs/dev/` topic file and delete the thread
from here; delete the file when all five are done. It sits in a subfolder deliberately —
`scripts/check_docs.py` globs `docs/dev/*.md` non-recursively, so like
`docs/dev/postmortems/`, files here carry no Documentation Map row and no word budget.

Nothing here is decided by the code yet. Read `CLAUDE.md` and the topic files named per
thread before implementing, and prefer the repo's conventions over the sketches below.

## Sequencing

Ordered by what unblocks what, not by interest:

| # | Step | Depends on | Notes |
|---|---|---|---|
| 0 | **Phase 0 gate** (§4) | — | No code. May cancel §4 entirely. |
| 1 | Token UI (§5) | — | Small, self-contained, unblocks gated models. |
| 2 | Marker/picker layer (§2) | — | The spine. |
| 3 | Aesthetic Predictor V2.5 (§1) | 2 | |
| 4 | Learned style head (§4) | 0, 2 | Only if the gate fails. |
| 5 | DINOv3 as opt-in backend (§3) | 1, 2 | Lowest priority. |

Step 0 is first because it is free and its outcome reshapes the largest chunk of work in
the batch.

## What was verified, and what was not

Cheap to get wrong twice, so recorded explicitly.

**Verified by reading code, the HF API, or package metadata:**

- Current aesthetic scorer is LAION's `sac+logos+ava1-l14-linearMSE` MLP over CLIP
  ViT-L-14 (`backend/ml/aesthetic_scorer.py`, `download_weights`).
- The `aesthetic` `ModelEntry` is a **three-tenant object**: its `clip` key also serves
  zero-shot watermark scoring and `clip_embedding` extraction
  (`backend/routers/quality.py`). No aesthetic swap removes CLIP.
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

**Not verified — check before relying on it:**

- `waifu-scorer-v3`'s exact layer ordering (ReLU/Dropout placement between the layers its
  tensor shapes reveal). No reference implementation was found. See §1.
- Whether V2.5 or DINOv3 is actually better *on this user's footage*. Both claims are
  upstream marketing, unmeasured here.
- DINOv3's benefit for a **global CLS-token** style embedding. Its headline gains are on
  dense tasks; Crucible only uses the CLS token.

## 1. Aesthetic Predictor V2.5

Read `docs/dev/scoring.md` and `docs/dev/ml-models.md` first.

**Decided**: install via pip, do not vendor. Zero new dependencies (bump `accelerate` to
`>=0.30`), no AGPL redistribution question, no upstream header to maintain.

**Decided**: ship the picker with LAION (current default) **plus V2.5**. Hold
`waifu-scorer-v3` for a follow-up — its head would have to be reconstructed from tensor
shapes, and a head assembled from guesswork produces plausible numbers that are quietly
wrong. Add it once its outputs are validated against a reference.

Costs that are not optional:

- SigLIP so400m is ~400M params **on top of** the existing CLIP. Grow `_evict_lru(3500)`,
  the `vram_mb=3500` floor, and the registry row (`"LAION Aesthetic Predictor"`) in
  `backend/ml/model_manager.py`.
- V2.5's score distribution is not comparable to v2's. See §2.

## 2. Marker / picker layer — the spine

**Decided**: add a marker column recording which model produced each stored number
(`aesthetic_model` for the score; the same idea on the embedding columns for §3). NULL
means the legacy LAION scorer.

**Decided**: do **not** force a global re-score. Extend the existing `score_coverage`
pattern to report coverage *per model*, and let the Quality page offer to re-score rows
whose marker differs from the current selection. Mixed scales become visible and fixable
rather than silent.

Wiring a new `*_score` column is not free in this repo — `CLAUDE.md` § Key invariants is
authoritative, but in short:

- Mirror it on `VersionImageState`, and add it to both `_DIFF_COLS` and
  `_DIFF_COMPARE_FIELDS` in `backend/services/version_service.py`. A `*_score` counts as
  authored data; `test_video_lineage_mirrors.py` fails CI if the mirror is missing.
- Wire `_ALLOWED_SCORE_FIELDS`, `dataset-stats`, `score-values`, and the TypeScript types.
  `backend/tests/test_luminance_score_http.py` is the template — it pins the *wiring*,
  which is where a new score field actually breaks.

Consumers of `aesthetic_score` that a scale change affects: the export `aesthetic_min`
filter (`backend/services/export_service.py`), the StatsPage histogram, gallery sort, and
`rankForKeepBest` in duplicate resolution — where a silent scale mix means deleting the
wrong image.

## 3. DINOv3 — deferred, opt-in only

Read `docs/dev/image-similarity.md`.

**Decided**: not as the default, and not in the same change as §4. Manual Meta approval as
a prerequisite for quality scoring is a worse regression than the model is an upgrade, and
swapping the backbone mid-experiment makes a §4 gate failure unattributable. The repo has
already been burned by gated models — `docs/dev/detection-inference.md` records SAM 3
loading from an ungated mirror while an HF access appeal sits pending.

**The trap, if it is ever done**: DINOv3 ViT-B/16 has the same `12 × 768` geometry, so
`_LAYER_BLOB_SIZE`'s length guard in `backend/ml/dino_scorer.py` passes for **both**
models. Existing DINOv2 blobs would be cosine-compared against DINOv3 blobs — two
unrelated embedding spaces — producing meaningless rankings with no error and no visibly
wrong number. The marker column from §2 is load-bearing here, not a nicety.

Register tokens sit at indices 1–4 with CLS still at 0, so `last_hidden_state[:, 0, :]` and
the `range(1, 13)` hidden-states loop both survive unchanged.

Touchpoints when it happens: `model_name` in `backend/ml/model_manager.py`,
`_LAYER_BLOB_SIZE`/`slice_layer_embedding`, the layer table in
`docs/dev/image-similarity.md`, the scorer row in `docs/dev/scoring.md`, and the
DINOv2-conditional per-layer row in `QualityPage` plus `frontend/e2e/quality.spec.ts`.

## 4. Learned per-project style head

Source: a handoff spec supplied by the user (Bradley-Terry linear head over frozen
embeddings, trained from pairwise labels). Read `docs/dev/image-similarity.md`,
`docs/dev/scores-stale.md` and `docs/dev/panes-routing.md`.

### Phase 0 is already implemented — run the gate before building anything

`compute_style_similarity` in `backend/ml/similarity_scorer.py` **is** the spec's centroid
baseline, step for step: stack references → mean → L2-renormalise → cosine. Embeddings are
already L2-normalised at store time in both `aesthetic_scorer.py` and `dino_scorer.py`, so
even the pre-normalisation matches. It is exposed at `POST /quality/style-similarity`,
writes `style_similarity_score`, is indexed, and has a reference-picker UI on QualityPage.

**The gate**: surface top-20 and bottom-20 by that score and judge whether the ordering
matches the user's sense of style quality. Run it in all three embedding modes (`dino`,
`clip`, `combined`). If it passes, ship the baseline and skip everything below — the
labeling apparatus exists only to beat it.

This is a judgement call only the user can make, and it is the highest-leverage decision
in the batch: passing removes the largest single chunk of work in these five threads.

### Corrections to the handoff spec

- **There is no content hash on `Image`.** Key preference pairs on `Image.id` with
  `ON DELETE CASCADE`. Keying on `phash` would merge distinct frames of the same shot —
  exactly the frames the head must tell apart. `VersionImageState.image_id` preserves
  `Image.id` across snapshots, so uuid keys survive restores.
- **Do not add scikit-learn.** Implement PCA + Bradley-Terry in numpy/scipy (both already
  declared). This also puts the head in the same CI-testable, torch-free class as
  `similarity_scorer.py`.
- **The DINOv2 here is `dinov2-base` (768-dim)**, not `dinov2-large` (1024). The spec's
  dimension arithmetic and its overfitting argument should be restated against 768.
- The "model-loading harness from the detection cascade" is `backend/ml/model_manager.py`.

### Decisions on the spec's open questions

- **Embeddings**: start with what is already cached — DINOv2-base and CLIP. The three
  existing `embedding_type` modes give a free A/B. Do **not** add SigLIP embeddings for
  v1; that is a third backbone and a full re-embed.
- **Scope**: per-dataset, not per-branch. Every other score column is
  per-image-within-dataset, and per-branch needs a new relation into `versions`.
- **Retraining**: version and keep both, via §2's marker mechanism.
- **`scores_stale`**: the head score joins `_UNREFRESHABLE_SCORE_COLUMNS` alongside
  `style_similarity_score` — a quality job cannot refresh it, since it needs a trained
  head. See `docs/dev/scores-stale.md`.
- **The "never a filter" rule**: the spec bans it outright, justified as matching an
  existing convention. That justification does not hold — this repo stores component
  scores *and* filters on them. Draw the line where the hazard actually is: **allow**
  gallery sort and gallery filtering (visible, reversible), **exclude** it from the export
  filter table (omission there is destructive and invisible). Comment the carve-out so
  nobody later "completes" the wiring.

### Where the cost actually is

The head is ~150 lines of numpy. The **800-pair labeling UI** is the expensive part: a new
routed page, keyboard-driven with undo, session tracking and consistency replays — the
six-site routed-page checklist in `docs/dev/panes-routing.md`, plus a persistence key and
an e2e spec. Likely more work than §1, §2 and the head combined. The spec does not
estimate it.

Two checks the spec asks for are cheaper than it knows: `luminance_score` already exists as
a column, so its luminance-correlation check is a two-column SQL query with no decoding;
and `source_video_id` is an indexed FK that already means "same episode", which is the
spec's `source_group` constraint.

## 5. Token management UI

Read `docs/dev/settings.md`. This is the smallest thread and has an exact in-repo
precedent.

**Decided — precedence**: DB value wins when non-empty, otherwise fall back to the existing
OS-env/`.env` chain. Conventional 12-factor says env beats everything; that is wrong here,
because a token typed into a field being silently overridden by an env var is the most
confusing possible failure. Show the source in the UI ("inherited from `.env`") so
precedence is readable rather than remembered.

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
  will not overwrite what `backend/main.py` already put there. New downloads then pick it
  up with no restart.
- **Clearing is the awkward path**: it must actively restore the `.env`/env-var value, or
  the override lingers in the process until a restart. Worth a test.
- **Pin the mask-echo case in a test.** If the form round-trips `api_key_masked` into a
  PATCH, the literal asterisks are saved as the token and it fails invisibly.
  `OpenAIProviderUpdate` dodges this via `exclude_none=True` plus null-for-unchanged.

Security context: the API is unauthenticated and binds `0.0.0.0`, so this is a new
LAN-writable surface. It does not make things worse — `GET /filesystem/list` already takes
an arbitrary path and `/roots` enumerates drives, so `.env` is already reachable, and the
provider tab already accepts LAN-writable keys. Masked-read plus write-only is strictly
better than a plaintext GET.

User-facing docs are part of this change, per `CLAUDE.md`: `README.md` currently documents
`HF_TOKEN=hf_...` in `.env`, and `docs/dev/settings.md` says the page has seven tabs.

## Traps worth restating

- A stem-keyed weights cache file named for a model it does not contain (§1).
- Two embedding spaces sharing a byte-identical blob size (§3).
- A preference pair keyed on a hash designed to collide (§4).
- A masked secret echoed back as its own new value (§5).

Every one of these fails **silently**. None produces an exception, a 500, or a visibly
wrong number.
