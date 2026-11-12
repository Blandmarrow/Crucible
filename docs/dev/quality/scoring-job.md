# Quality scoring job — how a run is chunked, resumed and unloaded

This file covers how the `quality_score` job runs: the technical scorer's concurrent loop, the GPU checks' prefetch, the chunked job loop with its resume path and progress, and the model lifecycle with its unload endpoints. What each scorer measures, the columns it writes and the failure contract are `docs/dev/quality/scoring.md`. Model loading and eviction are `docs/dev/ml-models.md`; leases are `docs/dev/model-residency.md`. Part of the Quality family, routed from `docs/dev/quality.md`.

## The technical scorer runs several images at once

`score_images_technical` keeps up to `_technical_workers()` calls of `score_technical_sync` in flight on asyncio's default pool: half the logical cores, capped at `TECHNICAL_MAX_WORKERS` (4), and never below 1. OpenCV and numpy release the GIL, so threads are enough, and no GPU code is involved, so PM-035's one-GPU-thread rule does not apply. This is M10's T1 (`docs/dev/throughput-plan/README.md`).

**It answers exactly like the serial loop it replaced.** The futures sit in a FIFO window and are awaited oldest first, which gives four guarantees with no reordering step: results in input order; a **prefix** of the input on a cancel, which `routers/quality.py` relies on when it writes `c_ids[:len(values)]`; `_unmeasured()` at the index of a failed image only; and progress after images 1, 11, 21, as before. A cancel stops new submissions at once, then lets the calls already in flight finish and keeps their results, since a pool thread cannot be stopped anyway. So a cancel lets at most four more images finish than it used to. The `run_in_executor` call must stay in `score_images_technical`'s own body: Rule A of `test_gpu_executor_guard.py` exempts it by its *innermost* enclosing function, so moving it into a nested helper fails the guard. `backend/tests/test_technical_scorer_parallel.py` holds all of this, including exact parity with the serial path on a varied fixture.

**Why the cap is 4.** Timed on 2026-11-12 on 32 logical cores, with each bound in its own process:

| Bound | 2,000 JPEGs, 1024×768 | 100 PNGs, 4096×4096 | Peak memory, PNGs |
|---|---|---|---|
| 1 (the old serial loop) | 66.5 images/s | 2.2 images/s | 0.6 GB |
| 4 | 152 images/s (2.3×) | 4.5 images/s (2.0×) | 2.0 GB |
| 8 | 156 images/s (2.35×) | 5.5 images/s (2.4×) | 4.1 GB |
| 12 and 16 | slower | 5.5–5.6 images/s | 6.0–7.9 GB |

Each call holds float32 copies of its image, about 0.5 GB for a 4096² file, so memory grows linearly with the bound while speed flattens early: OpenCV's own thread pool is already busy inside each call. 8 doubles the memory for 20% on large files only, which is not worth it on a machine that is also running ComfyUI. `cv2.setNumThreads` is left alone because it is process-wide and would change the video and SAM2 paths too.

## The GPU checks prepare images ahead

The six GPU checks — aesthetic (LAION or V2.5), watermark, CLIP embeddings, DINOv2, its layer pass, and NSFW — each split their per-image work in two (M10 T5). The **`prepare_*` half** opens the file with `open_rgb`, runs the model's preprocess and closes the image, returning a CPU tensor. The **`infer_*` half** moves that tensor to the card and runs the forward pass. `backend/ml/prefetch.py::run_prefetched` runs the prepare halves on asyncio's default pool, up to `_prefetch_workers()` images at once (half the cores, capped at `PREFETCH_MAX_WORKERS`, 8), while the GPU thread runs the infer half of the oldest one. T4 found decode plus preprocess to be 63–93% of each check on real images, all of it time the card spent waiting (`docs/dev/throughput-plan/README.md` § T4).

| Module | Prepare | Infer |
|---|---|---|
| `aesthetic_scorer.py` | `prepare_clip`, shared by three checks (and `prepare_clip_bytes` for `embed-references`) | `infer_aesthetic`, `infer_watermark`, `infer_clip_embedding` |
| `aesthetic_v2_5_scorer.py` | `prepare_v2_5` | `infer_v2_5`, which reads the model's dtype for the cast |
| `dino_scorer.py` | `prepare_dino`, shared by both passes | `infer_dino`, `infer_dino_layers` |
| `nsfw_scorer.py` | `prepare_nsfw` | `infer_nsfw` |

