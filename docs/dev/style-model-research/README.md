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
