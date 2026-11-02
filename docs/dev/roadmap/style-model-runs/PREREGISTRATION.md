# Style models on anime (test4) and illustrators (test3) — pre-registration

Written 2026-11-03, after the desk research (`DESK_RESEARCH.md`) and an artist-level metadata
count, and **before any test4 image was downloaded, viewed or embedded**. The candidate list
and the test-set design were chosen by the owner.

## Data

**test4, primary.** Danbooru artists that are **not** among Kaloscope 3.0's 44,129 training
classes (`class_mapping.csv` at revision `5e4bfa2`), so no candidate has learned any test
artist as a label. Of the 10,000 artist tags with the most posts, 179 are missing from that
list. An alias and other-name check removed `ichizen_ryuu` (Kaloscope knows it as
`ryuuforkaf`). The eight with at least 25 eligible posts, in order of eligible count:
`tetenka`, `edomura_niniko`, `suzuneiroekaki`, `jinri_shijie`, `premium_(prmsub)`, `pappi`,
`nashi_(shi_ra_nu_i)`, `apricotlemontea`. Nothing about their images was seen when choosing.

Eligibility is the fixed filter in `backend/scripts/style_research/fetch_booru.py`: rating
general, `1girl` and `solo` (one subject class for every artist), none of the comic,
monochrome, sketch, traditional, 3D, pixel-art, animated, multi-view, chibi or `ai-generated`
tags, no parent or children, short side ≥ 1024 px.

Build steps, all before embedding:
1. `fetch` 45 per artist, an even spread over post ids.
2. `screen.py` contact sheets. By eye, remove only: not one character, comic or text-heavy
   pages, edits or near-copies, and images that look AI-generated (`edomura_niniko` and
   `pappi` uploaded 50–166 posts in two months, so check them hardest). Record every removal
   with a reason in `exclude.json`.
3. `finalize.py`: pHash dedupe (distance ≤ 8), an even spread capped at **30**, every file
   1024×1024 centre-crop JPEG q90.
4. An artist left with fewer than **20** is dropped. The run needs at least **5** artists, or
   it stops and is re-planned.
5. Check that no file property splits with the artist: original format, original short side,
   upload year. Record it in `file_props.txt`. Upload year cannot be balanced (it is part of
   who the artist is). It is reported, not corrected.

Images are not committed. `MANIFEST.json` keeps each post's id and page URL.

**test3, secondary.** The existing seven public-domain illustrators (200 images), unchanged.

## Candidates and fixed settings

Each extractor writes the sweep's `.npz` shape with L2-normalised vectors in `cls`.

| Tag | Model | Setting |
|---|---|---|
| `kalo` | Kaloscope 3.0 preview, `v1-artist-classifier/model.safetensors` @ `5e4bfa2` | 256-d projector output, CLS + mean pooled, 512 px short side + centre 512, ImageNet mean/std (its `config.json`) |
| `csd` | CSD ViT-L, `tomg-group-umd/CSD-ViT-L` | 768-d style head, 224 px, CLIP preprocessing |
| `pe` | PE-Core L14-336, `facebook/PE-Core-L14-336` | final image embedding, 336 px, its own preprocessing |

**Primary settings**, one per model, scored against the baseline `0.30 CLIP + v2 L7`:
- **K: `kalo final`**, alone.
- **C: `csd final`**, alone.
- **P: `0.30 PE + v2 L7`**, PE in CLIP's role. This needs a second sweep run with
  `--clip pe.npz`, saved as its own CSV.

Also reported, not used for a verdict: `0.38 CLIP + v2 final` (the app on `main`), CLIP alone,
`pe final` alone, and `0.30 CLIP + kalo final` / `0.30 CLIP + csd final`.

Sweep: `dinov3_sweep.py`, 4 references × 20 seeded draws (seed 0) per style; positives are
the rest of that style, controls every other style.

## Rules

A primary setting **passes** if all three hold:
1. **test4:** mean AUC ≥ baseline + **0.03**, and a higher per-style mean than the baseline on
   **more than half** the styles.
