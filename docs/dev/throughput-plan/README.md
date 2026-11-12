# M10 — Job throughput

**Status: transient.** The design plan for M10. Its place in the order is in
[../roadmap.md](../roadmap.md); why it is a milestone of its own, and what it will not
build, is [decision 14](../decisions/14-job-throughput-is-its-own-milestone.md). When a
stage lands, move its durable rationale into the topic file that owns the code
(`docs/dev/ml-models.md`, `docs/dev/quality/scoring.md`, `docs/dev/quality/scoring-job.md`, `docs/dev/captioning.md`,
`docs/dev/image-import.md`) and delete the stage from here. Delete the folder when
everything is done or dropped. M10 landed on 2026-11-12 with T6 parked
([roadmap-archive/m10-job-throughput.md](../roadmap-archive/m10-job-throughput.md)), so
this folder now holds T6, T4's timing and the open questions.

A review on 2026-11-09 read the long-running jobs for throughput lost to anything other
than model speed. It found one pattern everywhere: **each job handles one image at a time,
and awaits it before starting the next**, even where the work is CPU-only and the machine
has idle cores. This plan orders the fixes by risk, lowest first.

The review itself timed nothing. Every stage starts by measuring the path it changes, and
is dropped if the gain is not there.

## The constraint: one GPU thread

PM-035 put every model load and inference call on one dedicated thread, because each
thread that runs CUDA math keeps its own cuBLAS and cuDNN handles for life, and the default
pool's threads stranded about 3 GB of VRAM (`docs/dev/ml-models.md` § One GPU thread).

**No stage of this plan moves a GPU call off that thread, or adds a second GPU thread.**
Only CPU work moves, onto the default pool, which the rule already allows: the comment in
`backend/ml/device.py` says "CPU-only work stays on the default pool, which stays
parallel". `backend/tests/test_gpu_executor_guard.py` keeps enforcing the rule, and a stage
that needs to change that guard has gone wrong.

One thing must stay on the GPU thread that looks like CPU work: moving a tensor to the card
(`.to(device)`). A pool thread prepares a CPU tensor and nothing more.

This slowness is not a regression from PM-035's fix. The loops awaited one image at a time
before it too.

## What the review found

*Checked* means the code was read on 2026-11-09. *From the audit* means a read-only
subagent reported it and it was not re-read; verify it before building on it.

| Path | What it does now | Source |
|---|---|---|
| Every scorer (`backend/ml/aesthetic_scorer.py`, `dino_scorer.py`, `nsfw_scorer.py`) | One `run_on_gpu` call per image, awaited in a loop. `SCORE_CHUNK` sizes the DB chunk, not the inference batch | Checked for the aesthetic scorer; from the audit for the rest |
| `score_image_sync` | Decodes the file (`open_rgb`), preprocesses it, moves it to the card and runs the model, all inside the one GPU call. The card waits during the first two steps. **Fixed by T5**: each scorer's `prepare_*` half runs on the default pool, up to 8 images ahead, 2.3× to 3.6× faster, scores identical | Checked |
| A full scoring run | Each check is its own pass and decodes every file again: up to seven decodes with every box ticked, six GPU passes (aesthetic, watermark, CLIP, DINOv2 and its layers, NSFW) plus the technical check's own `cv2` read. Watermark and CLIP each run the CLIP image encoder again. **Measured by T4**: decode alone is 13–23% of a full run on small JPEGs and 54–68% on large PNGs and real ComfyUI images | Checked by T4 |
| `technical_scorer.score_images_technical` | CPU-only (OpenCV). One `run_in_executor` call per image, awaited, so one core is busy. **Fixed by T1**: up to 4 at once, 2.0× to 2.3× faster (`docs/dev/quality/scoring-job.md` § The technical scorer runs several images at once) | Checked |
| `openai_compat_captioner._caption_sync` | One request at a time, and a new `OpenAI(...)` client for each image. **Fixed by T2**: up to a provider's Max concurrent requests at once, on one async client per job (`docs/dev/captioning.md` § Requests in flight) | Checked |
| `ollama_captioner` | One request at a time, and a new `httpx.AsyncClient` for each image. **Fixed by T2**, with one app-wide setting since Ollama has no provider row | Checked |
| The local captioners (Florence-2, PaliGemma, JoyCaption) | One image per call. `caption_batch` is defined in `florence_captioner.py`, `paligemma_captioner.py` and `ollama_captioner.py`, and nothing outside the tests calls it | Checked for `caption_batch`; from the audit for the per-image loop |
| `wd14_tagger` | Its ONNX session is built with `providers=["CPUExecutionProvider"]`, one image per call | Checked |
| `import_images_from_folder` | One file per `run_in_executor` call, awaited. Each file is opened about four times and fully decoded twice (once for size and phash, once for the thumbnail). **Fixed by T3**, which found a PNG was decoded four times, not twice: up to 8 files at once and one decode each, 7.6× faster on JPEGs and 18× on large PNGs (`docs/dev/image-import.md` § Several files at once, one decode each) | Checked |
| Detection jobs | One image per call, decode inside the GPU call, a session and a commit per image | From the audit |
| Export | One image at a time. A plain copy never decodes; a resize or format change re-encodes on one core | From the audit |
| `JobQueue` | One worker, so a CPU-only job waits behind a GPU job | Checked |
| Progress events | In-memory only, no DB write per event | From the audit |

