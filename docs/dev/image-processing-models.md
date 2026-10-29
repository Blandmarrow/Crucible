# Image processing models: upscaling, LUT grading & LaMa inpainting

This file covers the three image-*processing* pipelines that rewrite pixels through a
loaded model or table: ML upscaling through `spandrel` (`backend/ml/upscaler.py`,
`backend/routers/upscaling.py`), 3D LUT color grading (`backend/ml/lut_processor.py`,
`backend/routers/lut.py`) and LaMa inpainting (`backend/ml/lama_inpainter.py`, the model
half of `backend/routers/inpaint.py`). Upscaling and LUT grading offer a replace/new-file
pair and are governed by `image_service._open_safe` rather than by
`backend/ml/image_utils.py::open_rgb`; LaMa is the exception and opens through `open_rgb`,
because its mask comes from detection polygons in the EXIF-transposed frame.

The loading, VRAM accounting and device abstraction these lean on are
`docs/dev/ml-models.md`, eviction and leases are `docs/dev/model-residency.md`, and the
registry entries are `docs/dev/model-registry.md`. The bulk jobs that share their loop
shape — batch resize/crop, crop-to-detection and the `batch_inpaint` job that drives LaMa —
are `docs/dev/bulk-image-jobs.md`, and the user-facing side of all three pipelines is
`docs/editing.md`.

## Upscaling

ML-based image upscaling via the `spandrel` library, which auto-detects architecture from `.pth`/`.safetensors` files (RealESRGAN/RRDB, SwinIR, HAT, OmniSR, and more).

**Router**: `backend/routers/upscaling.py`, prefix `/upscaling`.

| Endpoint | Body / params | Returns |
|---|---|---|
| `GET /upscaling/models` | — | `list[UpscaleModelInfo]` — scans `settings.upscale_models_dir` |
| `POST /upscaling/run` | `UpscaleRunRequest` | `{ job_id, total }` |

**Config**: `settings.upscale_models_dir` (default `models/upscale_models/`). Override with `UPSCALE_MODELS_DIR=` in `.env` (e.g. pointing at a ComfyUI models folder). The directory is created automatically on startup.

**`UpscaleRunRequest`** fields: `dataset_id`, `image_ids` (null = whole dataset), `model_path`, `replace` (overwrite source vs. new file), `target_width`/`target_height` (optional: upscale then resize down to fit, maintaining AR), `subfolder` (null = all; applied only when `image_ids` is null), `quality_flags` (null = no filter; when set, excludes images where any of the listed flags is `True`; applied only when `image_ids` is null).

**ML inference** (`backend/ml/upscaler.py`):
- `scan_upscale_models(dir)` — globs `*.pth`/`*.safetensors`, detects scale from filename heuristics, returns `[{name, path, scale}]` without loading weights. `_detect_scale` accepts `1`–`8` (scale 1 being a restoration model — denoise, deblur, JPEG artifacts — which spandrel loads and runs like any other) in either order, `4x…` or `x4…`, case-insensitively, and needs a separator or string start *before* the token but not after it, so the openmodeldb convention (`4xNomos8kSCHAT-L`, `1xDeJPG_SRFormer_light`, `RealESRGAN_x4plus`) is read correctly. That leading boundary is strict on purpose: it is what keeps `Box4`, `my1xmodel` and `Model_v1x` from matching, at the accepted cost of `HAT-L_SRx4_ImageNet-pretrain` returning `None`. A second rule does the rest: a `(?![0-9])` lookahead means the digit must not be followed by another one, so `12x_upscaler` and `model_x16_something` are `None` rather than a confidently wrong 2 and 1. A miss is cosmetic — the real factor comes from `descriptor.scale`, so only the dropdown badge and the New-file suffix are affected. Covered by `backend/tests/test_detect_scale.py`, which is torch-free and runs in CI.
- `upscale_image_sync(src, dest, model_path, replace, target_w, target_h)` — loads via `spandrel.ModelLoader().load_from_file()`, tiles if either dimension exceeds 512 px (512 px tiles, 64 px overlap, linear-ramp seam blending), optional LANCZOS resize post-upscale. Returns `{width, height, file_size_bytes, format, out_path}`. As with `apply_lut_sync`, `out_path` may differ from the path asked for when the source format is unsupported and falls back to PNG; every caller reads `info["out_path"]` (below).
- Model caching uses `model_manager._registry` under ID `upscale:{abs_path}`; `_ensure_upscaler_loaded` includes a double-check after re-acquiring `_sync_lock` to prevent the TOCTOU double-load race.
- `_ensure_upscaler_loaded` hands its entry back **already leased** on all three paths (cache hit, fresh insert, another thread won the load), taking the lease via `model_manager._acquire_locked` inside the same `_sync_lock` block as the lookup or insert; `upscale_image_sync` releases it with `_release_lease` in a `finally` around inference. Per image, not per job: a miss simply reloads, and the HTTP crop+upscale routes call it concurrently with the job worker. See `docs/dev/model-residency.md` § Leases.

