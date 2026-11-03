# Style models on photographs, paintings and 3D (test6–test8) — pre-registration

Written 2026-11-03, after the metadata-only feasibility pass (counts of files and posts per
artist, film availability) and **before any test6, test7 or test8 image was downloaded, viewed
or embedded**. The owner asked for these three media and left the open choices to this run:
1930s photographs (no API key for a colour source), paintings by artists Kaloscope knows, and
Blender's open films for 3D.

The models and settings are the ones in `../style-model-runs/PREREGISTRATION.md`, unchanged.
This run asks one new question: **do the anime and illustration results hold outside drawn
art?**

## Data

Every set follows the same build, all before embedding:
1. Fetch 45 per style, an even spread over the source's order.
2. `screen.py --min-colour 0` contact sheets. Remove by eye only what the set's list below
   names, and record every removal with a reason in `exclude.json`.
3. `finalize.py`: pHash dedupe (distance ≤ 8), an even spread capped at **30**, every file a
   1024×1024 centre-crop JPEG q90.
4. A style left with fewer than **20** is dropped. A set needs at least **5** styles, or it
   stops and is re-planned.
5. Check that no file property splits with the style, and record it in `file_props_<set>.txt`.
   For test6 the main risk is negative size (Shahn worked in 35 mm, Evans often on large
   format), which changes grain and aspect; for test8 it is the source codec and resolution.
   A style that splits markedly gets a sensitivity run without it, as test4's amendment 3 did.

Images are not committed. Each set's `MANIFEST.json` keeps every file's source page or URL.

### test6 — photographs (Wikimedia Commons, public domain)

The FSA photographers: the same years, the same black-and-white film and similar subjects, so
the photographer's eye is what differs. Roots are `Category:Photographs by <name>`, crawled two
subcategories deep. In order: Delano, Lange, Wolcott, Rothstein, Vachon, Shahn, Evans, Mydans;
reserve Hine. The first 8 that keep at least 20 are used. Downloads are 2048 px wide copies of
originals whose short side is at least 1024.

Remove by eye: anything not a photograph (documents, caption cards, maps, copies of art),
colour photographs (the FSA also shot Kodachrome), portraits of the photographer, frames where
film border, sleeve or caption fills more than about a tenth of the centre square, and a second
frame of the same scene and moment (keep the lower index). **Every test6 file is converted to
greyscale** in `finalize.py`, so print toning cannot stand in for the photographer.

### test7 — digital paintings (Danbooru)

Artists from the feasibility pass's `painterly` and `realistic` lists, queried as
`<artist> <that tag>`. Eligibility is `fetch_booru.py`'s filter with `solo` alone required (not
`1girl`): rating general, none of its excluded tags, no parent or children, short side ≥ 1024.
In order of eligible count: `tenjin_hidetaka` (realistic), `yuming_li`, `chan8016`,
`nyte_tyde` (painterly), `vlfdus_0` (realistic), `fuyumurasaki` (painterly), `bm94199`,
`hatsburg` (realistic); reserves in order `kuto_(kuroto)`, `koriarredondo`, `john_tedrick`,
`usagimarumei`, `maomi_jita`, `pluvium_grandis`, `hua_bucheng`. The first 8 with at least 25
eligible posts are used.

**Kaloscope was trained on every artist above except `john_tedrick` and `hua_bucheng`.** Its
test7 score is an upper bound, and it is labelled so wherever it appears.

Remove by eye: as test4 (not one character, comic or text-heavy pages, edits or near-copies,
AI-looking images), plus anything that is not painted (flat cel shading under a painterly tag).

### test8 — 3D animation (Blender open films, CC-BY)

A style here is a production's look, not one person's. In order: Elephants Dream (2006), Big
Buck Bunny (2008), Sintel (2010), Cosmos Laundromat (2015), Glass Half (2015), Caminandes 3
(2016), Agent 327: Operation Barbershop (2017), Spring (2019), Sprite Fright (2021), Charge
(2022), Wing It! (2023). **Every film that keeps at least 20 is used.** Source: the
highest-resolution file up to 2160p, from download.blender.org or archive.org (the films are
CC-BY, though some archive.org items leave the licence field empty), recorded with its URL and
sha256. Coffee Run (2020) is left out before download: its best copy found is 536 px high.

Frames: shot cuts from ffmpeg's scene score (> 0.3), then one frame at the middle of each shot
of at least 1 s, with letterboxing found by `cropdetect` and cut off first. The 45 are an even
spread over those shots, so no shot gives two frames. Sources run from 804 to 2160 px high,
so **every frame's centre square is resized to 800 px, then to 1024**: each film then passes
through the same effective resolution, and sharpness cannot stand in for the film.

Remove by eye: titles, credits and logos; black, fading or near-empty frames; frames mostly
motion blur; and a repeat of the same setup and framing (keep the lower index).

**Known confound, reported, not corrected:** characters and sets recur within a film, so
test8 rewards content matching as well as look.

### Secondary sets

- **test3** (seven public-domain illustrators) and **test4** (anime) are not re-run. Their
  results stand.
