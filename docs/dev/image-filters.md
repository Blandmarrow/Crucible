# The `GET /images/` filter parameters

The image listing endpoint (`backend/routers/images.py::list_images`) carries a long tail of
filter params beyond `dataset_id`/`subfolder` — score ranges, quality flags, dimensions and
format, detection and mask predicates, caption length, license and frame lineage. They are one
shared contract with seven consumers, which is why they live in their own file: the gallery's
filter bar (`docs/dev/gallery.md` § Gallery filters) and the detail view's boundary prefetch,
which replays that same memo out of the stored nav context
(`docs/dev/image-detail.md`), the Statistics page's bar click-through
into `BucketPanel` (`docs/dev/statistics.md`), the video frame-lineage deep link
(`docs/dev/video-ui.md`), the license filters shared with export
(`docs/dev/provenance.md`, `docs/dev/export-licensing.md`), and the two endpoints in
§ The same filters over the whole result set below.

Every param is optional and additive (AND), and an **absent** value is *no filter*, never
"match nothing". A *falsy* one is not the same thing: several params are gated on
`is not None` rather than on truthiness, so `false` and `""` are real filters wherever the row
below says so — `license_missing=false` selects images that *have* a license,
`captioned=false` selects uncaptioned ones, `detection_label_exact=""` matches detections
whose label is empty, and `subfolder=""` is the dataset root. Check the row rather than
assuming.

## The parameter table

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
| `label_filter` | `str` (JSON) | JSON-encoded **array** of label ids, the same encoding as `license_filter` and parsed by `utils.parse_id_list_param` (which deliberately is *not* `parse_license_filter_param` — that one also runs license normalization). Applied as a correlated `EXISTS` over `image_labels`, **never a join**: `/count` runs this same builder over `select(func.count(Image.id))`, so a join would count a two-label image twice. A blank entry is a **400**, for the `license_filter` reasoning, and so is a list of more than `MAX_LABEL_FILTER_IDS` (100) ids — under `label_match="all"` the id count is an expression-tree depth, and ~1,000 of them overflow SQLite's `SQLITE_MAX_EXPR_DEPTH` into an uncaught 500. See `docs/dev/labels.md` |
| `label_match` | `str` | `"any"` (the default, one `EXISTS` with `label_id.in_(ids)`) or `"all"` (one `EXISTS` per id). A plain `str` rather than a `Literal`, so a bad value is a **400 from the shared validator** rather than a per-route 422 |
| `label_missing` | `bool` | `true` = only images carrying no label at all (`~exists`); `false` = only images that carry one — **`false` is a filter, not "no filter"**, which is why every client encoder omits it when unset. Combining `true` with a non-empty `label_filter` is a **400** — unsatisfiable, and a query that always returns zero rows is indistinguishable from a broken filter |
| `caption_tokens_min` / `caption_tokens_max` | `int` | GPT-2 BPE token count range — **pure SQL** over the persisted `func.coalesce(Image.caption_token_count, 0)` column (`min` inclusive, `max` exclusive), so ordinary `ORDER BY`/`OFFSET`/`LIMIT` paging applies; no `tiktoken` in the request path. See `docs/dev/gallery.md` § Gallery filters |

The three label rows' 400s live in one place, `utils.validate_label_filter_params`,
which `_apply_image_filters` calls **unconditionally** rather than inside the label
block — `?label_match=either` with no filter is still bad input, and all three
endpoints must refuse it identically. The four export handlers call the same
function in their request path, so the two surfaces cannot drift into accepting
different shapes (`docs/dev/export.md`, `docs/dev/shared-utilities.md`).

## The same filters over the whole result set

Two sibling endpoints answer *how many* and *which ids* the same filters match, so the gallery
can offer "select all 1,240 matching filters" instead of one page at a time
(`docs/dev/gallery.md` § Gallery image selection). Both are pure reads over the identical
`where` chain.

| Route | Returns |
|---|---|
| `GET /images/count` | `{"count": n}` — `select(func.count(Image.id))` plus the filters. No ordering, no paging |
| `GET /images/ids` | `{"ids": [...], "count": n, "truncated": bool}` — the ids in the grid's own order, capped at `SELECT_ALL_ID_CAP` (20,000, a module constant in `routers/images.py`) |

`/ids` takes `sort`/`order` as well as the filters, so a truncated response is *the first
20,000 in the order the user is looking at* rather than an arbitrary slice. It fetches
`cap + 1` rows and only trims when the extra row comes back; over the cap it runs the count
query so `count` is the true total (the UI can say "the first 20,000 of 84,113") while under
it `count` is just `len(ids)` and there is no second query. The cap bounds the response *and*
every batch request body that follows from the selection.

**The parameters are one declaration, not three copies.** `ImageFilterParams` in
`backend/schemas/image.py` holds `dataset_id` plus the whole filter tail; `ImageIdsParams`
subclasses it to add `sort`/`order`, and `ImageListParams` adds `page`/`limit` on top of
that. Each route takes exactly one `Annotated[…, Query()]` model, and `routers/images.py`
applies them through two helpers: `_apply_image_filters(q, f)` (the whole `where` chain, plus
the `score_field` and `quality_flag` 400s, so bad input is rejected identically everywhere)
and `_apply_image_ordering(q, sort, order)`. The filter helper takes any select shape — the
license block's `join(Dataset, …)` carries a `select(func.count(Image.id))` as happily as a
`select(Image)` — and the ordering split is safe because `where` clauses accumulate
order-independently, even though the ordering block used to sit *between* two filter blocks.

Two traps are worth stating, because both fail quietly:

- **Route order.** `GET /{image_id}` is declared later in the same router, so `count` and
  `ids` must stay above it or FastAPI matches them as an image id.
- **A query model must be a route's *sole* query parameter.** FastAPI only unpacks a Pydantic
  model into real query params in the `len(fields) == 1` branch of
  `dependencies/utils.py::request_params_to_args`. One scalar `sort: str = "created_at"`
  declared beside it turns the whole model back into a single required query param literally
  named after the argument — the route still starts, still generates an OpenAPI document, and
  rejects every real request. That is why paging and ordering are model subclasses rather than
  plain arguments.

`backend/tests/test_image_select_all_scope.py` is the guard: it compares both endpoints
against `GET /images/` paged to exhaustion across a dozen filter shapes and several sorts,
and reads `app.openapi()` back to assert the three query-param sets are nested (`/count` ⊂
`/ids` ⊂ `/`). A filter re-declared on `list_images` alone fails CI there rather than in a
user's selection.
