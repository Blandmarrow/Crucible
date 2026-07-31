# The `GET /images/` filter parameters

The image listing endpoint (`backend/routers/images.py::list_images`) carries a long tail of
filter params beyond `dataset_id`/`subfolder` — score ranges, quality flags, dimensions and
format, detection and mask predicates, caption length, license and frame lineage. They are one
shared contract with four consumers, which is why they live in their own file: the gallery's
filter bar (`docs/dev/gallery.md` § Gallery filters), the Statistics page's bar click-through
into `BucketPanel` (`docs/dev/statistics.md`), the video frame-lineage deep link
(`docs/dev/video-ui.md`), and the license filters shared with export
(`docs/dev/provenance.md`, `docs/dev/export-licensing.md`).

Every param is optional and additive (AND), and an **absent** value is *no filter*, never
"match nothing". A *falsy* one is not the same thing: several params are gated on
`is not None` rather than on truthiness, so `false` and `""` are real filters wherever the row
below says so — `license_missing=false` selects images that *have* a license,
`captioned=false` selects uncaptioned ones, `detection_label_exact=""` matches detections
whose label is empty, and `subfolder=""` is the dataset root. Check the row rather than
assuming.

**`GET /images/` filter extensions** (in `backend/routers/images.py`):

| Param | Type | Effect |
|---|---|---|
| `search` | `str` | Case-insensitive LIKE filter across `original_filename` and `caption_text` (OR logic) |
| `score_field` | `str` | Which score column `min_score`/`max_score` apply to (whitelist-validated; defaults to `aesthetic_score`) |
| `score_is_null` | `bool` | Filter images where `score_field IS NULL` (used for "unscored" bucket) |
| `score_filters` | `str` (JSON) | JSON-encoded array of `{field, min?, max?}` objects; each entry adds an AND condition with **both bounds inclusive**; fields validated against `_ALLOWED_SCORE_FIELDS` whitelist |
| `subfolder` | `str` | **Descendant-inclusive**: `Image.subfolder == s OR Image.subfolder LIKE '<escaped s>/%'`, with `%` and `_` escaped, so selecting a parent folder in the sidebar also lists every nested subfolder's images. `is not None`-gated — `""` is the dataset root and a real filter, not "no filter" |
| `captioned` | `bool` | `true` = `caption_text != ''`, `false` = `caption_text == ''`. The gallery's All / Captioned / Uncaptioned control; `is not None`-gated, so `false` is a filter |
| `min_score` / `max_score` | `float` | Range on whatever column `score_field` names, **both bounds inclusive**. Skipped entirely when `score_is_null` is true |
| `quality_flag` | `str` | Filter by JSON flag key in `quality_flags` (e.g. `is_blurry`); validated against `utils.ALLOWED_FLAG_KEYS`, and an unknown key is a **400**, exactly like `score_field` |
| `file_size_min` / `file_size_max` | `int` | `file_size_bytes` range (bytes), **both bounds inclusive** |
| `mp_min` / `mp_max` | `float` | `width × height` megapixel range, min **inclusive** / max **exclusive** |
| `ar_min` / `ar_max` | `float` | Aspect ratio `width / height` range, min **inclusive** / max **exclusive** |
| `format_filter` | `str` | Exact `Image.format` match (e.g. `PNG`) |
| `detection_label` | `str` | `EXISTS` subquery: only images that have at least one detection with `label ILIKE '%...%'` (substring — gallery search) |
| `detection_label_exact` | `str` | Exact `Detection.label == value`; combined with the score params below into **one** `EXISTS` subquery so all conditions apply to the same detection row (Detections & Masks label bar click-through) |
| `detection_score_min` / `detection_score_max` / `detection_score_null` | `float` / `bool` | Detection-score range (min inclusive, max exclusive) or NULL-score ("unscored") match, folded into the same `EXISTS` subquery as `detection_label_exact` |
| `mask_coverage_min` / `mask_coverage_max` | `float` | Per-image `SUM(Detection.mask_area)` clamped to 1.0 (`case`), min inclusive / max exclusive; also requires ≥1 detection (`EXISTS`) so the population matches the coverage histogram |
| `detection_count_min` / `detection_count_max` | `int` | Correlated `COUNT(Detection.id)` coalesced to 0 (so the "0" bucket = images with no detections works), both bounds inclusive |
| `caption_words_min` / `caption_words_max` | `int` | Word count range — SQL approximation via `length(trim(text)) - length(replace(trim(text), ' ', '')) + 1`; `min` is inclusive, `max` is exclusive |
| `license_filter` | `str` (JSON) | JSON-encoded **array** of effective license ids (never comma-separated — an `other:<free text>` id may contain commas), parsed by `utils.parse_license_filter_param`. Matched against `COALESCE(NULLIF(images.license,''), datasets.license, '')` over a join. Any array containing a blank entry (`[""]`, `["  "]`, or a mixed `["CC-BY-4.0", ""]`) is **rejected with 400** — use `license_missing` for unlicensed images; an empty array (`[]`) is no filter |
| `source_video_id` | `str` | Frame lineage: only images extracted from that video, wherever curation has since filed them. A plain indexed equality on `Image.source_video_id` (no join, no `EXISTS`), applied right after the subfolder block. Truthiness-gated, so `""` is no filter; no allowlist — the value is an opaque uuid and an unknown one correctly returns zero rows. See `docs/dev/gallery.md` § Gallery filters and `docs/dev/video-ui.md` |
| `license_missing` | `bool` | `true` = only images whose effective license is `""`; `false` = only images that have one. This is the Licenses panel's unlicensed click-through |
| `caption_tokens_min` / `caption_tokens_max` | `int` | GPT-2 BPE token count range — **pure SQL** over the persisted `func.coalesce(Image.caption_token_count, 0)` column (`min` inclusive, `max` exclusive), so ordinary `ORDER BY`/`OFFSET`/`LIMIT` paging applies; no `tiktoken` in the request path. See `docs/dev/gallery.md` § Gallery filters |
