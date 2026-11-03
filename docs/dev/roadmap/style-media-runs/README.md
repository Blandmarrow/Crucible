# Style models on photographs, paintings and 3D — 2026-11-03

**Status: transient.** The record of the M7 follow-up the owner asked for: do the anime and
illustration results of `../style-model-runs/README.md` hold outside drawn art? The same three
candidates against the same baseline, on three new sets. Pre-registered in
`PREREGISTRATION.md`, amendments 1–7 included. Quote numbers from `rules.txt` and
`file_props.txt`, not from memory. An Opus review checked a first draft against the raw files;
its points are folded in, and it found the missing sensitivity run below.

## Verdict

**No candidate is general. Under the pre-registered rules all three are "neutral outside
drawn art", and Kaloscope's verdict is fragile.**

| setting | test6 photos (5) | test7 paintings (8) | test8 3D films (10) | verdict |
|---|---|---|---|---|
| `0.30 CLIP + v2 L7` — baseline | 0.594 | 0.905 | 0.937 | |
| `0.38 CLIP + v2 final` — `main` | −0.020 (1/5) | −0.080 (1/8) | −0.045 (1/10) | |
| **K: Kaloscope** | −0.021 tie (1/5) | +0.091 helps (8/8), **upper bound** | +0.001 tie (7/10) | neutral, **fragile** |
| **C: CSD** | +0.013 tie (3/5) | +0.021 tie (4/8) | +0.006 tie (7/10) | neutral |
| **P: `0.30 PE + v2 L7`** | +0.023 tie (4/5) | −0.018 tie (3/8) | +0.031 helps (10/10) | neutral |

Baseline row: mean AUC. Other rows: Δ against it and styles won, over 20 seeded draws of 4
references per style. "Helps" is Δ ≥ +0.03 with more than half the styles won; "hurts" is
Δ ≤ −0.03.

**The sensitivity runs (build step 5).** Two test6 photographers split with a file property:
Shahn's files are mostly 35 mm frames (22 of 30 at an aspect of 1.4 or wider, against 2–8 for
each of the others), and Lange's were mostly fetched at half the others' resolution
(amendment 5). **Without Shahn, K's test6 call becomes "hurts" (−0.033, 1 of 4 won), and its
verdict becomes "not general".** Without Lange no call changes. Without `tenjin_hidetaka` on
test7, none changes. The first draft of this record skipped these runs; the review caught it.

**Both K and P are one photographer from flipping, in opposite directions** (an exploratory
leave-one-out, after the review): without Evans K also "hurts"; without Rothstein P's test6
call becomes "helps" (+0.033, 4 of 4) and P becomes "general". With five styles, the
between-style spread on test6 is larger than the bar: any test6 call inside ±0.03 is at the
noise level across styles.

### What the pattern suggests, and how far it goes

- **Kaloscope helps only where it has seen the artists or the domain.** It led by +0.167 on
  anime and +0.115 on illustrators, and here on paintings by artists it was trained on, which
  shows recognition, not general skill. test7 is Danbooru character art in a painted style,
  close to anime, so it hardly tests "outside drawn art". On films it ties on the mean but
  swings per film from −0.048 (Sintel, 3/20 draws) to about +0.036. On photographs it is both
  the worst setting on Evans (0.443, below chance) and the best on Shahn (0.784, 20/20): a
  strong, inconsistent signal, not an absent one.
- **CSD's mean Δ is positive on every medium, but small, and per style it loses in places**:
  Rothstein 1/20 draws, `vlfdus_0` 1/20, Sintel 2/20, Cosmos Laundromat 3/20, Mydans 5/20. It
  wins half or fewer of the styles on test6 and test7. It is the most stable of the three on
  the mean, and the only one no sensitivity run moves; that is not the same as never hurting.
- **PE-Core's test8 gain is at the bar (+0.0307) and is not separated from content
  matching**, which test8 rewards and which PE, a strong general image encoder, is good at.
  `pe alone` helps on test8 as well (+0.033, 10/10). Its test6 lead depends on Lange, the
  half-resolution photographer (without Lange, +0.017). A lean toward photographic images is
  possible, not shown.

### Hypotheses

- **H5 holds.** Kaloscope does not help on photographs (Δ −0.021).
- **H6 fails.** CSD ties on paintings (+0.021, 4 of 8 won).
- **H7 holds on all three**, as a non-inferiority bar (≥ −0.03): the baseline is +0.020,
  +0.080 and +0.045 against `main`. This bears on the open M7 item about the layer-7 default;
  it does not settle it. The baseline and `main` differ in blend weight (0.30 against 0.38) as
  well as layer, and this run has no `0.38 CLIP + v2 L7` row to tell them apart. The earlier
  run did: there, the layer was nearly all of the gain. On test6, `main` still wins 1 of 5.

## The sets

