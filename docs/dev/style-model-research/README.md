# Style-model research — the plan

**Status: transient.** This is the plan for looking past CLIP and DINO for style similarity.
When it concludes, its durable findings move to `docs/dev/quality/style-similarity.md` and
this folder is deleted. Its place in the order is M7 in `docs/dev/roadmap.md`.

## Why

Style similarity ranks images by a centroid cosine over CLIP and DINOv2 embeddings
(`docs/dev/quality/style-similarity.md`). Neither model was trained to see style. DINO is
trained to *ignore* colour and texture changes, and CLIP sees style only through caption
words. The 2026-11-02 DINOv3 test showed that a newer general model does not fix this. On
seven illustrators it tied DINOv2 (`docs/dev/quality/style-similarity.md` § DINOv3 was
measured and not adopted). The gain left to find is a model, or a feature, built for style.

## First run (2026-11-03): Kaloscope and CSD pass

The owner picked three candidates after desk research: Kaloscope 3.0 (a DINOv3-B fine-tune
for Danbooru artist retrieval), CSD ViT-L and PE-Core L14-336. They were pre-registered and
tested against `0.30 CLIP + v2 L7` on a new anime set, `test4`. It holds seven Danbooru
artists that Kaloscope never trained on, 204 images. `test3` was the second check.

- **Kaloscope passed widely**: 0.999 against 0.832 on `test4`, and 0.906 against 0.791 on
  `test3`.
- **CSD passed modestly**: 0.888 and 0.842.
- **PE-Core tied.** IntroStyle and Gram matrices were dropped at the desk stage.
- **The app builds CLIP with plain GELU**, while its weights expect QuickGELU. A correct
  build scores about +0.015 higher on both sets. Fixing it changes every stored vector,
  including the aesthetic scorer's input.

Adoption is the owner's call; licence, storage and residency are open. The record is
docs/dev/roadmap/style-model-runs/README.md on branch `exp/style-models` (pushed, not
merged), with the pre-registration, the raw tables, the leakage checks and an Opus review
folded in. The candidate list below is the plan as first written.

## Second run (2026-11-03): no model is general

The owner asked for the same three models on **photographs, 3D art and digital paintings**,
and left the open choices to the run. Three pre-registered sets: five FSA photographers from
Commons in greyscale (`test6`), eight Danbooru digital painters (`test7`, all known to
Kaloscope, so its score there is an upper bound) and frames from ten Blender open films
(`test8`). Delano, Vachon and Wolcott dropped under the colour and border rules, and Hine, the
reserve, was not fetched.

- **No candidate is general.** Under the pre-registered rules all three are "neutral outside
  drawn art": none hurts on a medium, and none helps on two.
- **Kaloscope** helps only on the paintings it was trained on (+0.091), ties films and photos.
  Its verdict is **fragile**: without Shahn, whose 35 mm frames split by aspect, its photo call
  becomes "hurts".
- **CSD** is slightly positive on every medium (+0.006 to +0.021) and never clears the bar.
  No sensitivity run moves it.
- **PE-Core** helps on films at the bar (+0.031), a gain not separated from content matching.
- **The layer-7 baseline** beats the app on `main` on all three (+0.020, +0.080, +0.045).
- The photo set has five styles, and both Kaloscope and PE-Core flip without one photographer.

The record is docs/dev/roadmap/style-media-runs/README.md on branch `exp/style-media`
(pushed, not merged), with the pre-registration and its seven amendments, the raw tables and
an Opus review folded in. On this evidence the owner chose the layer-7 default and no new
model ([decision 6](../decisions/06-layer-7-default-and-no-new-style-model.md)).

## Candidates to check

None is verified yet. Check each one's licence, weights and size before testing it.
- **CSD (Contrastive Style Descriptors, 2024).** A style embedding trained on
  artist-labelled images, to measure style copying in diffusion models. It is the most direct
  fit, if its weights and licence allow use here.
- **Larger CLIP models**, such as OpenCLIP ViT-H/14 and ViT-bigG/14. CLIP's caption training
  is the one style signal that already helped (about +0.06 on the illustrators). bigG is
  already in the owner's Hugging Face cache.
- **SigLIP 2**: a newer image–text model, for the same reason as CLIP.
- **Gram-matrix statistics** of a CNN's feature maps: the classic Gatys style-transfer
  representation. It is a cheap, training-free baseline that measures texture rather than
  content.
- **DINOv3 patch tokens, pooled as a Gram matrix** rather than the CLS token. This tests
  whether DINOv3's dense features carry style that its summary vector drops.

## Protocol — what the DINOv3 runs taught

1. **Pre-register** fixed, named settings and a pass bar before downloading data. A best
   setting chosen on the same data is exploratory, never the verdict.
2. **At least five styles, about 30 images each, in one medium.** The two-style test
   overstated DINOv3 by about 0.07 AUC. Subjects crossed across styles are better still.
3. **Make the files identical.** Use one format, size and aspect, and check before scoring
   that no file property splits with the style label. Remove duplicate scans with a
   perceptual hash and by eye.
4. **Prove the harness** against the app's stored vectors (`--verify`, cosine ≥ 0.999).
   Cross-check one configuration against `style_gate.py`.
5. **Report the spread**: per-style AUCs, paired wins over reference draws, and the SD over
   draws. Gaps under 0.03 are ties.
6. **Get a second-opinion review** of the write-up against the raw tables before recording a
   verdict.

## Tooling and test sets

On branch `exp/dinov3-test`, not on `main`:
- the offline harness (`dino_embed_offline.py`, `style_gate.py`, `style_crossed_probe.py`);
- the sweep driver and the Wikimedia Commons test-set builders, in
  `backend/scripts/style_research/`;
- each run's pre-registration and tables, in `docs/dev/roadmap/dinov3-runs/`.

The seven-illustrator set is `test3` in the owner's dev DB. Its `MANIFEST.json` on that branch
lists each image's Commons page and licence, so it can be rebuilt elsewhere. A new candidate
model needs an extractor that writes the same `.npz` shape (`ids`, `filenames`, `cls`, and
optionally `layers`), after which the sweep compares it unchanged.
