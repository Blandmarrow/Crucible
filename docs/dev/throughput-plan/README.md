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

Nothing here has been timed. Every stage starts by measuring the path it changes, and is
dropped if the gain is not there.

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
| A full scoring run | Each check decodes every file again: up to five decodes for aesthetic, watermark, CLIP, DINOv2 and technical. Watermark and CLIP each run the CLIP image encoder | From the audit |
| `technical_scorer.score_images_technical` | CPU-only (OpenCV). One `run_in_executor` call per image, awaited, so one core is busy | Checked |
| `openai_compat_captioner._caption_sync` | One request at a time, and a new `OpenAI(...)` client for each image | Checked |
| `ollama_captioner` | One request at a time, and a new `httpx.AsyncClient` for each image | Checked |
| The local captioners (Florence-2, PaliGemma, JoyCaption) | One image per call. `caption_batch` is defined in `florence_captioner.py`, `paligemma_captioner.py` and `ollama_captioner.py`, and nothing outside the tests calls it | Checked for `caption_batch`; from the audit for the per-image loop |
| `wd14_tagger` | Its ONNX session is built with `providers=["CPUExecutionProvider"]`, one image per call | Checked |
| `import_images_from_folder` | One file per `run_in_executor` call, awaited. Each file is opened about four times and fully decoded twice (once for size and phash, once for the thumbnail) | Checked for the loop; from the audit for the open count |
| Detection jobs | One image per call, decode inside the GPU call, a session and a commit per image | From the audit |
| Export | One image at a time. A plain copy never decodes; a resize or format change re-encodes on one core | From the audit |
| `JobQueue` | One worker, so a CPU-only job waits behind a GPU job | Checked |
| Progress events | In-memory only, no DB write per event | From the audit |

## Stages

CPU-only work first, since it cannot touch the GPU rule at all.

### T1 — The technical scorer on several cores

Run a bounded number of `score_technical_sync` calls at once on the default pool
(`asyncio.gather` under a semaphore, or `as_completed`), keeping the result order, the
failure contract (`_unmeasured()` per failed image), the per-image cancel check and the
`progress` callback that stage 3 of M9 added. OpenCV releases the GIL, so threads are
enough. Pick the bound from the core count, and leave cores for the server.

**Acceptance.** The scores match the serial path exactly on a fixture (it is the same
function per image). A timing on a few thousand images shows the gain.

### T2 — Concurrent requests for the remote captioners

For the OpenAI-compatible and Ollama captioners, send a bounded number of requests at once
and reuse one client for the job. A local server such as vLLM or llama.cpp can batch
requests that arrive together; a hosted one just answers in parallel.

- **A per-provider setting, default 1.** Other people's servers differ, and a server that
  cannot take two requests must keep working unchanged
  (`docs/dev/llm-providers.md` owns the provider row and its hand-kept field list).
- **Keep what PM-023 fixed**: the configured timeout, `max_retries=0`, and a failure that
  shows on the job (`docs/dev/captioning.md`).
- **The paid-call cap** of an automated run counts these calls (M9 stage 10). Concurrency
  must not let a run overshoot its cap by more than the requests in flight; say by how many.
- **The Strata handoff** ([decision 10](../decisions/10-unload-strata-while-comfyui-generates.md))
  is untouched: it happens between stages, not between requests.

**Acceptance.** With the setting at 1 the request order and results are as today. At N, a
fake server sees at most N requests in flight, and a cancel stops new ones at once.

### T3 — Parallel import, and one decode per file

Ingest a bounded number of files at once on the default pool. The name and stem picking
(`occupied_thumb_stems`, `planned_thumb_stems`) is stateful and must stay serial: pick
names in order on the event loop, then fan out the file work
(`docs/dev/image-import.md` § A stem-keyed derived artifact needs a collision guard at
every writer). Then share one decode between `get_image_info` and `generate_thumbnail`,
which today each open and decode the file.

**Acceptance.** An import of a fixture folder produces the same rows, names, thumbnails and
provenance as the serial path, in the same order. The rescan path gets the same treatment
or a note saying why not.

### T4 — Time the decode share on a real card

Before any GPU-path change: on a real card, time one scoring check over a few thousand
images and split each image's time into decode plus preprocess, and the model call. Do it
for small JPEGs and for large PNGs. Record the numbers here.

No gate runs torch (`CLAUDE.md` § Verification, under "What no gate runs"), so this is a
hand measurement.

### T5 — Decode ahead of the GPU call (only if T4 says so)

If decode and preprocess are a large share: prepare the next few images on the default
pool while the GPU thread runs the current one. Split each `*_sync` scorer into a CPU half
(`open_rgb`, `preprocess`, close the image) and a GPU half (`.to(device)`, the forward
pass).

- **Bound the queue** to a few images. A prepared tensor sits in RAM until the card takes
  it.
- **Keep the invariants**: `open_rgb` for every open, and `img.close()` right after
  preprocessing (`CLAUDE.md` § Key invariants).
- **One image per forward pass still**, so the scores cannot change.

**Acceptance.** Scores are identical to the serial path on a fixture. VRAM after the job
and an unload returns to where PM-035's fix left it (its hand check, repeated).

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
- **One decode shared across a scoring run's checks.** It would cut up to five decodes to
  one, but it turns the per-check loop inside out, and M9 stage 3 chose chunking inside
  each check so that models are not swapped in and out of VRAM. Revisit after T4.
- **Detection and export** follow the same one-at-a-time pattern. Neither was the complaint
  that started this plan.