The old `*_sync(path, entry)` functions remain as `infer(prepare(...))`, for `POST /quality/embed-references` and the bench script's parity check.

**The scores cannot move.** The ops are the serial path's, in the same order, one image per forward pass (decision 14 rules out batching). A prepare half takes only the transform or processor, never the model entry, so it cannot reach the model or the device. `backend/tests/test_prefetch_halves.py` holds that structurally; `docs/dev/ml-models.md` § One GPU thread says why it matters.

**It answers exactly like the serial loops it replaced**, which `backend/tests/test_prefetch.py` pins with fake halves:

- Results come back in input order, because the GPU always takes the oldest prepared image.
- A cancel returns a prefix. `cancel_requested` is checked before every top-up and before every GPU call, and images already prepared are dropped, unlike T1, which keeps its in-flight work.
- A failure in either half is None at that index only, logged with its traceback on the scorer's own logger. Each loop maps None to its own failure value (`docs/dev/quality/scoring.md` § The failure contract). This also gave the CLIP loop, which used to fail silently, and the DINOv2 loops, which logged no traceback, the same logging as the rest.
- Progress fires after images 1, 11, 21, with the same messages.

The window counts the image the GPU is about to take, so while a forward pass runs, up to seven more images are being prepared behind it. On a 4–5 core machine the bound is 2, one image of lookahead, and below that it is 1, which is the serial loop. Each holds a decoded buffer while it is prepared and a small tensor after.

**Checked on a real card on 2026-11-12** (32 GB, 32 logical cores, Windows), with all seven checks run through the real scoring loops, once from a `main` worktree and once from the branch:

- **The scores did not move.** Every output — the floats, and the SHA-256 of every embedding — matched `main` bit for bit, on a real ComfyUI dataset of 1,876 images (mostly PNGs around 2000 px) at 4 and at 8 workers, and on 30 synthetic 4096² PNGs. The bench script's parity guard passed on every check as well.
- **VRAM after an unload went down, not up**: 613 MiB on the branch against 677 MiB on `main` (547 MiB is the CUDA context alone), read from the process's `Dedicated Usage` counter as PM-035 was. The difference is the watermark text features no longer running on the event loop's thread.

| Workers | Real set, first 600 | Real set, all 1,876 | 30 PNGs, 4096² | Peak RAM, PNGs |
|---|---|---|---|---|
| `main` (serial) | 327.9 s | 854.8 s | 53.3 s | 4.12 GB |
| 1 | 339.0 s | | | |
| 2 | 195.5 s (1.7×) | | | |
| 4 | 139.0 s (2.4×) | 398.6 s (2.1×) | 19.9 s (2.7×) | 4.16 GB |
| 6 | 126.2 s (2.6×) | | | |
| 8 | 104.6 s (3.1×) | 374.1 s (2.3×) | 14.9 s (3.6×) | 4.33 GB |

**Why the cap is 8.** It is never slower than 4 and costs about 0.2 GB, because a prepared tensor is small and the models dominate the process's memory. That is the opposite of T1's case, where each image in flight held 0.5 GB. The rest of the real set gains less from 8 than its first 600 images do: there V2.5 runs at 17 images/s against 29, and DINOv2 at 37 against 42.

**Why it falls short of T4's simulated 4.2×**, which ignored the GIL and torch's own threads:

- The HF-processor checks (V2.5, DINOv2) never keep more than about 4 cores busy, whatever the worker count, which looks like their preprocess holding the GIL. V2.5 is the slowest check of a run either way.
- The open_clip checks and NSFW show 27 cores busy. Most of that is torch's CPU thread pool spinning, and the serial loop already showed 16. Holding torch to one thread brought it to 3 cores at no cost at 4 workers, but lost a fifth of the gain at 8. It is process-wide, so it is left alone, as `cv2.setNumThreads` is above.

The watermark check's text features (a CUDA `encode_text`, once per chunk) also moved onto the GPU thread with T5. They used to run on the event loop's thread, which built a second set of CUDA handles there, PM-035's class, and the executor guard could not see it, since it only follows executor dispatches.

## The chunked job loop