**Output modes**: *New file* — filename `{stem}_up{N}x{ext}` (`{stem}_1x{ext}` when the detected scale is 1, `{stem}_upscale{ext}` when `_detect_scale` returned `None`), where `{ext}` is the extension that will actually be **written** (the PNG fallback below, not necessarily the source's), collision-handled via `unique_filename_with_thumb`; new `Image` record created, thumbnail generated. *Replace* — updates `width`/`height`/`file_size_bytes`/`updated_at`/`processing_history` on the existing record, plus `filename`/`file_path`/`format` when the fallback moved the path; thumbnail regenerated from the written file. Replace mode calls `protect_file_before_overwrite` before overwriting the file — see `docs/dev/versioning.md` for the copy-on-write mechanism.

**Replace mode follows the PNG fallback — at all three call sites.** The twin of the LUT paragraph below, and the reason PM-009 recurred: `upscale_image_sync` ran the identical correction but did not return `out_path`, so its callers were structurally unable to follow it. All three now read `info.get("out_path", …)` — `routers/upscaling.py` (both modes), and `routers/images.py`'s two crop+upscale workers, whose `_croptmp` source carries the original suffix and hits the same fallback. Replace-shaped paths update `filename`/`file_path`/`format`, commit, and only then unlink the original and regenerate the thumbnail — both best-effort, neither allowed to fail the image (`docs/dev/postmortems/PM-013-fs-mutation-before-the-commit.md`). New-file paths take the row's name *and* its thumbnail from the written path. The pre-write collision guard differs by shape: the batch jobs log, emit progress and skip the image, while `POST /images/{id}/crop` handles one image and answers **409** before touching anything (see `docs/dev/image-detail.md`). Copy mode reserves its name under the written extension, so `unique_filename_with_thumb`'s on-disk check applies to the path that will exist. Tests: `backend/tests/test_upscale_png_fallback_http.py`.

**Copy mode's occupied-stem sets are keyed by thumbnail directory.** Both `routers/upscaling.py` and `routers/lut.py` carry the `occupied_by_dir` / `planned_by_dir` dicts that `routers/detection.py` already had, built lazily per dir *inside* the loop. Neither job constrains its selection by dataset — `Image.id.in_(...)` with a bare id list — while `dest_images` is chosen per image, so the single flat set each of them built from `images[0]`'s `thumbnails/` dir false-shared stems across datasets and let a derived `.webp` overwrite a live sibling's in whichever dataset did not seed the set (PM-007's recorded recurrence, fixed 2026-07-31). `db_names` was already per image in both, which is why only the *thumbnail* was clobbered while the filename looked correct. The general rule: when a guard is built once before a loop but the thing it guards is chosen inside it, something has to make them agree. Tests: the two `..._copy_across_two_datasets_does_not_share_one_thumbnail_stem_set` cases in `test_upscale_png_fallback_http.py` and `test_lut_replace_extension_http.py`.

