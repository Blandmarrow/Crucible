# Image processing models: upscaling, LUT grading & LaMa inpainting

This file covers the three image-*processing* pipelines that rewrite pixels through a
loaded model or table: ML upscaling through `spandrel` (`backend/ml/upscaler.py`,
`backend/routers/upscaling.py`), 3D LUT color grading (`backend/ml/lut_processor.py`,
`backend/routers/lut.py`) and LaMa inpainting (`backend/ml/lama_inpainter.py`, the model
half of `backend/routers/inpaint.py`). All three offer a replace/new-file pair. Upscale and LUT
run one shared job loop, `backend/services/image_edit_job.py`, documented in
`docs/dev/image-edit-job.md` (output modes, the PNG fallback, the path gate and the run's outcome
hold for LUT too). Upscaling and LUT
grading are governed by `image_service._open_safe` rather than by
`backend/ml/image_utils.py::open_rgb`; LaMa opens through `open_rgb` instead, because its
mask comes from detection polygons in the EXIF-transposed frame.

The loading, VRAM accounting and device abstraction these lean on are
`docs/dev/ml-models.md`, eviction and leases are `docs/dev/model-residency.md`, and the
registry entries are `docs/dev/model-registry.md`. The bulk jobs that share their loop
shape are batch resize/crop (`docs/dev/bulk-image-jobs.md`), crop-to-detection
(`docs/dev/detection-crop.md`) and the `batch_inpaint` job that drives LaMa
(`docs/dev/inpaint.md`), and the user-facing side of all three pipelines is
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
- `upscale_image_sync(src, dest, model_path, replace, target_w, target_h)` — loads via `spandrel.ModelLoader().load_from_file()`, tiles if either dimension exceeds 512 px (512 px tiles, 64 px overlap, linear-ramp seam blending), optional LANCZOS resize post-upscale. Returns `{width, height, file_size_bytes, format, out_path}`. As with `apply_lut_sync`, `out_path` may differ from the path asked for when the source format is unsupported and falls back to PNG; every caller reads `info["out_path"]` (`docs/dev/image-edit-job.md` § Replace mode follows the PNG fallback).
- Model caching uses `model_manager._registry` under ID `upscale:{abs_path}`; `_ensure_upscaler_loaded` includes a double-check after re-acquiring `_sync_lock` to prevent the TOCTOU double-load race.
- `_ensure_upscaler_loaded` hands its entry back **already leased** on all three paths (cache hit, fresh insert, another thread won the load), taking the lease via `model_manager._acquire_locked` inside the same `_sync_lock` block as the lookup or insert; `upscale_image_sync` releases it with `_release_lease` in a `finally` around inference. Per image, not per job: a miss simply reloads. Every caller (the upscale job and both crop+upscale jobs) runs it on the single GPU thread, so the "another thread won the load" path no longer occurs in practice, and is kept as a defence. See `docs/dev/model-residency.md` § Leases and `docs/dev/ml-models.md` § One GPU thread.

**History management**: Non-replace upscale navigation uses `{ replace: true }` so the source image's history entry is overwritten rather than stacked, leaving a single clean entry so one Back press returns to the gallery. Do not remove the `replace: true` from these `paneGo` calls without considering the double-Back regression. See `docs/dev/gallery/nav.md` § Newly created images: injectNavId for `injectNavId`/`paneGo` mechanics.