`start_score_job` in `backend/routers/quality.py` reads **only ids** in the request, so the scope is frozen when the job is queued. The job then runs the ticked checks one after another — aesthetic, technical, watermark, CLIP embeddings, DINOv2 (with the layer pass), NSFW, the order `_score_checks` returns — and **within each check** walks the ids in chunks of `SCORE_CHUNK` (1,000). Per chunk it reads the rows' current paths, runs the scorer, writes the results with one ORM bulk `update(Image)` (`updated_at`'s `onupdate` still fires, so the stats cache moves), commits, and calls `job_queue.raise_if_cancelled`. `_write_score_chunk` does the write: it re-reads `scores_stale`, the job's score columns and `quality_flags` in the writing session, right before the write, so the window for a concurrent writer is the write rather than the minutes of inference before it. A row deleted mid-job drops out at either read. This is M9 stage 3 (`docs/dev/scale-plan/README.md`).

**Chunk inside each check, never across checks.** Running every check on chunk 1, then chunk 2, would swap models in and out of VRAM for every chunk, because `model_manager` evicts the least recently used one. So each check loads its model once and holds one lease for all its chunks — never one for the whole job, which would stop `_evict_lru` freeing that model while the next check loads (GPT-032).

**What a stop leaves behind.** Every finished chunk is committed, so a cancel, a crash, a Restart or a shutdown past `JobQueue.stop()`'s grace loses at most the chunk in progress. A cancel saves that chunk too: the scorers return a short list when they see the request, the job writes it, commits, and only then raises. Before this the job held every result (layer embeddings are 18 KB per image) and committed once at the end, so anything but a cancel saved nothing.

