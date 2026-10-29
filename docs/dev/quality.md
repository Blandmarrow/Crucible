# Quality — scoring, duplicates and style

The Quality page measures images and compares them. Its three parts are per-image scoring
(the scorers, the columns and flags they write, and the `scores_stale` bit that qualifies
them), duplicate detection and resolution, and style similarity. This file is the family's
entry point: the tables below route from a code path to whichever file in
`docs/dev/quality/` owns it. The models these scorers load are managed by
`docs/dev/ml-models.md`; the threshold settings are `docs/dev/settings.md`.

## The family

| File | Covers |
|---|---|
| [scoring.md](quality/scoring.md) | The scorers and their columns, the failure contract, the scoring job's model lifecycle, flag thresholds, e2e coverage |
| [aesthetic-model.md](quality/aesthetic-model.md) | LAION vs V2.5, the `aesthetic_model` marker, the two destructive consumers, per-model coverage |
| [scores-stale.md](quality/scores-stale.md) | The `scores_stale` bit: `record_in_place`, the clear predicate, the surfaces |
| [duplicate-detection.md](quality/duplicate-detection.md) | `find_duplicates_sync`, and the duplicate flag as a relationship kept true by `duplicate_service.py` |
| [duplicate-resolution.md](quality/duplicate-resolution.md) | The duplicates read path, same-source groups, the panel's bulk bar and `resolve_duplicates` |
| [style-similarity.md](quality/style-similarity.md) | The `embedding_type` modes, what each is measurably worth, all-layers scoring |
| [style-match.md](quality/style-match.md) | `StyleSimilarityRun`, the distribution endpoint, the percentile contract, the meters |

## Where things live

Each row names the file that **owns** that code. A `+` clause is a second genuine owner,
not a see-also, and says which part of the code it owns.

### Backend

| Editing… | Read |
|---|---|
| `backend/routers/quality.py` | `docs/dev/quality/scoring.md` for `POST /quality/score`, the job and the model endpoints; + `docs/dev/quality/duplicate-detection.md` for `_flag_duplicates`; + `docs/dev/quality/duplicate-resolution.md` for `GET /quality/duplicates/{dataset_id}` and the resolve; + `docs/dev/quality/style-similarity.md` for `POST /quality/style-similarity` and `embed-references`; + `docs/dev/quality/style-match.md` for the GET and `_record_style_run`; + `docs/dev/quality/aesthetic-model.md` for `aesthetic-coverage` and `only_mismatched` |
| `backend/ml/technical_scorer.py` | `docs/dev/quality/scoring.md`; + `docs/dev/quality/duplicate-detection.md` for `find_duplicates_sync` |
| `backend/ml/aesthetic_scorer.py`, `backend/ml/aesthetic_v2_5_scorer.py` | `docs/dev/quality/aesthetic-model.md` for the shared batch loop and the marker; + `docs/dev/quality/scoring.md` for the columns and the failure contract |
| `backend/ml/dino_scorer.py`, `backend/ml/nsfw_scorer.py` | `docs/dev/quality/scoring.md` |
| `backend/ml/similarity_scorer.py` | `docs/dev/quality/style-similarity.md` |
| `backend/services/duplicate_service.py` | `docs/dev/quality/duplicate-detection.md` |
| `backend/models/style_run.py` | `docs/dev/quality/style-match.md` |
| `backend/services/dataset_service.py` (its style half) | `docs/dev/quality/style-match.md` — `get_style_distribution`, `_style_quantiles`, `_style_validator` |
| `backend/services/threshold_service.py` | `docs/dev/quality/scoring.md` § Flag thresholds; + `docs/dev/settings.md` for the settings surface |
| `backend/utils.py` (`record_in_place`) | `docs/dev/quality/scores-stale.md` |
| `backend/scripts/style_gate.py` | `docs/dev/quality/style-similarity.md` § What the modes are actually worth |

### Frontend

| Editing… | Read |
|---|---|
| `frontend/src/pages/QualityPage.tsx` | `docs/dev/quality/scoring.md` § Frontend coverage for the scoring panel; + `docs/dev/quality/aesthetic-model.md` for the picker; + `docs/dev/quality/duplicate-resolution.md` for the duplicates panel; + `docs/dev/quality/style-similarity.md` for the style picker |
| `frontend/src/constants/aestheticModels.ts` | `docs/dev/quality/aesthetic-model.md` |
| `frontend/src/constants/styleModes.ts` | `docs/dev/quality/style-match.md` § The shared mode copy |
| `frontend/src/utils/percentile.ts` | `docs/dev/quality/style-match.md` § The client-side percentile contract |
| `frontend/src/components/image/StyleMatchPanel.tsx` | `docs/dev/quality/style-match.md` § The rendered surfaces |
| `frontend/src/components/gallery/ImageCard.tsx` (its style meter) | `docs/dev/quality/style-match.md` § The rendered surfaces |
| `frontend/src/pages/ImageDetailPage.tsx` (`DinoLayerBreakdown`) | `docs/dev/quality/style-match.md` § `DinoLayerBreakdown` — the axis it used to lie about |
| `frontend/src/components/gallery/SelectionToolbar.tsx` (its style-similarity section) | `docs/dev/quality/style-similarity.md` for the `image_ids` scope; + `docs/dev/quality/style-match.md` § Invalidation |
| `frontend/src/pages/StatsPage.tsx` (the aesthetic producer breakdown) | `docs/dev/quality/aesthetic-model.md` § Per-model coverage and the re-score offer |
| `frontend/src/components/quality/StyleReferencePicker.tsx` | `docs/dev/quality/style-similarity.md` |
| `frontend/e2e/quality.spec.ts` | `docs/dev/quality/scoring.md` § Frontend coverage |
