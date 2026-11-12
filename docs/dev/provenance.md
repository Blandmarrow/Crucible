# Source & license provenance

This file covers where an image's source, license and attribution come from, how they
are inherited from the dataset, captured at ingest, carried through derivatives and
cross-dataset moves. The API that exposes it and the UI that edits it are in
`docs/dev/provenance-api.md`.

The vocabulary itself and the read/write helpers live in `backend/licenses.py` — see
`docs/dev/shared-utilities.md` for the one-line index of every function named here.
Export-side consumption (CREDITS.md, licenses.csv, the license filters) is in
`docs/dev/export-licensing.md`; the snapshot mirror is in `docs/dev/versioning/mirrors.md`.

## Model & schema

`Dataset` carries four NOT NULL defaults (`source_name`, `source_url`, `license`, `attribution`; `""` = unset). `Image` carries the same four as **nullable** columns plus `source_meta` (JSON — scraper long tail, `deferred=True` like the embedding blobs, so every reader that loads `Image` **as an ORM entity** must `undefer` it or a lazy load raises `MissingGreenlet` on the async session; those readers are `get_image`, the provenance PATCH, `create_snapshot`, and the four derivative paths that call `copy_provenance`). The rule is about entity loads only: a `select(Image.source_meta, …)` column list already has the value, which is why `batch_copy_dataset` and `duplicate_dataset` name it in their column tuples and add no `undefer`, and `batch_move_dataset` deliberately omits the column altogether (a move keeps the row's own `source_meta`). `VersionImageState` mirrors all five (see `docs/dev/versioning/mirrors.md` § Provenance). A third model carries them too: `Video` has the same four nullable columns and deliberately **no** `source_meta`, since nothing captures scraper sidecars for a video — see `docs/dev/video/storage.md` § The model.

There is deliberately **no index on `(dataset_id, license)`**: every license filter runs on the effective license, `COALESCE(NULLIF(images.license,''), datasets.license, '')`, which is not sargable against one, and `EXPLAIN QUERY PLAN` confirms SQLite never chose it. Migration `b5e8d2a7c9f4` drops the one the feature originally shipped with.

## Inheritance rule

A NULL/empty image field means *inherit the dataset default*, resolved at read time by `backend/licenses.py::resolve_provenance(img, ds)`. Editing a dataset default therefore retroactively changes every non-overridden image — intended, not a bug. `resolve_provenance` is duck-typed (works on ORM rows and `select(...)` row tuples) and must never import models, or it re-introduces an import cycle.

## Capture precedence

At ingest — the first source to supply a field wins, and anything still unset stays NULL:

1. request-supplied provenance — **folder import only** (`DatasetImportWithOptions` →
   `routers/datasets.py` → `import_images_from_folder(provenance=…)`). Direct upload
   (`POST /images/upload`, which takes only `dataset_id, subfolder, files`) and folder
   rescan have no request layer; they capture from the file alone.
2. scraper sidecar — `image_service.read_provenance_sidecar()`, which accepts both
   `{filename}.json` (`pic.png.json`, gallery-dl's default) and `{stem}.json`, in that
   order. Checking only one silently skips half the real scrape folders.
3. embedded attribution — `image_service.extract_embedded_provenance()`: EXIF Artist
   (315) / Copyright (33432) for JPEG/TIFF, plus the `Author`/`Artist`/`Copyright`/
   `Attribution` PNG tEXt chunks read from `img.info` (a PNG has no EXIF Artist tag at
   all, so an EXIF-only read captured nothing for the most common format here).

Which paths capture what: **folder import** gets all three layers; **rescan** gets
sidecar + embedded; **drag-and-drop upload** gets embedded only, because the browser
sends the image bytes and nothing else. Dragging a gallery-dl folder onto the gallery
therefore drops its sidecar provenance — use folder import for scrape folders.

`read_provenance_sidecar` accepts the `{filename}.json` layout unconditionally (the
name is unambiguous) but requires the `{stem}.json` layout to carry at least one
recognised provenance key, or a ComfyUI `workflow.json` beside `workflow.png`, a
project `metadata.json` beside `metadata.jpg`, or one `12345.json` claimed by both
`12345.png` and `12345.jpg` would be adopted as provenance. Sidecars over
`_SIDECAR_MAX_BYTES` (256 KB) are skipped rather than read into a DB column.

Both run in the existing executor hop (`_ingest_file_sync` / `_register_file_sync` in `dataset_service.py`), never on the event loop: the embedded read inside `image_service.decode_for_ingest`, which shares one open of the file with the size, pHash and thumbnail (`docs/dev/image-import.md` § Several files at once, one decode each), and the sidecar read in `_capture_provenance`, which layers it over that result. `extract_embedded_provenance` populates **only** `attribution` — a copyright string is not a license id, and inferring one would put unverified images in the commercial-use bucket. Sidecar `license` strings run through `normalize_license`, falling back to `other:<raw>` rather than being dropped. `_SIDECAR_KEYS["source_url"]` prefers `post_url`/`page_url` over `file_url`: a direct file URL is routinely a signed, rotating CDN blob that is dead by the time anyone reads CREDITS.md.

**Capture truncates; the API rejects.** `licenses.merge_provenance(*layers)` is the left-wins merge every ingest path funnels through, and it ends in `clamp_provenance`, so an over-long scraped value is cut to its column width instead of failing the import. Its `fields=` argument narrows the column set for a model that lacks `source_meta` — video ingest passes `PROVENANCE_FIELDS`. The edit endpoints (`docs/dev/provenance-api.md` § API surface) do the opposite and 422 — an API client must never silently lose data. Getting this backwards is what made an image's provenance permanently unsaveable: an uncapped 500-char `rights` value stored a 506-char license, which then failed the response schema on every save.

## Derived-image rule

Crop, upscale, LUT, detection-crop and **video frame extraction** all copy the parent's provenance verbatim via `licenses.copy_provenance(...)` — a derivative of a CC-BY-SA image is still CC-BY-SA. Frame extraction is the fifth consumer and the only one whose parent is not an `Image`: it calls `copy_provenance(video)` once per run and splats the result onto every frame it writes, which makes it the rights-carrying path for the whole video feature (`docs/dev/video/extract.md`). A `Video` has no `source_meta`, so the `getattr` default correctly yields `None` and no `undefer` is possible or needed there. Raw (not resolved), because the derivative stays in the same dataset and should keep tracking that default. Each of the four *image* loaders must `undefer(Image.source_meta)` — and `copy_provenance` deep-copies that dict, so parent, derivative and every snapshot of either do not share one mutable JSON object. The upscale and LUT loop (`services/image_edit_job.py`) applies the `undefer` **only on the non-replace branch**: the replace branch never calls `copy_provenance`, so undeferring there would load the full scraper payload for the whole batch and discard it.

## ComfyUI synthetic stamping

ComfyUI import reads the **plan's** `output_is_synthetic` flag (`_comfy_output_provenance(synthetic, plan_name)` in `routers/comfy.py`), not the dataset. Nothing at run time can tell a text2img plan from one derived from licensed source material, and one dataset holds both kinds — inferring it from the dataset's defaults (the earlier design) credited a real photographer for generated images on any labelled dataset.

`ComfyPlan.output_is_synthetic` (bool, `NOT NULL DEFAULT true`, migration `c3f7a9e1d4b6`) is the plan's own declaration that its output is self-created. True stamps **three** of the four fields, all concrete: `license="synthetic"`, `source_name="ComfyUI"` and `attribution='Generated by ComfyUI plan "<name>"'` (plain `"Generated by ComfyUI"` when the plan name is blank, clamped to `FIELD_MAX_LEN["attribution"]`) — concrete because a blank attribution reads as "inherit" (`resolve_provenance` treats `""` and NULL alike), which credited a real photographer for an AI image in `CREDITS.md`. False returns `{}` so all four fields inherit **together** — never a partial stamp, which would mix a "synthetic" license onto an inherited real credit and hide a CC-BY-NC source from the commercial-use filter. Documented residual: `source_url` is the fourth field and is **never** stamped, so it inherits under either setting — every candidate value would be invented, and there is no honest one to write; the helper's docstring shows the manifest that produces.

`source_meta` is written **whether or not** the plan is synthetic — it records origin, not rights. It comes from `_comfy_source_meta(plan_row, row, wf)` — built from `wf`, the graph actually submitted, not the plan template, and computed **once per row** next to `wf` (deep-copied per image so multi-output rows never share one JSON dict). It records plan/row identity plus the model; `_workflow_checkpoints` reads `ckpt_name` / `unet_name` / `model_name` so Flux/SD3 `UNETLoader` graphs record a model too. See `docs/dev/comfyui/run.md`.

## Cross-dataset copy/move

`batch_move_dataset` and `batch_copy_dataset` call `licenses.materialize_by_source(rows, source_datasets)` — inherited values are written out concretely against the *source* dataset, or the image silently re-inherits the destination's unrelated default. **Per row, against its own source**: a selection can span datasets (that is what the toolbar's `datasetBreakdown` badges exist to show), so both routers derive `source_dataset_ids = {row.dataset_id for row in rows}`, fetch all of them, and drive the busy guard, the materialization and the closing `refresh_stats` off that set — never off `rows[0]`. Materialized values are concrete, so resolving them all against one dataset writes an unrelated license in permanently. `duplicate_dataset` is the exception: it copies the four dataset defaults onto the new dataset, so raw `copy_provenance` keeps inheritance equivalent. That covers `Video`'s four provenance columns too — under `include_videos` a copied video carries `source_name`/`source_url`/`license`/`attribution` raw for exactly the same reason (`Video` has no `source_meta`).

Move and copy **differ deliberately on `source_meta`**: `batch_move_dataset` strips it from the materialized values (a move does not change where the image came from, and the row keeps its own `source_meta` untouched), while `batch_copy_dataset` carries it onto the new row (which has none of its own). Not an oversight — don't "fix" one to match the other.

## Tests

This section covers both halves, including `docs/dev/provenance-api.md`. `backend/tests/test_provenance.py` covers inheritance direction, snapshot restore, cross-dataset materialization, sidecar shapes, export manifests, the `license_filter` encoding, the length caps, and the `other:` semantics — plus `backend/tests/test_provenance_http.py`, which drives the same behaviour through the real routers over `httpx.ASGITransport` (see `backend/tests/conftest.py`). Router-shaped failures — the ComfyUI import crash, the uneditable over-long license — are invisible to service-level tests, which is why the harness exists.