## Stages

CPU-only work first, since it cannot touch the GPU rule at all.

T1, the technical scorer on several cores, is done; its design and timing now live in
`docs/dev/quality/scoring-job.md` § The technical scorer runs several images at once. T3,
parallel import and one decode per file, is done too; its design and timing live in
`docs/dev/image-import.md` § Several files at once, one decode each. T2, concurrent requests
for the remote captioners, is done; its design lives in `docs/dev/captioning.md` § Requests
in flight, and its rulings in [decision 16](../decisions/16-concurrent-caption-requests.md).
T5, decoding ahead of the GPU call, is done; its design, the checks that the scores did not
move, and its timing live in `docs/dev/quality/scoring-job.md` § The GPU checks prepare
images ahead. It reached 2.3× on the real set against T4's simulated 4.2×, for reasons
recorded there.

T3 put T1's bounded FIFO window (await the oldest future first) into a class, now
`utils.InOrderWindow` (T5 moved it out of `dataset_service.py` to use it in
`ml/prefetch.py`). T2 did not reuse it: T2 runs coroutines, not executor futures, and a
cancelled call has to come back as a failure with its item, which `pop_oldest` cannot do.
T2 added `utils.InOrderCalls` instead. T1 can still move onto `InOrderWindow`; it keeps its
own deque for now.

### T4 — Time the decode share on a real card (done)

Timed on 2026-11-12 with `backend/scripts/bench_score_decode.py`, whose docstring is its
manual. It times each GPU check's four stages per image (decode, preprocess, the copy to
the card, the forward pass) through instrumented twins of the app's `*_sync` functions.
Each twin's tensors in and out of every model, and its autocast state, are checked
bit for bit against the real function before timing, and the real function is timed beside
it. Twin and app agreed within 1% on every check. T5's speed-up is simulated over the
measured per-image times: k threads prepare images in order, one GPU thread takes them,
and at most 4 wait ahead.

