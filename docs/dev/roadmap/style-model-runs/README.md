# Style models on anime and illustrators — 2026-11-03

**Status: transient.** The record of the M7 style-model research run: three candidates
against the shipped style signal, on a new anime set and the existing illustrator set.
Pre-registered in `PREREGISTRATION.md` (amendments included), with the candidate facts in
`DESK_RESEARCH.md`. Quote numbers from `rules.txt` and the other raw files here, not from
memory. An Opus review checked this write-up against the raw files and reproduced
`rules.txt`. Its points are folded in below.

## Verdict

**Kaloscope 3.0 passes, by a wide margin. CSD passes, by a modest one. PE-Core is a tie and
fails.** No model was adopted. That is the owner's decision, and an adoption plan is the next
step (§ What adoption would need). The run also found that **the app builds CLIP with the
wrong activation** (§ A finding about the app's CLIP).

| setting | test4 (anime) | Δ | styles won | test3 (illustrators) | Δ | verdict |
|---|---|---|---|---|---|---|
| `0.30 CLIP + v2 L7` — baseline (the M7 branch) | 0.832 | | | 0.791 | | |
| `0.38 CLIP + v2 final` — the app on `main` | 0.741 | −0.091 | 1/7 | 0.706 | −0.085 | |
| **K: Kaloscope, alone** | **0.999** | **+0.167** | 7/7 | **0.906** | **+0.115** | **pass** |
| **C: CSD, alone** | 0.888 | +0.056 | 7/7 | 0.842 | +0.051 | **pass** |
| P: `0.30 PE + v2 L7` | 0.850 | +0.018 | 5/7 | 0.820 | +0.029 | fail (tie) |

Mean AUC over styles, 20 seeded draws of 4 references per style; every setting shares each
draw's references. Rule 1 (test4): Δ ≥ +0.03 and more than half the styles won. Rule 2
(test3): Δ ≥ −0.03.

- **Kaloscope won all 20 paired draws on every test4 style**, with an SD over draws of at
  most 0.003. On test3 it won 20/20 on six styles and 13/20 on Clarke.
- **CSD's gain is modest and uneven.** On test4 it is +0.156 on `tetenka` and +0.101 on
  `premium_(prmsub)`, but only +0.013 to +0.055 on the other five. Without `tetenka` it would
  still pass (+0.040). Without both it would not (about +0.027). On test3 it gains on Bauer,
  Bilibin, Dulac, Goble and Rackham, and loses on Clarke (0/20 draws) and Nielsen (7/20).
- **Without `edomura_niniko`** (the sensitivity run the file-property check called for), K is
  +0.195 (6/6) and C +0.059 (5/6). Neither verdict is fragile.
- **The app on `main` is the worst setting here.** The DINOv2 layer-7 change is the gain:
  `0.38 CLIP + v2 L7` scores 0.830 on test4 and 0.789 on test3, so the blend weight adds only
  about 0.002. This confirms the M7 port on a third set.

### Hypotheses

- **H1 holds.** On test4, Kaloscope beats CSD by +0.111.
- **H2 holds**, against either CLIP build. On test3, CSD beats the app's CLIP by +0.065 and
  a correctly built CLIP (§ below) by +0.049. CSD shares CLIP's backbone but is built with
  the right activation, so part of the first figure is the activation, not the style head.
- **H3 fails.** PE alone beats the app's CLIP by +0.024 on test4 and +0.033 on test3, and the
  correctly built CLIP by +0.008 and +0.017. The bar was +0.03 on both.
- **H4 fails.** A 0.30 CLIP blend changes Kaloscope by +0.0001 and CSD by +0.0015: it neither
  helps nor hurts a style model. The prediction was a loss.

## Not label leakage — and what that does not prove

Kaloscope was trained on 6.57M Danbooru images, and test4 is Danbooru. These checks rule out
**label** leakage: that it learned any test4 artist under some name.

1. **The design.** Every test4 artist is absent from its 44,129 training classes, checked
   against tag aliases and other names, which removed one candidate.
2. **Its own classifier cannot name them** (`kalo_check.txt`). On 3 images per test4 artist,
   the top artist is never above 0.08, and is a different stranger each time. This mostly
   restates check 1. What it adds is that no single known class absorbs a test4 artist, as a
   circle name or an untagged alias would.
3. **Positives it cannot have trained on** (`post_cutoff_probe.txt`, exploratory, small).
   References come from before its 2026-10-02 checkpoint, and positives only from after it.
   Kaloscope scores 0.987 on `apricotlemontea` (16 positives) and 1.000 on
   `nashi_(shi_ra_nu_i)` (5) and `suzuneiroekaki` (3). The baseline scores 0.669, 0.976
   and 0.912. Only three artists have enough post-cutoff uploads.
4. **test3 is outside its classes.** None of the seven test3 illustrators is among its
   44,129 artists, and it still leads there by +0.115.

Near-copies are not the driver. The review found Kaloscope's highest within-artist cosine is
0.81. **What these checks do not rule out** is that the margin comes from cues other than
drawing style, which test4 does not control:
- **Recurring content.** `edomura_niniko` draws the same 3–4 characters, mostly as standing
  art on flat backgrounds. `suzuneiroekaki` and `pappi` both draw mostly one franchise
  (Umamusume), which works *against* separating them.
- **Templates and text.** `tetenka` reuses one layout: grey panels, date and number labels,
  and a handle watermark.
- **Resolution.** Kaloscope reads 512 px. CLIP, DINOv2 and CSD read 224, and PE 336, so
  Kaloscope can see linework, text and signatures the others cannot.
- **Its training sweet spot.** Kaloscope learned same-artist pairs within 365 days of upload,
  and test4 is mostly 2026 uploads.

## A finding about the app's CLIP

`backend/ml/model_manager.py` builds `open_clip` `ViT-L-14` with `pretrained="openai"`.
open_clip builds that with plain GELU and warns that the weights were trained with QuickGELU.
`backend/main.py` silences the warning with a comment that it has "no effect on inference
output". **It does have an effect.** The same checkpoint built as `ViT-L-14-quickgelu` gives
vectors with a mean cosine of only 0.928 (min 0.845) to the app's on test4, and 0.917
(min 0.836) on test3 (`clip_quickgelu.txt`). The correctly built CLIP scores higher
(exploratory rows in `rules.txt`):

| | test4 | test3 |
|---|---|---|
| CLIP alone: the app's / QuickGELU | 0.821 / 0.836 | 0.777 / 0.792 |
| `0.30 CLIP + v2 L7`: the app's / QuickGELU | 0.832 / 0.846 | 0.791 / 0.807 |

No verdict changes: P still ties a QuickGELU baseline (0.850 against 0.846). But every
stored `clip_embedding` was made this way. That includes the input to the LAION aesthetic
predictor, which was trained on correctly built CLIP vectors. A fix changes every stored
vector, so old and new ones must not be mixed. It needs its own plan.

## What this run does not show

- **That the margin is style rather than artist identity.** See the cues listed above. Both
  sets also label a style by who drew it, which is exactly what Kaloscope was trained to tell
  apart. A reference set that is a style spanning artists, such as "watercolour" or "1990s
  cel anime", is untested.
- **A resolution-matched comparison.** Neither Kaloscope at 224 px nor the others at 512 px
  was run.
- **Anything beyond 7 styles per set.** There is no confidence interval over styles, only
  the spread over draws.
- **A ranking above Kaloscope.** At 0.999, test4 is saturated. A harder set (artists who draw
  alike, or one artist across years) would be needed.
- **A representative artist sample.** test4's artists were chosen because Kaloscope lacks
  them, which mostly means artists new in 2026. AI use was screened by eye only.
- **Backgrounds as Kaloscope expects them.** Transparent PNGs become black in the test files
  (the app's `open_rgb`), while Kaloscope's own loader composites them on white. This
  affects `edomura_niniko` most.
- **CPU cost.** Kaloscope runs a ViT-B on 1,029 tokens at 512 px, against DINOv2-base's 257
  at 224: 4.0× the tokens and about 16× the attention. On the RTX 5090, a 204-image
  extraction took 14 s with the model load (`extract_times.txt`).
- **PE beyond one setting.** PE was tested in CLIP's role at one blend weight, from the
  open_clip port `timm/PE-Core-L-14-336` (Apache-2.0), not Meta's own
  `facebook/PE-Core-L14-336` that the pre-registration names.

## What adoption would need

Each is a design question for the owner, not settled here:
- **Licence.** Kaloscope's weights are under the Meta DINOv3 License (no commercial clause,
  military and ITAR uses barred, redistribution on the same terms). Its code is GPL-3.0. The
  loader here (`style_embed_offline.py`) is written against transformers and copies none of
  it. CSD is MIT code with CC-BY-4.0 weights, the permissive fallback.
- **Storage.** A 256-d vector (CSD: 768-d) needs its own column and a model discriminator:
  `Image.dino_embedding` holds one model's output with no record of which.
- **Model residency.** One more ~625 MB model under `model_manager`.
- **The loader's exactness.** Its check (amendment 4) was not set in advance with a pass bar,
  and 4 of 9 right first is far below the card's 95.2% dev top-1. The posts were recent ones,
  where an artist's style may have drifted. A partly wrong mapping could only lower
  Kaloscope's scores, so this does not threaten the pass. It does mean the loader is shown to
  work, not shown to be exact.

## Files

- `PREREGISTRATION.md`, `DESK_RESEARCH.md`
- `rules.txt`: every setting per style, the paired wins, the verdicts and hypotheses
  (`backend/scripts/style_research/rules.py`)
- `post_cutoff_probe.txt` (`post_cutoff_probe.py`), `kalo_check.txt`, `clip_quickgelu.txt`,
  `extract_times.txt`
- `groups_test4.json`, `MANIFEST_test4.json` (post ids and pages), `exclude_test4.json`,
  `file_props_test4.txt`
- `extract_all.sh`: the extraction order. The extractor is
  `backend/scripts/style_research/style_embed_offline.py` and the fetcher `fetch_booru.py`.

### Harness checks

- `--verify` against the app's stored vectors: CLIP 1.000000 and DINOv2 0.999997 on test4;
  1.000000 and 0.999996 on test3.
- Self-check (one image twice, in one batch): cosine 0.99993–1.00008, every norm 1.
- `style_gate.py` against the sweep, with 4 fixed `tetenka` references: CLIP 0.6898 in both
  and Kaloscope 1.0 in both. The Kaloscope figure is saturated, so only the CLIP one
  discriminates.
- The test3 baseline reproduces the DINOv3 run's: 0.7909.