- **flickr6**, exploratory only: the August photograph set (`data/photo-style-import/`, six
  modern colour photographers, 15 each). Below the 30-per-style protocol, so it decides
  nothing. It is the only colour photography available without an API key.

## Settings

Unchanged from `../style-model-runs/PREREGISTRATION.md`. Baseline `0.30 CLIP + v2 L7`.
Primary: **K** `kalo final` alone, **C** `csd final` alone, **P** `0.30 PE + v2 L7`. Also
reported: `0.38 CLIP + v2 final` (the app on `main`), CLIP alone, `pe final` alone, the CLIP
blends of K and C, and the QuickGELU CLIP rows. Sweep: 4 references × 20 seeded draws (seed 0)
per style.

## Rules

For each primary setting and each of test6, test7 and test8, Δ is its mean AUC minus the
baseline's:
- **helps:** Δ ≥ +0.03, and a higher per-style mean than the baseline on more than half the
  styles;
- **hurts:** Δ ≤ −0.03;
- otherwise a **tie**.

Each primary setting then gets one verdict over the three media:
- **general**, if it hurts on none and helps on at least two;
- **not general**, if it hurts on any;
- **neutral outside drawn art**, otherwise.

K's test7 result is an upper bound: a "helps" there does not count toward "general", while a
"hurts" does. Report per-style AUC, paired wins over the 20 draws and the SD over draws.

## Hypotheses (each reported, none decides adoption)

- **H5.** K does not help on test6: an anime-trained model does not carry over to photographs.
- **H6.** C helps on test7: a style model trained on artist labels does well on paintings.
- **H7.** On each of test6, test7 and test8, the baseline scores at least
  `0.38 CLIP + v2 final` − 0.03: the layer-7 default does not lose outside drawn art. This
  bears on the open M7 item about the layer-7 default.

## Amendments before any image was viewed (2026-11-03)

1. **test8's scene threshold is 0.15, not 0.3.** At 0.3 the first cut found 9–119 shots per
   film, implausibly few for fast-cut films: 22 in Charge's 263 s, 36 in Sprite Fright's
   630 s, 9 in Glass Half's 193 s. The frames from that cut were deleted unviewed. A false cut
   only splits a shot, and pHash plus the by-eye repeat rule remove the doubled frames. This
   changes a build setting, not a rule or a model setting.

## Amendments after screening, before any score (2026-11-03)

2. **test7 is also scored without `tenjin_hidetaka`.** Its contact sheet is nearly all mecha
   on space or battle grounds, a subject no other test7 artist shares: a content confound like
   test4's `edomura_niniko`. A verdict that flips without it is reported as fragile; the rules
   above still decide it. Written after embedding, before any setting was scored.
3. **flickr6 is cropped like the others** (1024² centre crop, JPEG q90), from the August files.
   87 of its 90 originals are under 1024 px, so most are upscaled. It stays exploratory.
4. **Glass Half drops** under build step 4: 17 shots of at least 1 s. test8 is 10 films,
   292 images (26–30 each).
5. **test6's missing files are fetched 1920 px wide.** Commons answered about one request in
   five with 429, and Lange stalled at 10 of 45. No test6 image had been viewed.
   **Corrected after the review:** the files already fetched are **3840 px wide**, not 2048:
   Commons served its 3840 size for a 2048 request, so the pre-registered "2048 px wide
   copies" never happened either. Lange's 25 files at 1920 are half the resolution of the
   rest, not 6% smaller. This was reported rather than corrected on the wrong premise; the
   sensitivity run without Lange (build step 5) is in `rules.txt`.
6. **test6's border rule is measured, then checked by eye** (a screening method, made while
   screening). Judging "about a tenth" from 150 px tiles was inconsistent between
   photographers, so `border_check.py` measures the uniform dark or light edge bands of each
   centre square. Every file over 0.10 was looked at: flat sky and plain walls were kept,
   real rebates and margins removed. Toned mats and handwritten captions, which it cannot see,
   were judged by eye. Delano, Vachon (whose category includes his 1970s colour work) and
   Wolcott fall below 20 under the colour and border rules and drop; Hine, the reserve,
   is fetched. Written before any test6 embedding.
7. **Hine is not used: test6 runs with five photographers** (Lange, Rothstein, Shahn, Evans,
   Mydans), the pre-registered minimum. His category (9,305 files) was still crawling after
   an hour and was stopped; no Hine image was fetched or viewed. test6's screening was
   finished before any score was seen (`exclude.json` 13:48, first scores 14:16). **But the
   decision to drop Hine came after** test7, test8 and flickr6 were scored, when test6 was
   known to decide P between "general" and "neutral". Which way a sixth photographer would
   push was unknowable, and a capped crawl of Hine was not tried.

## Harness checks

- `--verify` for CLIP and DINOv2 on each set: cosine ≥ 0.999 against the app's stored vectors.
- The same image extracted twice gives cosine 1.000 for each new model, and every vector has
  norm 1.
- One configuration cross-checked between the sweep and `style_gate.py`.