**`only_unscored` is the resume path, and the rows are the checkpoint.** Each `_ScoreCheck` carries its own `unscored` predicate — `aesthetic_score IS NULL`; **any** of the six technical columns NULL, so a re-run fills a column added later, like `luminance_score`; `watermark_score`, `nsfw_score`, `clip_embedding`; `dino_embedding`, or `dino_layer_embeddings` when the layer pass is ticked — and `_check_filter` ORs onto it the rows that were **stale when the job started** (the user's ruling, 2026-11-08). That is a snapshot, `stale_at_start`, not a live `scores_stale` test: an earlier check can clear a row's bit, and a live test would then drop the row from the later checks, leaving its CLIP and DINOv2 embeddings describing the old pixels with nothing left to flag them. The stale half is load-bearing, not a convenience; see `docs/dev/quality/scores-stale.md` § The clear predicate. The request-time scope is the union of the ticked checks' filters, so the label's count and the `{"job_id": None, "message": "Nothing left to score"}` answer describe rows some check will visit; each chunk's path read then applies the check's own filter. A failed measurement is stored as NULL (`docs/dev/quality/scoring.md` § The failure contract), so it is retried. It cannot be combined with `only_mismatched` — they contradict on aesthetic — and that pair is a 422.

**Progress follows the `video_extract` precedent: one monotone `percent` across the whole job.** Before the first check the job counts each check's rows (`len(ids)`, or a chunked COUNT under `only_unscored`), and the DINOv2 check counts twice when the layer pass is ticked. `done`/`total` count images within the current check, so `total` equals the job's `total_items` in a full run, and the message names the step: `Technical scoring 4,200/10,000 · step 2 of 3`. The seven scorers take an optional `progress` callback; given one, they call it every ten images in place of their own SSE event, and without one they behave as they always did. For the six GPU checks that cadence lives once, in `run_prefetched`. `backend/tests/test_score_chunking.py` holds all of this: chunk sizes, a cancel and a crash keeping the finished chunks, `only_unscored`'s per-check visits and its two refusals, the stale bit clearing across checks, a stale row still re-embedded after an earlier check cleared it, a row edited mid-job left stale, the marker beside its score, and the percent never going back.

## The model lifecycle

A scoring run loads up to four models, and it frees them when it ends. `SCORING_MODEL_IDS` in `backend/routers/quality.py` is the one definition of which four — `("aesthetic", "aesthetic_v2_5", "dino", "nsfw")` — and the job's auto-unload, the unload endpoint and the page's button all read it, so none of the three can drift from the others. It is a literal rather than a `kind`-based filter over `model_manager.list_models()` on purpose: `kind == "embed"` also matches `tag_embedder`, which scoring never loads and tag consolidation would then have to reload.

The job's `_run` collects ids into `loaded_ids` **beside each loader call** — the same discipline as `aesthetic_marker`, which is captured once beside the choice of aesthetic loader — so what is unloaded is provably what was loaded, rather than a second read of `body.run_*` that could disagree with the branch actually taken. Five loader calls feed four ids; `load_aesthetic` serves three checks (aesthetic, watermark, embeddings), all adding `"aesthetic"`, which is the same shared CLIP backbone the user doc's cost table describes.

The unload itself sits in a `finally` around the whole body, not in a tail statement, for three reasons: a run cancelled through `raise_if_cancelled` should free VRAM too; a scorer that raises should not strand 3.5 GB; and every chunk's `commit()` is already past by then, so it is the post-commit epilogue CLAUDE.md's PM-013 rule asks for — it logs its own exception and can never change the job's outcome. Awaiting inside that `finally` is safe because cancellation here is **cooperative**: `raise_if_cancelled` raises `asyncio.CancelledError` by hand and the task is never `.cancel()`ed (`backend/workers/job_queue.py`). It is scoped to `loaded_ids` and never calls `evict_all()` — a Florence-2 or SAM2 loaded for other work is not this job's to evict. Every one of the job's own per-stage leases is released before it runs, and the unloads are caught **per id**: an id something else has leased (`ModelInUseError`) is logged and skipped, and one failing id never stops the rest.

`thresholds.auto_unload_after_scoring` gates it, default on. `POST /quality/embed-references` is deliberately left alone: it is a synchronous request-path load with no job to hang an epilogue on, and the button below covers it. It leases `aesthetic` across all its files, so an unload landing mid-request skips that model rather than failing the upload.

Two endpoints back the button, both on the quality router rather than in `routers/models.py` — the captioning precedent, where `DELETE /captioning/model/{id}/unload` sits beside the job whose models it frees:

| Endpoint | Returns |
|---|---|
| `GET /quality/models` | `list_models()` filtered to `SCORING_MODEL_IDS`, each carrying the registry's `loaded` flag and `vram_mb` — the button needs both halves, whether to render and what it will free. `vram_mb` is the registry's forecast until a model is resident and its measured `ModelEntry.vram_mb` once it is, so `freed_mb` reports what the process held rather than what the table guessed. |
| `POST /quality/models/unload` | `{"unloaded": [ids that were resident], "freed_mb": int, "in_use": [ids left resident]}`, or **409** while a `quality_score` job is pending or running. The resident set is read *before* the unloads, so the response describes what actually went; an id something else holds a lease on (an `embed-references` request in flight) makes `unload` raise `ModelInUseError`, is caught per id, and is reported under `in_use` and left out of `unloaded`/`freed_mb` while the other ids still unload. |

`model_manager.unload` is a no-op for an unregistered id and takes the per-model lock, so asking for all four unconditionally is safe, and no id comes from the client so there is nothing to validate. The 409 guard queries `BackgroundJob` for a pending-or-running **`quality_score`** and nothing else — that is the only job type loading anything in `SCORING_MODEL_IDS`, since style similarity reads stored embeddings without loading `dino` and detection loads sam2/sam3. `dataset_busy.ensure_not_busy` is the wrong tool here and not merely unused: it is dataset-scoped and the scoring job never takes the flag, while VRAM residency is a property of the whole process. `POST /models/unload-all` still exists and still has no callers — it evicts *everything*, which is precisely what a scoped unload must not do.

On the frontend, `QualityPage` holds a `["quality", "models"]` query (global, not dataset-scoped — residency is a property of the process) and renders *Unload models · N GB* beside *Reset to defaults* only when something is resident. It is disabled while a run is in flight, and the endpoint **refuses with a 409** independently of that. The job leases each model only for the stage that uses it (`docs/dev/model-residency.md` § Leases), so between stages a model the run loaded — and will auto-unload or reload later — is unleased; the job-level 409 keeps a run's models out of reach for its whole life. A response with `in_use` toasts the kept ids (an error toast when nothing was freed). The client-side `isRunning` cannot be that guard on its own, because it knows only about a job *this page instance* started and goes stale on a reload or in a second pane; `onError` surfaces the server's `detail` through `apiErrorDetail`. The terminal-status effect invalidates the same key on **completed, failed and cancelled** alike — not just success, since the auto-unload lives in a `finally` and those are the two cases it exists for — so after an auto-unload run the button removes itself.

`backend/tests/test_quality_model_unload.py` covers all of it, including the job's `finally` on both the success and the raising path — it stubs `backend/ml/aesthetic_scorer` into `sys.modules` (that module imports torch at *its* top, and `_run` imports it unconditionally) and monkeypatches `model_manager`, so it runs torch-free in CI.
