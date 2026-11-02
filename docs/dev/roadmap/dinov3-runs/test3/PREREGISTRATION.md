# DINOv3 test on test3 (public-domain illustrators) — pre-registration

Written 2026-11-02, while the image list was still being crawled: before any image was
downloaded, viewed or embedded.

## Data
`test3` in the dev DB: colour plates by golden-age fairy-tale illustrators from Wikimedia
Commons (public domain), one style per artist, aiming for 40–50 each and at least 4 artists.
The candidates are Rackham, Dulac, Nielsen, Bilibin, Goble, Bauer and Clarke, depending on
what the crawl yields. Selection is an even spread through each artist's category listing.
I remove only non-plates (covers, text pages, photos, monochrome), judged from a contact
sheet, without looking at any score.

Every image is centre-cropped to a square and saved as 1024×1024 JPEG q90 **before import**,
so format, resolution and aspect cannot split with the label. One cue cannot be removed:
each artist's plates come from different printed books and scans, so paper tone and print
texture vary by artist as well as by hand.

## Settings
Reuse the `test2` design: 4 references × 20 seeded draws per style, positives = the rest of
that style, controls = every other style.

## Rules
1. **Fixed settings, the primary rule.** Each candidate is 30% CLIP + a fixed layer:
   `v3b224 L7`, `v3b512 L7`, `v3l224 L14`, `v3l512 L14`. The baseline is
   `0.30 CLIP + v2 L7`. A candidate passes if
   - (a) its mean AUC over styles beats the baseline by **≥ 0.03**, and it beats the baseline
     on **more than half** of the styles; and
   - (b) its layer-alone margin in `pools` is no worse than DINOv2 L7's, **and** its 0.30
     blend margin is no worse than the baseline blend's.
2. **Leave-one-out, the plan's original rule.** Compare the held-out mean of the all-DINOv3
   family against the DINOv2 family (alone and blended settings, any layer, weights 0.20 /
   0.30 / 0.38). DINOv3 passes if it is higher by **≥ 0.03**.

## The test2 hypotheses, tested here
Each is a test of a claim `test2` only suggested:
- **H1, relative depth.** DINOv3's leave-one-out picks fall at 40–60% of depth (L5–L7 of 12,
  L10–L14 of 24).
- **H2, the CLIP weight.** At the fixed layers, the layer alone beats the 0.30 blend by
  **≥ 0.02** mean AUC, for the majority of the four candidates.
- **H3, resolution.** 512 px beats 224 px at the fixed setting by **≥ 0.04** for the same
  model size.
- **H4, size.** L/16 beats B/16 at the same resolution by **≥ 0.04**.

A difference under 0.03 is reported as a tie. Report each per-style AUC, the paired wins
over draws, and the spread. Everything else is exploratory.

## Amendment before any embedding (2026-11-02, 23:3x)
Seven artists made it, but at **25–30 each** (200 images) rather than 40–50: Clarke, Nielsen
and Bauer have no more usable plates once duplicates, covers and text pages are removed. The
others were capped at 30 to keep the styles balanced. Nothing else changes.