2. **test3:** mean AUC ≥ baseline − **0.03** (no real loss on a second medium).
3. Its harness checks hold (below).

If (1) passes and (2) fails, the verdict is **"anime only"**: a candidate for an optional
mode, not a new default. A difference under 0.03 is a tie. Report per-style AUC, paired wins
over the 20 draws and the SD over draws, from `rules.py`.

## Hypotheses (each reported, none decides adoption)

- **H1.** On test4, K beats C by ≥ 0.03: anime-specific training beats general style
  training on anime.
- **H2.** On test3, C beats CLIP alone by ≥ 0.03, as the CSD paper's WikiArt result predicts.
- **H3.** `pe final` beats CLIP alone by ≥ 0.03 on both sets.
- **H4.** On test4, blending 0.30 CLIP into K or C lowers its mean AUC: a style model needs
  no caption signal.

## Harness checks

- `--verify` for CLIP and DINOv2 on test4: cosine ≥ 0.999 against the stored vectors.
- `kalo`: reproduce the values in Kaloscope's `inference_verification.json`, if it states
  any for a known input, within its own tolerance.
- Every new model: the same image extracted twice gives cosine 1.000, and every vector has
  norm 1.
- One configuration cross-checked between the sweep and `style_gate.py`.
- Pickled weights only through `torch.load(weights_only=True)`; safetensors preferred.

## Amendments before any test4 embedding (2026-11-03)

Made after the contact sheets and the file-property check, before any test4 image was
embedded by any model. None changes a rule's pass bar or a model setting. Two change
something else, and are marked so: amendment 2 changes a screening setting, and amendment 4
replaces a harness check with one that had no pass bar set in advance.

One choice before this file was written also belongs here. The "at least 25 eligible posts"
artist cutoff was set after the metadata count, which had used 30. At 25 it admits
`nashi_(shi_ra_nu_i)` (29) and `apricotlemontea` (28). No image had been seen.

1. **Seven artists, not eight.** `jinri_shijie` is dropped: nearly every image is a
   mecha-series poster with large title typography, which the build steps remove as
   text-heavy, leaving 1. test4 is 204 images: 27–30 for each of `apricotlemontea`,
   `edomura_niniko`, `nashi_(shi_ra_nu_i)`, `pappi`, `premium_(prmsub)`, `suzuneiroekaki`
   and `tetenka`. Every removal and its reason is in `exclude.json`.
2. **`screen.py` ran with `--min-colour 0`** (a screening setting). The fetcher already removes monochrome by tag,
   and a colour bar would drop muted palettes, which are part of some artists' styles.
3. **`edomura_niniko` splits with file properties** (`file_props.txt`): all 30 originals are
   PNG, many with transparency (black after RGB conversion, as in the app's `open_rgb`), at a
   median short side of 4600 px against about 1450 for every other artist. It also draws the
   same few characters again and again. So the verdicts are also computed **without
   `edomura_niniko`** (six styles; rule 1's "more than half" becomes 4 of 6). A verdict that
   flips there is reported as fragile; the rules above still decide it.
4. **Kaloscope's harness check** (a replaced check, with no pass bar set in advance).
   `inference_verification.json` records only the export's
   parity with its own code, with no known input to reproduce. Instead, the classifier head
   was run on 9 recent posts by 5 artists it was trained on (`--kalo-check`). Chance is
   1/44,129. The right artist came first on 4 of 9, at 0.66–0.90 for three of them, and was
   in the top 5 on 6 of 9. A wrong key mapping would give flat, unrelated output. The RoPE
   periods it recomputes round exactly to the export's bf16-stored ones.
5. **CSD's checkpoint is a full pickle** (training args included), not a bare state dict. Its
   globals were listed with `pickletools` without unpickling: `OrderedDict`, tensor rebuild,
   `FloatStorage`, numpy `scalar` and `dtype`, `_codecs.encode`, `argparse.Namespace`. Exactly
   those are allowlisted under `weights_only=True`.
