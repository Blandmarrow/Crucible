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

## Next: other media (owner's request, 2026-11-03)

Both sets so far are drawn illustration, and a style there means one artist. The owner asked
for the same three models to be tested on **photographs, 3D art and digital paintings**. A
metadata-only feasibility pass ran on 2026-11-03, with no images downloaded. Three choices
are open for the owner before any set is built:

- **Photographs: feasible, from Wikimedia Commons.** The public-domain FSA photographers shot
  similar Depression-era subjects on similar black-and-white film, so the photographer's eye
  is what differs. Files per photographer, crawled two subcategories deep with
  `fetch_commons.py`: Delano 2,155, Lange 1,908, Wolcott 589, Rothstein 436, Vachon 407,
  Shahn 135, Evans 107, Mydans 97, and Hine 9,305 (documentary, not FSA). The category
  "Photographs by Russell Lee" holds 1 file, so Lee needs another root. Commons counts only
  through subcategories, and loc.gov answers automated requests with 403. **Open:** these are
  all 1930s monochrome. Modern colour photography needs an API source, such as Flickr with a
  key from the owner.
- **Digital painting: feasible only with a caveat, from Danbooru.** The test4 rules are an
  artist unseen by Kaloscope, `1girl` + `solo`, rating general and 1024 px. Under them, no
  `painterly` or `realistic` artist has more than 12 usable posts. Keeping `solo` but
  dropping `1girl`, eight artists Kaloscope **was** trained on have 27–94: `yuming_li`,
  `chan8016`, `nyte_tyde`, `fuyumurasaki`, `kuto_(kuroto)`, `koriarredondo`, `usagimarumei`
  and `maomi_jita`. From `realistic`: `tenjin_hidetaka`, `vlfdus_0`, `bm94199`, `hatsburg`
  and `pluvium_grandis`. (`alex_ross` paints traditionally.) Only `john_tedrick` (31) and
  `hua_bucheng` (25) are unseen. **Open:** whether to build it with seen artists. Kaloscope's
  score would then be an upper bound only, while CSD, PE-Core and the baseline still get a
  fair test.
- **3D: not feasible from Danbooru.** One artist (`serebii_(artist)`, 52, known to
  Kaloscope) has 25 usable posts under any rule. The best unseen one has 14. **Open:**
  - **Blender Studio's CC-BY open movies**: Sintel, Spring, Sprite Fright, Charge, Coffee
    Run and others. Frames would be cut with the app's own video extraction. Here a style
    means a production's look; the downloads are a few GB; characters recur within a film.
  - Or skip 3D.

Each set then follows the protocol below. The harness on `exp/style-models` takes any
`raw/<name>/` folder: `screen.py`, `finalize.py`, `style_embed_offline.py` and `rules.py`.
`fetch_booru.py` already takes an artist list; photos and films need a fetcher of their own.

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
