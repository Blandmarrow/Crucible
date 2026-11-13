# Style similarity — how well the modes measure up

This file holds the measurement and research half of the style-similarity feature: what each `embedding_type` mode is measurably worth, which DINOv2 layer is the default and why that default moved twice, the candidate models that were measured and not adopted (DINOv3, Kaloscope, CSD and PE-Core), and the protocol for testing the next candidate. The flow, the endpoints, the constants and the blend are `docs/dev/quality/style-similarity.md`; making a stored score readable is `docs/dev/quality/style-match.md`. Part of the Quality family; the hub, `docs/dev/quality.md`, routes from a code path to the file that owns it.

## What the modes are actually worth

Two measurement campaigns, and the second revised the first. `backend/scripts/style_gate.py`
is a read-only offline harness that calls these same production functions; its report
(`backend/scripts/style_gate_report.md`) is the 2026-08-02 run, against 98 frames of one
animated film scored from eight references of one lighting cluster with 20 out-of-style
controls mixed in. Its conclusion stands and is the reason no learned head is planned for
style: **the centroid baseline separates well enough** (AUC 0.94–0.97, no control in any
mode's top 20).

What did not stand is anything read off that *single reference configuration*. A 2026-08-04
sweep of twelve configurations across illustration, animation and photography rewrote three
of its recommendations, and the shape of the correction is worth more than any of the
numbers:

- **The last layer is the weakest layer.** DINOv2's post-layernorm `dino_embedding` — what
  the app scored on before this port — averaged **0.7166** mean AUC over six configurations
  and fell to **0.4066**, worse than a coin flip, on one of them. Reading layer 9 instead
  takes the same model to 0.8645, and `combined` at 0.30/0.70 on layer 9 to **0.9244** from
  the old blend's 0.8246. Layer 12 being worst replicated on every model and every dataset
  tested, including photographs, and is the most robust result of the campaign. The robust
  unit is the *band*, roughly layers 5–11, not any single index — which is why the default
  moved again, to 7, without any of these numbers changing. See § Which layer, and why it
  moved twice.
- **No single AUC is a property of a mode.** CLIP scored 0.9733 on the gate's reference
  cluster and **0.6144** on an opposing cluster drawn from *the same 118 images*, and 0.7399
  on photographs. That is why `styleModes.ts` no longer quotes a figure in any mode
  description — the rule is stated in that file's module doc, because the copy had already
  been taken back twice.
- **"Layers 1–8 cannot be thresholded" was the wrong conclusion from a real observation.**
  The compression is real (every image lands in 0.90–0.99 on DINOv2's low layers) but it
  does not follow that the band is unusable: a narrow range with clean ordering thresholds
  fine, and a mid-layer configuration with a range of 0.129 reached 0.9909 best-threshold
  accuracy. Range and separability are separate properties that coincided on one dataset.
- **`combined` is worth more than either half, and is not just DINOv2 with a tilt.** The
  gate's ρ 0.9844 against `dino` was measured at the old weights on the final embedding.
  CLIP and the DINO family remain the only pair that genuinely disagree (ρ 0.7525), and at a
  mid layer the blend beats both components alone.

**What to promise.** ~0.98 AUC is the ceiling for sorting *across* media — anime screencaps
from painterly illustration — and **~0.85 is the realistic figure for style matching inside
one medium**. The photograph test (six photographers, style label = the photographer) was
pre-registered at ~0.93 and failed at 0.8220 for the shipped configuration. Every signal
dropped there, including the old one, so it is a property of the task rather than of any
model: telling six photographers apart is a far finer discrimination than telling two media
apart. UI copy promising more is promising the easy case.

The full method, the leave-one-out validation and the open stage (dense features) are in `docs/dev/style-embedding-plan/README.md` while that folder exists.

## Which layer, and why it moved twice

Both moves were measured on 2026-08-04: the final embedding → layer 9 on the sweep above,
then layer 9 → **layer 7** on a second pair of runs that asked a different question.

Every sweep before the second pair scored *separability* — can the signal tell in-style
candidates from out-of-style controls, with subject and framing free to vary however they
happen to. A layer can win that on content alone, because references and positives usually
share subject matter as well as a look. Two **crossed** runs hold one of the two fixed.
Both are `backend/scripts/style_crossed_probe.py`, the offline companion to
`style_gate.py`, and both read the DB read-only:

- **Synthetically crossed**: 30 base pictures × 7 restyles (grayscale, warm grade, contrast
  punch, and the three that change the rendering itself — posterised, painterly smear,
  pencil sketch) × 3 framings, asking whether a layer ranks *different picture, same
  rendering* above *same picture, restyled*.
- **Real pools**: three drawn styles that share one subject domain — two anime films and an
  SDXL set, all gothic vampire imagery — plus photographs, with subject then held fixed by
  CLIP zero-shot subject bins.

Both put the crossover at about layer 7. On the real pools, layer 9 ranks same-subject pairs
above same-style pairs (0.737 against 0.682), layers 5–6 do the reverse (0.674 against 0.707
at layer 5), and layer 7 sits near the balance point; layer 12 is furthest into subject on
both runs. That is what the picker copy means by suggesting 5–6 for a look and 9–10 for more
of the same scene.

Two things this does not claim. **The move is close to free, not a gain**: on the gate's own
published configuration layer 7 scores 0.9442 against layer 9's 0.9428, and on the
photographer test the blend gives up 0.011 — on a test that itself rewards subject matching,
since a photographer repeats subjects. And **with subject genuinely held fixed, every
configuration measured lands at 0.65–0.66 AUC**, so the layer choice is arguing over a small
part of a weak signal. The design that would separate style from subject by construction
rather than by binning is specified in the plan folder.

The later model runs (below) re-tested the layer-7 blend on five more sets — seven
illustrators, an anime set, photographs, paintings and 3D films — and it beat the old
default on every one. That is what [decision 6](../decisions/06-layer-7-default-and-no-new-style-model.md)
rests on.

## Other models were measured and not adopted

### DINOv3

Tested offline on 2026-11-02, in two pre-registered runs, with no app code changed. The full
write-up and raw tables are on branch `exp/style-media`, in docs/dev/roadmap/style-embedding.md
and the `dinov3-runs/` folder beside it.
- **Two anime styles.** DINOv3 (ViT-B/16 and ViT-L/16, 224 and 512 px) beat DINOv2 at the
  same relative depth. But part of that lead was a file confound: one style was all JPEG,
  the other all PNG.
- **Seven public-domain illustrators, as identical 1024 px JPEGs.** All four DINOv3 settings
  **tied** DINOv2 layer 7 (−0.003 to −0.010 mean AUC). Size and resolution made no difference.

**Why a newer model does not help here.** DINO models are trained to give the same answer for
a picture whatever its colour shift, blur or crop, so a stronger one ignores palette and
texture *more*. Those are much of what "style" means. DINOv3's headline gain is in its
per-patch features, which this flow never reads.

Two findings carry over regardless of model:
- **Reading DINOv2 at a middle layer (7) instead of its final embedding beat the old
  `combined` default** on both sets, by +0.085 to +0.223 AUC.
- **CLIP's value is set-dependent.** On the illustrators, a 30% CLIP blend beat every DINO
  layer alone by about 0.06. On the anime pair it did not.

### Kaloscope, CSD and PE-Core

Neither CLIP nor DINO was trained to see style, so on 2026-11-03 three models that might were
tested against `0.30 CLIP + v2 L7`, each in a pre-registered run: Kaloscope 3.0 (a DINOv3-B
fine-tune for Danbooru artist retrieval), CSD ViT-L (Contrastive Style Descriptors) and
PE-Core L14-336. IntroStyle and Gram matrices were dropped at the desk stage.

- **On drawn art they helped.** On a new set of seven Danbooru artists Kaloscope never trained
  on (`test4`, 204 images — a new set reusing the name of August's 24-image one), Kaloscope scored 0.999 against 0.832, and 0.906 against 0.791 on
  the illustrators (`test3`). CSD scored 0.888 and 0.842. PE-Core tied.
- **No candidate is general.** On photographs (five FSA photographers, greyscale), digital
  paintings (eight Danbooru painters, all known to Kaloscope) and 3D films (ten Blender open
  films), all three were "neutral outside drawn art" under the pre-registered rules: none hurt
  on a medium, and none helped on two. Kaloscope helped only on the paintings it was trained
  on, and its photo verdict flips to "hurts" without one photographer. CSD was slightly
  positive everywhere (+0.006 to +0.021) and never cleared the bar.

On this evidence the owner chose the layer-7 default and no new model
([decision 6](../decisions/06-layer-7-default-and-no-new-style-model.md)). Reopening it needs
a superseding entry, then an adoption plan: licence, its own embedding column and
discriminator, residency. The records are on branch `exp/style-media` (pushed, never merged),
in docs/dev/roadmap/style-model-runs/README.md and docs/dev/roadmap/style-media-runs/README.md,
with the pre-registrations, raw tables and an Opus review each.

A side finding of these runs was that the app built CLIP with plain GELU; see the next
section.

### The CLIP QuickGELU fix

Until 2026-11-13 the app built OpenAI's CLIP ViT-L/14 with plain GELU, while its weights were
trained with QuickGELU. The cause was open_clip's `"ViT-L-14"` config, and the warning about
it was muted with a filter that did not even match. In the runs above, a correct build scored
about +0.015 AUC higher on both drawn-art sets.

`backend/scripts/clip_build_delta.py` then scored all 1,033 images in the dev library
(`test2`, `test3`, `Set1`) under both builds, through the app's own `infer_*` functions:

| Old build vs fixed build | All | `test3` (illustrators, 200) | `test2` (804) |
|---|---|---|---|
| CLIP vector cosine, mean / min | 0.944 / 0.837 | 0.917 / 0.837 | 0.950 / 0.863 |
| LAION score, mean / max \|Δ\| | 0.17 / 0.96 | 0.28 / 0.96 | 0.15 / 0.73 |
| LAION Spearman ρ | 0.82 | 0.82 | 0.85 |
| LAION mean shift (new − old) | +0.08 | +0.27 | +0.03 |
| Watermark score, mean / max \|Δ\| | 0.10 / 0.61 | 0.11 / 0.52 | 0.10 / 0.61 |
| `has_watermark` flips at 0.6 | 83 (+57 / −26) | 19 | 63 |

The change is far from rounding noise, so the fix clears the old vectors and watermark results
and re-labels the old LAION scores `laion_gelu`
([decision 17](../decisions/17-clip-quickgelu-fix.md)). Every CLIP build, scripts included, now
goes through `aesthetic_scorer.build_clip_vit_l14` (`docs/dev/ml-models.md` § One CLIP
builder).

Style results measured before the fix (the tables above and `style_gate.py`'s report) used the
old build's vectors. A re-run needs the vectors re-embedded first. The `--verify` step of
`dino_embed_offline.py` reads a little under 0.999 against vectors stored before the fix.

### How to test the next candidate

What the runs taught, so the next one is not re-learned:

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

The harness on `main` is `dino_embed_offline.py`, `style_gate.py` and
`style_crossed_probe.py` in `backend/scripts/`. The sweep driver and the Wikimedia Commons
test-set builders (`backend/scripts/style_research/`) stay on `exp/style-media`, whose
`MANIFEST.json` lists each `test3` image's Commons page and licence so the set can be rebuilt.
A new candidate needs an extractor that writes the same `.npz` shape (`ids`, `filenames`,
`cls`, and optionally `layers`), after which the sweep compares it unchanged.