**Frontend surfaces**: all three render each model's option text through `upscaleModelLabel` in `frontend/src/api/upscaling.ts` — `name (4×)`, `name (1× restore)` for a restoration model, bare `name` when the heuristic found no scale. It exists to keep that expression in one place; a fourth surface reuses it rather than re-inlining it.
- `ImageDetailPage` — "Upscale" toolbar button toggles inline controls (model select, Replace checkbox, optional W×H) and calls `upscalingApi.run()` with `image_ids: [imageId]`. How it reads the run's outcome, and why the status alone is not enough, is `docs/dev/image-edit-job.md` § The detail page's outcome read.
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
- `apply_lut_sync(src, dest, lut_path, intensity, replace)` — loads PIL image + `exif_transpose`, converts to float32 [0,1], applies trilinear LUT interpolation, blends `original * (1-intensity) + graded * intensity`, saves. Returns `{width, height, file_size_bytes, format, out_path}`. Note `out_path` may differ from `dest` when the source format is unsupported and falls back to PNG — the shared loop uses `info["out_path"]` to derive the actual output path (`docs/dev/image-edit-job.md`).
- Module-level `_lut_cache: dict[str, np.ndarray]` — parsed LUT arrays are cached for the process lifetime. LUTs are tiny (<1 MB each); no eviction needed in practice.

**LUT axis-ordering invariant**: The `.cube` spec (and `.3dl`) stores data with **R varying fastest, B slowest**. After `reshape(N, N, N, 3)` numpy's axis order is `[B, G, R]`. Both `_parse_cube` and `_parse_3dl` therefore call `.transpose(2, 1, 0, 3)` to produce a `[R, G, B]`-indexed array, so that `lut[r, g, b]` is the natural lookup in `_apply_lut_array`. Do not remove this transpose — without it R and B are swapped in the lookup, producing visually wrong results.

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

**`torch` is imported inside the functions, never at module scope**, for the same reason `device.py` does it (`docs/dev/ml-models.md` § ML device abstraction): `routers/inpaint.py` imports `inpaint_image_sync` at module import and `main.py` imports that router, so a module-level torch import would put torch on the `from backend.main import app` path and take down collection of the whole backend suite in CI. `backend/tests/test_inpaint_http.py` runs torch-free because of it.

**Inference is crop-and-composite, not whole-image.** `inpaint_image_sync` takes the mask's bounding box, expands it generously, snaps the span to a multiple of 8 (LaMa's stride) and runs the model on that crop alone, downscaling above 1536 px. This bounds VRAM independently of the source resolution — a fully-convolutional model on a 4K image otherwise blows up — and improves the fill, since LaMa was trained at 512². The painted crop is then composited back **through the mask**, so every pixel outside it is bit-identical to the source rather than making a round trip through the model. The image opens through `image_utils.open_rgb`, never a bare `Image.open`: detection polygons are normalized against the EXIF-transposed frame, so any other open misplaces the mask.

**One OOM retry, at `_OOM_RETRY_SIDE = 768`.** The resize → tensor → `model(...)` → array block is a local `_run_at(side)` so the retry can redo *all* of it: the resized copies and the tensors are both what the allocation failed on, and re-running only the model call would ask for the same VRAM again. The first call is at `_CROP_MAX_SIDE`; on `_device.is_oom_error(exc)` it calls `_device.empty_cache()` (the retry needs the failed attempt's fragments released) and tries once more at a quarter of the area, which loses fill detail and beats failing the image. A second OOM raises a `RuntimeError` naming both sizes and the crop, and suggesting a narrower detection. The catch pattern is `florence_captioner.infer_sync`'s — MPS raises a plain `RuntimeError`, so anything that is not an OOM re-raises untouched. Untested for the same reason Florence-2's is: it needs a GPU it can actually exhaust. `_run_at` closes its own resized copies per the "Close PIL Images after preprocessing" invariant; `crop_rgb`/`crop_mask` outlive it because the composite — and a retry — still need them.

**It returns `phash`, as every pixel-writing worker does.** Inpainting changes pixels without changing geometry, and `phash` is the only thing duplicate detection keys on, so a stale one is the entire failure mode. `upscale_image_sync` and `apply_lut_sync` return it too (since 2026-11-12; before, an upscale or LUT replace kept the old hash and a copy row had none, so it never entered a duplicate scan). Each hashes its output **in memory, before the save**, as `image_service._save_measured` does, and every caller stores it with a strict `info["phash"]`. The `normalize_image_format` PNG fallback applies here as everywhere: `out_path` is the path actually written and every caller must follow it (PM-009).