**The run reports what it did.** `batch_upscale` writes `result_data = {processed, skipped, failed, thumbnails_stale}` — seeded with the rows that vanished between enqueue and run, and committed above `raise_if_cancelled` so a cancelled run keeps its counts. Without it both `continue` branches were invisible and a run that failed on 40 of 50 images still toasted a flat "Upscaling complete". `thumbnails_stale` is the epilogue's own failure: the image is committed and serves, but the gallery tile is the old one. `TopBar` owns the warning and the repair pointer for all four such jobs — see `docs/dev/frontend-jobs.md` § The stale-thumbnail warning.

**History management**: Non-replace upscale navigation uses `{ replace: true }` so the source image's history entry is overwritten rather than stacked, leaving a single clean entry so one Back press returns to the gallery. Do not remove the `replace: true` from these `paneGo` calls without considering the double-Back regression. See `docs/dev/gallery/nav.md` § Newly created images: injectNavId for `injectNavId`/`paneGo` mechanics.

**Frontend surfaces**: all three render each model's option text through `upscaleModelLabel` in `frontend/src/api/upscaling.ts` — `name (4×)`, `name (1× restore)` for a restoration model, bare `name` when the heuristic found no scale. It exists to keep that expression in one place; a fourth surface reuses it rather than re-inlining it.
- `ImageDetailPage` — "Upscale" toolbar button toggles inline controls (model select, Replace checkbox, optional W×H). Uses `upscalingApi.run()` with `image_ids: [imageId]`.
- `SelectionToolbar` — "Upscale" button opens a modal with `<UpscaleForm>`.
- `BulkEditPage` — "Upscale" tab (see `docs/dev/bulk-ops.md`).

`UpscaleForm` (`frontend/src/components/upscale/UpscaleForm.tsx`) — reusable form used by `SelectionToolbar` and `BulkEditPage`. Queries `["upscale-models"]` with `staleTime: Infinity` (model list never changes at runtime).

## LUT Color Grading

Applies `.cube` and `.3dl` 3D color look-up tables to images with a user-controlled blend intensity.

**Router**: `backend/routers/lut.py`, prefix `/lut`.

| Endpoint | Body / params | Returns |
|---|---|---|
| `GET /lut/models` | — | `list[LutModelInfo]` — scans `settings.lut_models_dir` |
| `POST /lut/run` | `LutRunRequest` | `{ job_id, total }` |

**Config**: `settings.lut_models_dir` (default `models/lut/`). The directory is created automatically on startup.

**`LutRunRequest`** fields: `dataset_id`, `image_ids` (null = whole dataset), `lut_path`, `intensity` (0.0–1.0, clamped by validator), `replace` (overwrite source vs. new file), `subfolder` (null = all; applied only when `image_ids` is null), `quality_flags` (null = no filter; when set, excludes images where any of the listed flags is `True`; applied only when `image_ids` is null).

**ML processing** (`backend/ml/lut_processor.py`):
- `scan_lut_models(dir)` — globs `*.cube`/`*.3dl`, returns `[{name, path, format}]`.
- `apply_lut_sync(src, dest, lut_path, intensity, replace)` — loads PIL image + `exif_transpose`, converts to float32 [0,1], applies trilinear LUT interpolation, blends `original * (1-intensity) + graded * intensity`, saves. Returns `{width, height, file_size_bytes, format, out_path}`. Note `out_path` may differ from `dest` when the source format is unsupported and falls back to PNG — the router uses `info["out_path"]` to derive the actual output path.
- Module-level `_lut_cache: dict[str, np.ndarray]` — parsed LUT arrays are cached for the process lifetime. LUTs are tiny (<1 MB each); no eviction needed in practice.

**LUT axis-ordering invariant**: The `.cube` spec (and `.3dl`) stores data with **R varying fastest, B slowest**. After `reshape(N, N, N, 3)` numpy's axis order is `[B, G, R]`. Both `_parse_cube` and `_parse_3dl` therefore call `.transpose(2, 1, 0, 3)` to produce a `[R, G, B]`-indexed array, so that `lut[r, g, b]` is the natural lookup in `_apply_lut_array`. Do not remove this transpose — without it R and B are swapped in the lookup, producing visually wrong results.