Two machines, both with 32 logical cores and NVMe drives: a 32 GB card on Windows and a
16 GB card on Linux. Three image sets: 2,000 synthetic 1024×768 JPEGs, 100 synthetic
4096×4096 PNGs (ComfyUI's compression level), and a real ComfyUI dataset of 1,876 images,
mostly PNGs around 2000 px. Figures are for a full run (all six GPU checks, one after
another), per image:

| Set | Card | Run time | Decode share | T5, 1 worker | T5, 4 workers | One shared decode saves |
|---|---|---|---|---|---|---|
| JPEG 1024×768 | 32 GB | 86 ms | 21% | 1.61× | **2.08×** | 18% |
| JPEG 1024×768 | 16 GB | 85 ms | 13% | 1.36× | **1.64×** | 11% |
| PNG 4096² | 32 GB | 1,322 ms | 61% | 1.09× | **4.26×** | 51% |
| PNG 4096² | 16 GB | 1,218 ms | 68% | 1.05× | **4.14×** | 56% |
| Real, ~2000 px | 32 GB | 334 ms | 54% | 1.16× | **4.16×** | 45% |
| Real, first 600, over SMB | 16 GB | 475 ms | 69% | 1.13× | **3.75×** | 57% |

Per check, decode plus preprocess (the half T5 moves) is 8–75% of the time on JPEGs,
lowest for the V2.5 aesthetic model and highest for DINOv2 and NSFW. It is 73–99% on the
large PNGs and 63–93% on the real set. A 4K PNG takes about 135 ms to decode and 50–100 ms
to preprocess, against 3–30 ms for the model.

What else the runs showed:

- **A cold file cache changes little on a local NVMe drive.** Evicting each file before it
  is read added 0.3–1.6 ms per JPEG and about 10% per PNG decode, and moved no T5 figure by
  more than 0.06×. Decoding, not reading, is the cost.
- **A network share costs more, and T5 hides it too.** The 16 GB card's machine read the
  real set's first 600 images from a datasets folder on an SMB share. Decode took about
  54 ms per image even after a read-through, against 31 ms for the whole set on a local
  disk on the other machine. Those are not the same files, and the share may not keep pages
  cached, so the gap is not split between the two. Evicting each file first raised it to
  64 ms, and the 4-worker figure to 4.21×. A read happens in T5's CPU half, so it overlaps
  the GPU like the decode does.
- **Preprocess does not lean on torch's own threads.** With torch held to one thread it
  moved from 3.6 to 3.4 ms on a JPEG and 56 to 53 ms on a PNG. So T5's workers will not
  be fighting torch's pool for cores, which was the main doubt over the 4-worker figure.
  The simulation still ignores GIL contention, so it remains an upper bound.
- **One worker is not enough for large images.** Its gain is 1.05–1.16×, because the CPU
  half is still the slow one. Four workers are what reach 4×.
- **The model call ran 2–3× slower on the PNG set than on the JPEG set on the 32 GB card**
  (aesthetic: 21 ms against 9.6 ms). The likeliest cause is the card dropping its clocks
  during the long CPU gaps between images, which T5 would close. Not investigated.

**Verdict: T5 pays.** Decision 14's condition, that decoding be a large share of a run, is
met on both cards and on real data.


### T6 — Fetch model weights off the GPU thread (parked)

PM-035's follow-up. A first-run model download happens inside a loader, and loaders run on
the GPU thread, so the download holds up every other GPU call until it ends. Fetch the
weights on the default pool first, then load on the GPU thread
(`docs/dev/model-registry.md` and `docs/dev/image-processing-models.md` list where each
model's weights are cached).

**Parked on 2026-11-12, by the user's call.** It costs a stall once per model per machine,
and every later load reads from the cache, so it is not worth its size now. Reopen it if a
first-run download is seen holding up a job that matters, such as a long download stalling
an automated run's scoring.

What sizing it found, read from `backend/ml/model_manager.py` and the predictor modules
that day, so the next session need not count again:

- **About 11 loaders download on the GPU thread, five ways.** Hugging Face
  `from_pretrained`: Florence-2 (both variants), PaliGemma-2, JoyCaption, DINOv2, NSFW,
  SAM2 and Grounding DINO. `open_clip`'s `pretrained=`: the LAION aesthetic backbone.
  `torch.hub`, inside the `aesthetic_predictor_v2_5` package: V2.5's head. Our own URL
  download: the LAION head and LaMa. `sentence-transformers`: the tag embedder.
- **Not on the list:** SAM 3 never downloads (its checkpoint is placed by hand), and WD14
  and NudeNet do not load through `run_on_gpu`.
- **The hard part is the file list.** A Hugging Face prefetch (`snapshot_download`) must
  fetch exactly the files the loader later reads. Too broad pulls gigabytes nobody uses;
  too narrow lets the loader download the rest on the GPU thread, silently. Loading with
  `local_files_only=True` after the prefetch would make a miss fail loudly instead, at the
  price of breaking a first run where the list has a gap.
- **No gate can check it.** CI has no torch and no real download, so a mocked test can
  prove the fetch runs before the load but not that the file lists match. That needs a
  first run on an empty cache, model by model.
- **A smaller first cut** is the eight Hugging Face loaders alone, through one helper,
  leaving the other three ways for later.

## Not built

- **Batched forward passes.** Several images in one model call still run on the GPU thread,
  so memory is safe, but the scores may move in the last decimal place. That is not
  measured; the nearest evidence is M9 stage 5, where float32 rounding moved stored style
  scores by 0.0001 when only the row order changed. The owner's rule is quality over speed,
  so this stays out unless a measurement and a later decision supersede decision 14.
- **A second GPU thread, or GPU calls on the default pool.** That is PM-035.

## Open questions, for later

- **What still holds the GPU checks back after T5.** Two different limits, measured on
  2026-11-12 (`docs/dev/quality/scoring-job.md` § The GPU checks prepare images ahead).
  The HF-processor checks (V2.5, DINOv2) never use more than about 4 cores at any worker
  count, which looks like their preprocess holding the GIL; V2.5's SigLIP processor is the
  slowest half of a run. The open_clip checks and NSFW show 27 cores busy, most of it
  torch's CPU pool spinning, and the serial loop already showed 16. Holding torch to one
  thread is process-wide, so it was left alone, as T1 left OpenCV's.

- **WD14 on the GPU.** It runs on the CPU provider. Was that a choice (VRAM, a Windows
  provider problem) or a default? Find out before proposing a change.
- **CPU-only jobs beside a GPU job.** The queue has one worker. An export or import could
  run beside a scoring job. What else assumes one job at a time (the busy guards, the model
  leases, the progress bar) has not been surveyed. This is a design question, not a quick
  win.
- **One decode shared across a scoring run's checks.** T4 puts the saving at 11–18% of a
  full run on small JPEGs and 45–56% on large PNGs, and that counts decode only. But it
  turns the per-check loop inside out, needs every model resident at once, and M9 stage 3
  chose chunking inside each check so that models are not swapped in and out of VRAM.
  After T5, most of that decode runs off the GPU thread anyway, so re-measure before
  weighing it.
- **Repeated work T4's review found**, none of it timed:
  - The aesthetic (LAION) and watermark checks run the same CLIP `encode_image` on the
    same tensor under the same autocast, so their image features are identical. CLIP
    embedding runs it again without autocast.
  - With layers on, the DINOv2 layer pass already returns the final CLS token that the
    plain DINOv2 pass computes again.
  - `open_rgb` copies a whole RGB image twice after decoding it: `convert("RGB")` on an
    image already in RGB returns a copy, and so does `exif_transpose` with no orientation
    tag. That is about 96 MB of copying per 4K image, inside every check's decode.
- **Detection and export** follow the same one-at-a-time pattern. Neither was the complaint
  that started this plan.
