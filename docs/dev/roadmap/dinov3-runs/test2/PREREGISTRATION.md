# DINOv3 test on test2 — pre-registration

Written 2026-11-02, after the dataset was chosen and before any embedding of it existed.

## Data
`test2` in the dev DB: 234 *Highlander: The Search for Vengeance* frames vs 564 *Hellsing
Ultimate* images (Hellsing pre-split by subject: characters, expressions, effect, action,
environment, prop). The 6 `pruned_synthetic_*` files are excluded. Two styles, both 2D
anime, overlapping dark subject domain.

## Why the rule changed from the plan
The plan's rule (a) chose DINOv3's best setting by leave-one-out over ≥ 4 styles. With two
styles, leave-one-out picks on one style and tests on the other, and best-of-~400 on two
configurations is selection, not measurement. So the comparison is at **fixed settings**,
named here before the data.

## Fixed settings
- Today: `0.38 CLIP + v2 final`
- Baseline for the rule: `0.30 CLIP + v2 L7` (the branch default)
- Candidates, same weights, same relative depth:
  `0.30 CLIP + v3b224 L7`, `0.30 CLIP + v3b512 L7`,
  `0.30 CLIP + v3l224 L14`, `0.30 CLIP + v3l512 L14`
- August's own pick, reported beside them: `0.20 CLIP + v3b512 L7`

## Rule
A DINOv3 candidate passes if:
- **(a)** its mean AUC over the two configurations (Highlander as reference, Hellsing as
  reference; 4 references, 20 seeded draws each) beats the baseline by **≥ 0.03**, and it
  is lower on **neither** configuration; and
- **(b)** in `style_crossed_probe pools` (CLIP subject bins), its fixed layer's margin
  (style minus subject leak) is **no worse** than DINOv2 L7's.

`synthetic` (30 bases) is reported for its style-over-same-picture column as context, not as
part of the rule.

Everything else — best layer per model, best blend weight, leave-one-out picks — is
**exploratory** and reported as such. Differences under ~0.04 are ties.
