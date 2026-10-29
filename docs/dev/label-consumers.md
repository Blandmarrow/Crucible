# Labels — where an assignment shows up

Every subsystem outside the label vocabulary's own CRUD that reads or carries a label
assignment: the `GET /images/` filter block and its shared validator, the export narrowing
and its client encoder, the `VersionImageState.label_ids` snapshot mirror and restore's
Pass 2c, and the cross-dataset copy/duplicate/move paths plus the five same-dataset
derivative sites. The vocabulary itself — the two tables, `label_service.py`, the `/labels`
router and `POST /labels/assign` — is `docs/dev/labels.md`; the frontend surfaces are
`docs/dev/labels-ui.md`. The param contract the filters join is `docs/dev/image-filters.md`,
the export filter table is `docs/dev/export.md`, and the snapshot passes the mirror rides
are `docs/dev/versioning-service.md`.

## Filters

`label_filter` / `label_match` / `label_missing` are appended to
`ImageFilterParams`, so `ImageIdsParams` and `ImageListParams` inherit them and
`/images/`, `/images/count` and `/images/ids` agree by construction — see
`docs/dev/image-filters.md`. `label_filter` is a JSON array in a string, the same
encoding as `license_filter`, parsed by `utils.parse_id_list_param`.

`label_match` is a plain `str`, **not** a `Literal`, so a bad value is a **400
from the shared filter validator** rather than a 422 from per-route query
parsing — the contract `test_bad_input_is_rejected_identically_by_all_three`
pins. On export it *is* a `Literal` and so gives a 422; that asymmetry is
deliberate and is the one `_parse_flags` already documents.

The SQL block sits after the detection-count block and before `score_filters`,
and uses **correlated `EXISTS` only, never a join**: `/count` runs the same
builder over `select(func.count(Image.id))`, so a join to `image_labels` would
count a two-label image twice and duplicate rows in `/images/`. The license block
stays the only one that mutates the FROM clause, and stays last.

- `label_missing is True` → `~exists`; `is False` → `exists`.
- `label_match="any"` (the default) → one `EXISTS` with `label_id.in_(ids)`.
- `label_match="all"` → one `EXISTS` **per id**, preferred over
  `GROUP BY … HAVING COUNT(...)`, which would need a join and a grouping this
  shared builder must not introduce. Each is index-backed on
  `ix_image_labels_image_id`, and nobody ticks fifty labels.

The 400s live in **`utils.validate_label_filter_params`**, not inline in
`_apply_image_filters`, because export calls the same function — see
`docs/dev/shared-utilities.md`. It raises for: a blank entry in `label_filter`
(the `license_filter` reasoning — silently dropping a blank narrows a mixed list
and voids an all-blank one, both silent lies); an invalid `label_match`;
`label_missing=true` combined with a non-empty `label_filter`, which is
unsatisfiable and indistinguishable from a broken filter; and **more than
`MAX_LABEL_FILTER_IDS` (100) ids**, since `match=all` builds one `EXISTS` per id
and ~1,000 of them overflow SQLite's `SQLITE_MAX_EXPR_DEPTH` into an uncaught 500.
Unparseable JSON is the fifth, and stays in `parse_id_list_param`.

`_apply_image_filters` calls the validator **unconditionally**, beside the
`score_field`/`quality_flag` checks rather than inside the label block:
`?label_match=either` with no filter at all is still bad input, and all three
endpoints must refuse it identically.

`label_filter_clause` additionally raises `ValueError` on a `match` that is
neither `"any"` nor `"all"`, instead of falling through to "any" — a direct
service call with a typo cannot then quietly answer the wrong question. HTTP
callers never reach it, having been 400'd upstream.

## Export

`_run_export_loop` and `preview_export` each take the three params and apply
`label_filter_clause` right after the `subfolders` where. Like `subfolders`, the
label filter **narrows the query** rather than appearing in the exclusion tally:
it defines which images the export is about, not an exclusion over a fixed
population. So `image_count` shrinks and no new counter is needed. The three
request bodies in `routers/export.py` and the preview `GET` carry them too; the
preview parses via `parse_id_list_param`.

**Export validates exactly what the gallery validates.** All four POST/GET
handlers call `utils.validate_label_filter_params` — the three POSTs in the
*request* path beside their `_normalize_mask_labels` normalizers, so a bad shape
is a 400 to the client rather than an already-enqueued job failing later.

The client half matters as much: `ExportPage` encodes the trio through a single
`labelParams()` helper used by **both** the export POST bodies and the preview
query, and it **omits every falsy value**. `label_missing: false` is not "no
filter" — in the documented three-endpoint contract it means *"only images that
carry a label"* — so sending the plain boolean default silently dropped every
unlabelled image from every export while the preview, which stripped falsy values,
promised the full count. The server behaviour is correct and deliberately
unchanged; two encodings of one thing was the bug. `frontend/e2e/export-plain.spec.ts`
guards it by asserting on the files on disk, not on the preview.

`_write_image` and the caption sidecar writer stay untouched — see the
labels-are-not-tags invariant at the top of `docs/dev/labels.md`.

## The mirror column

`VersionImageState.label_ids` is a NOT NULL JSON list with a `'[]'`
`server_default`. Its rules, in the model's comment and pinned by
`test_labels_survive_rebuild_paths.py`:

- **Ids, not names.** A *rename* means "same concept, new spelling" — with ids a
  restore reattaches the concept under its current name for free, where names
  would name a label that no longer exists. A *delete* is a deliberate removal,
  and with ids a restore honestly drops the assignment rather than resurrecting
  it. Storing both is worse than either: a rename would flip the blob for every
  image at once and produce a full-dataset "modified" diff for a vocabulary edit
  that changed no image.
- **NOT NULL with `'[]'`**, not nullable — a NULL/`[]` split would make every
  pre-migration row differ from every post-migration row.
- **Stored sorted.** The diff compares with `!=`, so an unsorted list would
  report a reorder as a change.
- **No FK**, matching `image_id`/`source_video_id` on the same model: a snapshot
  must survive the deletion of what it names.

## Versioning and cross-dataset hooks

`create_snapshot` prefetches `labels_by_image` once for the dataset (a per-image
query inside the state loop would be N+1 over 20k images) and passes
`label_ids=labels_by_id.get(img.id, [])` — never `or None`; the column is NOT
NULL.

`restore_snapshot` writes labels at the **end of Pass 2c**, before the
`db.commit()` that is the DB→filesystem boundary. Four things about it:

- It keys on **`p.img.id`, not `p.state.image_id`** — Pass 0b can fork a fresh
  uuid or adopt a different row, and writing the snapshot's id would label an
  image in another dataset, or nothing at all.
- Plans with `p.img is None` (`skip_recreate`) are skipped.
- Ids are resolved through `live_label_ids`, with one aggregate warning for
  assignments naming a label deleted since the snapshot — there is no FK to
  catch it.
- `set_labels` is an **authoritative replace, not a merge**: a restore means "the
  dataset looked like this", so a label added after the snapshot disappears,
  exactly as a caption edit does. It deletes only for image ids in `wanted`, so
  `handle_extra_images="keep"` images keep theirs.

A row Pass 0b forks (its image now lives in another dataset) needs no id
remapping at all — the direct payoff of the vocabulary being global.

The diff triple: `VersionImageState.label_ids` is in `_DIFF_COLS` and
`_DIFF_COMPARE_FIELDS`, and **not** in `_HEAVY_DIFF_FIELDS`. It is mutable state
(attach/detach) and therefore diffed, unlike the immutable lineage carve-out
beside it. Because the *comparison* runs on ids, a rename produces no diff at all
— the correct answer. Ids are resolved to names once per diff, after the modified
list is built, and joined to a comma-separated string server-side, so
`DiffModal` needs no change; an id with no row renders `(deleted)`.

Cross-dataset:

- **`batch_copy_dataset`** binds `new_img = Image(...)` into an `id_map` and
  calls `copy_labels` between the insert loop and the FS copies, preserving that
  function's deliberate stage-DB → copy-files → commit ordering.
- **`duplicate_dataset`**, Step 2A (on-disk) builds the same `image_id_map` and
  calls `copy_labels` after the loop — including after the cancellation `break`,
  so a cancelled duplicate keeps labels for whatever it did copy. Step 2B (from a
  snapshot) reads `state.label_ids` off the mirror and resolves against the live
  vocabulary exactly as restore does, in one query for the whole run.
- **`batch_move_dataset`: no change**, and that is a property asserted rather
  than remembered.

Labels travel on a copy while **detections do not** (there are no `Detection`
references in `dataset_service.py`). Not a bug — "this image is a reject" is a
fact about the image — but surprising enough that someone will otherwise "fix"
it. *Snapshots* are the exception: since GPT-026 a snapshot mirrors detections
too (`VersionImageState.detections`) and a restore writes them back, because a
restore swaps the pixels their geometry is measured against. Duplicating a
dataset, from disk or from a snapshot, still leaves them behind. See
`docs/dev/versioning-service.md`.

**Same-dataset derivatives carry labels too**, for the same reason and by the
same rule as `copy_provenance`: a crop is the same picture at a different
framing, so a fact about the picture is still true of it. Five sites build a new
`Image` beside its parent and each calls `copy_labels`:

| Site | Shape |
|---|---|
| `routers/lut.py`, `routers/upscaling.py`, `routers/detection.py` (crop-to-detection) | Accumulate `derivative_ids[parent] = new_img.id` through the loop, drain with one `copy_labels` **after** the loop — so a cancelled run still labels what it did copy — before the trailing commit |
| `routers/images.py::crop`, sync copy-mode tail | The `id=str(uuid4())` is assigned explicitly (the `batch_copy_dataset` precedent) so the join rows can name the crop, and `copy_labels` rides the same transaction |
| `routers/images.py::crop`'s nested `_run_crop_upscale` | Runs in its own session with the parent ORM object gone, so `job_cfg` carries `parent_id`; `copy_labels` re-reads the parent's labels itself, and a `flush()` comes first because `Image.id` is a Python-side default |

A *score* must not travel the same way — those pixels are not the scored pixels —
and `test_video_lineage_mirrors._score_carriers` names these same five sites as
its deliberate exclusion. The label guard is the inverse and is executable:
`test_labels_survive_rebuild_paths.py::test_every_same_dataset_derivative_carries_labels`
walks each function's AST for the `copy_labels` **call** (not a kwarg — four of
the five pass the map positionally).