- **test6 (photographs)** runs with 5 of the 8 planned photographers. Delano and Wolcott shot
  mostly colour, Vachon's category includes his 1970s colour work, and the colour and measured
  border rules left each below 20 (amendment 6). Hine, the reserve, was not fetched
  (amendment 7). Scores are low (0.44–0.79 per style; means 0.57–0.62), but this is not
  uniform noise: paired wins are strong and consistent within a style, and the ties come from
  large opposing per-style effects. Format may carry some of it: Shahn worked in 35 mm and
  Evans often on large format, and K's two big test6 effects fall on exactly those two.
- **test7 (paintings)** is 8 Danbooru artists. Only two unseen painters (`john_tedrick` 31,
  `hua_bucheng` 25) cleared the bar: below the 5-style minimum, and as reserves they were
  never reached. So K's test7 score is an upper bound.
- **test8 (3D films)** rewards content as well as look, since characters and sets recur within
  a film. Cosmos Laundromat is the hardest film for every primary setting. Screening removed
  19 of its 45 frames, 14 as repeats of the same setup, so fewer within-film content
  matches remained there than in the other films.
- **flickr6 (exploratory)**: six modern colour photographers, 15 each. PE +0.057 (5/6), CSD
  +0.042 (5/6), K +0.002. It decides nothing: below the protocol's size, mostly upscaled, and
  it was part of the August data that chose the baseline's layer and weight.

## File properties (`file_props.txt`)

- **test6:** most files are 3840 px wide; 25 of Lange's 30 are 1920 (amendment 5, corrected).
  Shahn's aspect splits, as above. All are scaled to a 1024 centre square and greyscale.
- **test7:** 4 of `bm94199`'s 30 and 1 of `vlfdus_0`'s originals are PNG, the rest JPEG.
  Upload years differ by artist.
- **test8:** only the output is equalised: each frame is an 800 px centre square of the
  letterbox-cropped frame. Sources still differ in resolution (804–2160 px high) and codec
  (AVI, MP4, MKV), the risk the pre-registration named. Sources and sha256 are listed.

## The app's CLIP, again

Exploratory rows only. Building CLIP with the QuickGELU its weights expect changes the
baseline blend by +0.015 (test6), −0.007 (test7) and +0.012 (test8), in line with the drawn-art
sets on test6 and slightly negative on test7. The case for fixing it is correctness: the
aesthetic predictor was trained on correctly built vectors.

## What this run does not show

- **A robust test6 call for K or P.** Each flips without one photographer; the five-style set
  cannot carry a ±0.03 bar.
- **test6 free of format and resolution confounds** (Shahn's aspect, Lange's resolution), or
  with all eight planned photographers.
- **A fair Kaloscope test on paintings, or a painting set far from anime.** test7 is digital
  painting of characters, not traditional media.
- **PE's 3D gain separated from content matching.**
- **The layer separated from the blend weight** on these media.
- **A confidence interval over styles.** Spread is over reference draws only.
- **A resolution-matched comparison.** Kaloscope reads 512 px, CSD and CLIP 224, PE 336, which
  interacts with test6's grain and resolution cues.
- **3D as one artist's style.** A film is a production's look, made by many people.

## Files

- `PREREGISTRATION.md` (amendments 1–7, with amendment 5 corrected and amendment 7 restated
  after the review), `rules.txt` (every setting per style, paired wins, SD, the sensitivity
  runs, the exploratory leave-one-out, verdicts and hypotheses), `file_props.txt`,
  `extract_all.sh`
- Scripts, in `backend/scripts/style_research/`: `fetch_photos.py`, `fetch_paint.py`,
  `fetch_films.py`, `border_check.py`, `rules_media.py`, plus changes to `fetch_commons.py`
  (settable width, minimum size and batch, a 429 retry, re-resolving a link at a new width)
  and `finalize.py` (`--grey`).
- `exclude_test6.json`, `exclude_test7.json`, `exclude_test8.json` (every by-eye removal with
  its reason), `borders_test6.json` (the measured border shares), `MANIFEST_test6.json`,
  `MANIFEST_test7.json`, `MANIFEST_test8.json` (each final file's source page),
  `sources_test8.json` (film URLs and sha256) and `shots_test8.json` (cuts and crops per film),
  `gate_refs_test8.txt` and `gate_groups_test8.json` (the `style_gate.py` cross-check's inputs).
- Not kept: the images, the films, the contact sheets and the throwaway app DB, deleted after
  the run. Each set can be rebuilt from its manifest and the fetch scripts.

### Harness checks

- `--verify` against the app's stored vectors: CLIP 1.000000 on every set; DINOv2 mean
  0.999995–0.999997, minimum 0.999988.
- Self-check (one image twice): cosine 0.99991–1.00016 for Kaloscope, CSD, PE and QuickGELU
  CLIP on every set; norms 1 (QuickGELU 1.0001 on test8). float16 rounding.
- `style_gate.py` against the sweep's `--fixed-refs`, on test8 with 4 fixed `charge`
  references and every other film as controls: CLIP 0.9267, Kaloscope 0.8940 and
  `0.38 CLIP + kalo` 0.9269 in both. None is saturated. The sweep side was run by hand and is
  in no committed file.