**Output modes**: *New file* — filename `{stem}_lut{ext}`, `{ext}` being the extension that will actually be written (collision-handled via `unique_filename_with_thumb`), new `Image` record created, thumbnail generated. *Replace* — updates `file_size_bytes`/`updated_at`/`processing_history` on existing record, thumbnail regenerated. Replace mode calls `protect_file_before_overwrite` before overwriting the file — see `docs/dev/versioning.md` for the copy-on-write mechanism.

**Replace mode follows the PNG fallback.** `normalize_image_format` falls back to PNG for `.gif`/`.bmp`/`.tiff`/`.avif`, all of which `media_types.IMAGE_EXTENSIONS` accepts, so a replace-mode grade of one of those writes a *different file* from the one it read. The router updates `filename`/`file_path`/`format` to match `info["out_path"]` and commits before unlinking the original or regenerating the thumbnail (PM-013 — both are fallible, and a raise between the write and the commit rolled the row back onto a file that no longer existed); without the reassignment the row kept pointing at the stale source, which was also left on disk. A pure extension change moves nothing derived — the stem is unchanged, so the thumbnail and the `.txt` sidecar stay put — but the collision guard runs **before** the save, not after: an unregistered file at the fallback path has no DB row guarding it and is already gone by the time `apply_lut_sync` returns, so such an image is skipped with a message instead. Copy mode reserves its name under the written extension for the same reason — `unique_filename` only stats the suffix it is handed, so reserving `{stem}_lut.bmp` would leave an existing `{stem}_lut.png` to be overwritten. Upscaling is the same story at three more call sites (§ Upscaling above), and pass-2 video re-extraction does the swap deliberately; see `docs/dev/video/reextract.md` § The extension change. `batch_lut` carries the same `{processed, skipped, failed, thumbnails_stale}` `result_data` as its upscale twin, with the same seeding and the same placement above `raise_if_cancelled`.

**Frontend surfaces**:
- `ImageDetailPage` — "LUT" toolbar button (mutually exclusive with Crop and Upscale) toggles inline controls: LUT `<select>`, intensity slider, Replace checkbox, Run button. Non-replace completion calls `injectNavId` + `paneGo` to navigate to the new image (same pattern as upscaling). See `docs/dev/gallery/nav.md` § Newly created images: injectNavId for `injectNavId`/`paneGo` mechanics.
- `SelectionToolbar` — "LUT" button opens a modal with `<LutForm>`.
- `BulkEditPage` — "Apply LUT" tab (see `docs/dev/bulk-ops.md`).

`LutForm` (`frontend/src/components/lut/LutForm.tsx`) — reusable form used by `SelectionToolbar` and `BulkEditPage`. Queries `["lut-models"]` with `staleTime: Infinity`. On job completion invalidates `["images", datasetId]` and calls `onSuccess?.()`.

## LaMa inpainting

`lama` is the registry's one `kind: "edit"` entry (the registry is `docs/dev/model-registry.md`) — a model that rewrites pixels rather than describing them. None of `caption`/`score`/`embed`/`detect` is honest for an inpainter, and a new *value* costs nothing: `kind == "caption"` in `routers/captioning.py::list_models` is its only behavioural reader, so every other value is inert there. Do not read that as licence to generalise `kind` into a capability *set* — that still needs a second reader. `backend/tests/test_captioning_model_registry.py` partitions the registry in both directions, so a fourteenth model lands in neither set and fails there.

**TorchScript, not a package and not a state_dict, and the reasons are all environmental.** `simple-lama-inpainting` pins `Pillow>=9.5,<10` and `numpy<2.0` against this repo's Pillow 12 / numpy 2; `IOPaint` pins `fastapi==0.108.0`, `Pillow==9.5.0` and gradio. Installing either would wreck the venv. The HuggingFace `anyisalin/big-lama` mirror is a 473 MB bare state_dict that would require vendoring LaMa's FFC ResNet generator — ~400 lines this repo would then own forever. The TorchScript archive carries its own architecture, so `torch.jit.load` plus `lama_inpainter`'s ~60 lines of pre/post-processing is the entire inference path, which is exactly what `simple-lama` does internally. **No new pip dependency was added.**

