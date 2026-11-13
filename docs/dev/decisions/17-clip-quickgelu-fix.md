# Decision 17: CLIP is built with QuickGELU, and its old outputs are cleared or re-labelled

*Index: [decisions.md](../decisions.md).*

- **Date:** 2026-11-13.
- **Status:** **Decided** (2026-11-13, by the user): all three rulings below, the third
  after seeing the measurement.
- **Decision:** the app builds OpenAI's CLIP ViT-L/14 with QuickGELU, the activation its
  weights were trained with, through one builder, `aesthetic_scorer.build_clip_vit_l14`.
  Migration `f3a8d1c6b9e2` deals with what the old plain-GELU build left behind:
  - **Every `clip_embedding` is cleared.** *Score what's missing* with Style embeddings
    ticked refills them.
  - **LAION aesthetic scores are re-labelled** from `laion` to `laion_gelu`, in `images`
    and `version_image_states`. They stay readable, and the existing marker machinery offers
    to re-score them.
  - **Watermark results are cleared** wherever the CLIP watermark check ran (a
    `watermark_score` exists): the score, and `has_watermark` in `quality_flags`, in both
    tables. A flag on a row with no score came from a detection scan and is kept.
  - The migration copies the database to `backups/<stem>.pre-clip-quickgelu.db` first.
- **Rationale:**
  - **The old build was measurably wrong, and by more than the style result suggested.**
    The 2026-11-03 style runs found that a correct build scored about +0.015 AUC higher.
    `backend/scripts/clip_build_delta.py` then scored all 1,033 dev images under both
    builds, with these results:
    - CLIP vectors: mean cosine 0.944, minimum 0.837.
    - LAION score: mean change 0.17 points, largest 0.96, Spearman 0.82.
    - Watermark score: mean change 0.10, largest 0.61.
    - `has_watermark` at 0.6: 83 of 1,033 flags flipped (57 on, 26 off).

    Full table: `docs/dev/quality/style-evaluation.md` § The CLIP QuickGELU fix.
  - **Old and new values must never be compared.** A vector has no marker, so the only
    honest state for an old one is NULL. Every reader already skips NULL, and the Quality
    page already reports the gap. A LAION score already has a marker, so re-labelling it
    reuses the coverage line, the *Re-score N with LAION* offer and the mixed-model
    warnings, with no new code path.
  - **Snapshots are changed too.** A restore writes a state row back verbatim, so an old
    `laion` marker or watermark result in a snapshot would come back unmarked.
  - **The watermark flag drives actions.** The watermark-removal (inpaint) scope and the
    flag filters act on it, so wrong flags point those tools at the wrong images.
- **Rejected alternatives:**
  - **A build marker on `clip_embedding`.** It would keep old vectors usable until each was
    replaced, but every reader would need to filter on it, and the scan would need a
    re-embed-the-old-ones option. That is more code and more places where mixing could go
    wrong.
  - **Clearing the LAION scores.** It is simpler, but the scores would be gone until a
    re-scan, when a re-label keeps them readable and already has a re-score path.
  - **Leaving watermark results and documenting the shift.** 8% of flags would stay wrong
    with nothing to say which ones.
  - **Clearing watermark results on live rows only.** Restoring an old snapshot would bring
    old results back unmarked.
  - **Clearing every `has_watermark`.** That would wipe flags a detection scan set, which do
    not come from CLIP and are still right.
- **How it's verified:**
  - `backend/tests/test_clip_build_guard.py` checks the source: no open_clip model is built
    outside the builder, the builder passes `force_quick_gelu`, and only the measurement
    script asks for the old build.
  - `backend/tests/test_migration_clip_quickgelu.py` covers the migration: what it clears,
    what it re-labels, the detection-set flag it keeps, the backup, and downgrade.
  - `backend/tests/test_aesthetic_model_marker.py` checks that `laion_gelu` rows show in the
    coverage counts and are picked up by the re-score offer.
  - **Timed at scale:** on a 1M-row, 17 GB bench library the migration took 194 s: 28 s
    for the backup and 166 s for one UPDATE pass per table. Three separate passes took
    350 s.
  - **Run end to end on a throwaway instance** on 2026-11-13: the real `alembic upgrade`
    over seeded old-build rows, then a re-embed, a watermark re-check, a LAION re-score and
    a CLIP style run with an uploaded reference, all on the GPU. The uploaded reference's
    vector matched the stored one at cosine 1.000000.
  - **The build itself is checked only by hand**, because CI has no torch. It was built on
    open_clip 3.3.0: 36 QuickGELU modules (24 image, 12 text), and no mismatch warning. The
    builder raises if a future open_clip ignores the flag.
