# M10 — Job throughput

**Status: transient.** The design plan for M10. Its place in the order is in
[../roadmap.md](../roadmap.md); why it is a milestone of its own, and what it will not
build, is [decision 14](../decisions/14-job-throughput-is-its-own-milestone.md). When a
stage lands, move its durable rationale into the topic file that owns the code
(`docs/dev/ml-models.md`, `docs/dev/quality/scoring.md`, `docs/dev/captioning.md`,
`docs/dev/image-import.md`) and delete the stage from here. Delete the folder when
everything is done or dropped.

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
| `score_image_sync` | Decodes the file (`open_rgb`), preprocesses it, moves it to the card and runs the model, all inside the one GPU call. The card waits during the first two steps | Checked |
| A full scoring run | Each check is its own pass and decodes every file again: up to seven decodes with every box ticked, six GPU passes (aesthetic, watermark, CLIP, DINOv2 and its layers, NSFW) plus the technical check's own `cv2` read. Watermark and CLIP each run the CLIP image encoder again. **Measured by T4**: decode alone is 13–23% of a full run on small JPEGs and 54–68% on large PNGs and real ComfyUI images | Checked by T4 |
| `technical_scorer.score_images_technical` | CPU-only (OpenCV). One `run_in_executor` call per image, awaited, so one core is busy. **Fixed by T1**: up to 4 at once, 2.0× to 2.3× faster (`docs/dev/quality/scoring.md` § The technical scorer runs several images at once) | Checked |
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
`docs/dev/quality/scoring.md` § The technical scorer runs several images at once. T3,
parallel import and one decode per file, is done too; its design and timing live in
`docs/dev/image-import.md` § Several files at once, one decode each. T2, concurrent requests
for the remote captioners, is done; its design lives in `docs/dev/captioning.md` § Requests
in flight, and its rulings in [decision 16](../decisions/16-concurrent-caption-requests.md).

T3 put T1's bounded FIFO window (await the oldest future first) into a class,
`_InOrderWindow` in `dataset_service.py`, kept private to that module for now. T2 did not
reuse it: T2 runs coroutines, not executor futures, and a cancelled call has to come back
as a failure with its item, which `pop_oldest` cannot do. T2 added `utils.InOrderCalls`
instead. T1 can move onto `_InOrderWindow` once `docs/dev/quality/scoring.md` is split (it
is over budget, see `docs/dev/pending-splits.md`), and the class then belongs in
`backend/utils.py`.

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

Per check, decode plus preprocess (the half T5 moves) is 8–75% of the time on JPEGs,
lowest for the V2.5 aesthetic model and highest for DINOv2 and NSFW. It is 73–99% on the
large PNGs and 63–93% on the real set. A 4K PNG takes about 135 ms to decode and 50–100 ms
to preprocess, against 3–30 ms for the model.

What else the runs showed:

- **A cold file cache changes little on a local NVMe drive.** Evicting each file before it
  is read added 0.3–1.6 ms per JPEG and about 10% per PNG decode, and moved no T5 figure by
  more than 0.06×. Decoding, not reading, is the cost.
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

### T5 — Decode ahead of the GPU call

Justified by T4. Prepare the next few images on the default pool while the GPU thread runs
the current one. Split each `*_sync` scorer into a CPU half (`open_rgb`, `preprocess`,
close the image) and a GPU half (`.to(device)`, the forward pass).

- **Use about 4 workers, not 1.** T4's large-image gain comes from several images being
  prepared at once. Re-time the worker count, as T1 and T3 did, since each in-flight 4K
  image holds a decoded buffer and its tensor.

- **Bound the queue** to a few images. A prepared tensor sits in RAM until the card takes
  it.
- **Keep the invariants**: `open_rgb` for every open, and `img.close()` right after
  preprocessing (`CLAUDE.md` § Key invariants).
- **One image per forward pass still**, so the scores cannot change.

**Acceptance.** Scores are identical to the serial path on a fixture. VRAM after the job
and an unload returns to where PM-035's fix left it (its hand check, repeated). Re-run
`bench_score_decode.py` and a real scoring job, and compare against T4's figures above.
Its twins must then be split the same way, or its parity guard will fail.

### T6 — Fetch model weights off the GPU thread

PM-035's follow-up. A first-run model download happens inside a loader, and loaders run on
the GPU thread, so the download holds up every other GPU call until it ends. Fetch the
weights on the default pool first, then load on the GPU thread
(`docs/dev/model-registry.md` and `docs/dev/image-processing-models.md` list where each
model's weights are cached). It waits on nothing and can go at any time.

## Not built

- **Batched forward passes.** Several images in one model call still run on the GPU thread,
  so memory is safe, but the scores may move in the last decimal place. That is not
  measured; the nearest evidence is M9 stage 5, where float32 rounding moved stored style
  scores by 0.0001 when only the row order changed. The owner's rule is quality over speed,
  so this stays out unless a measurement and a later decision supersede decision 14.
- **A second GPU thread, or GPU calls on the default pool.** That is PM-035.

## Open questions, for later

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