**The weights are a pickle, and the SHA-256 pin is what makes that acceptable.** `_WEIGHTS_SHA256` is checked after every download *and* on every load of a cached file; a mismatch deletes the file and refuses, rather than warning. The SAM 3 loader's "safetensors only, never `.pt`" rule is stated in `sam3_predictor.py`'s own docstring and is SAM-3-specific, not repo-wide — `scan_upscale_models` already globs and loads arbitrary `*.pth` through spandrel with no verification at all, so a hash-pinned archive is strictly the more verified of the two. LaMa's code is Apache-2.0 (`advimman/lama`); the weights are Places2-trained and distributed through community mirrors.

**`_ensure_weights` is a plain GET, so `progress_tqdm_patch` does not apply.** That helper monkeypatches `huggingface_hub`'s tqdm and there is no hub call here; the byte loop calls `emit_sync(job_id, loop, msg, pct, dataset_id)` directly instead, roughly every 4 MB. It downloads to a `.part` file and `replace()`s only after the hash verifies, so an interrupted first run leaves nothing a later load would trust. `_load_lama_sync` resolves and verifies the weights **before** touching the accelerator — fail fast, no VRAM spent — and carries the same failure tail every `model_manager` loader does: `.cpu()` → `del` → `_device.empty_cache()` → `raise`. `vram_mb` is `max(2000, measured delta)`.

**`torch` is imported inside the functions, never at module scope**, for the same reason `device.py` does it: `routers/inpaint.py` imports `inpaint_image_sync` at module import and `main.py` imports that router, so a module-level torch import would put torch on the `from backend.main import app` path and take down collection of the whole backend suite in CI. `backend/tests/test_inpaint_http.py` runs torch-free because of it.

**Inference is crop-and-composite, not whole-image.** `inpaint_image_sync` takes the mask's bounding box, expands it generously, snaps the span to a multiple of 8 (LaMa's stride) and runs the model on that crop alone, downscaling above 1536 px. This bounds VRAM independently of the source resolution — a fully-convolutional model on a 4K image otherwise blows up — and improves the fill, since LaMa was trained at 512². The painted crop is then composited back **through the mask**, so every pixel outside it is bit-identical to the source rather than making a round trip through the model. The image opens through `image_utils.open_rgb`, never a bare `Image.open`: detection polygons are normalized against the EXIF-transposed frame, so any other open misplaces the mask.

**One OOM retry, at `_OOM_RETRY_SIDE = 768`.** The resize → tensor → `model(...)` → array block is a local `_run_at(side)` so the retry can redo *all* of it: the resized copies and the tensors are both what the allocation failed on, and re-running only the model call would ask for the same VRAM again. The first call is at `_CROP_MAX_SIDE`; on `_device.is_oom_error(exc)` it calls `_device.empty_cache()` (the retry needs the failed attempt's fragments released) and tries once more at a quarter of the area, which loses fill detail and beats failing the image. A second OOM raises a `RuntimeError` naming both sizes and the crop, and suggesting a narrower detection. The catch pattern is `florence_captioner.infer_sync`'s — MPS raises a plain `RuntimeError`, so anything that is not an OOM re-raises untouched. Untested for the same reason Florence-2's is: it needs a GPU it can actually exhaust. `_run_at` closes its own resized copies per the "Close PIL Images after preprocessing" invariant; `crop_rgb`/`crop_mask` outlive it because the composite — and a retry — still need them.

**It returns `phash`, unlike `upscale_image_sync`.** Inpainting changes pixels without changing geometry, and `phash` is the only thing duplicate detection keys on, so a stale one is the entire failure mode. Follow `crop_image_to_dest`, not the upscaler. The `normalize_image_format` PNG fallback applies here as everywhere: `out_path` is the path actually written and every caller must follow it (PM-009).
