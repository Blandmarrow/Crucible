# M10 — Job throughput

**Status: archive (landed 2026-11-12).** *Record for the M10 line in the
[roadmap](../roadmap.md#landed).* Merged task by task on 2026-11-12, PRs #145 to #149. Its
plan, [throughput-plan/README.md](../throughput-plan/README.md), stays in place: it holds
T6, which was parked, and the open questions, none of them timed.

Came after M9 and before M7
([decision 14](../decisions/14-job-throughput-is-its-own-milestone.md)). Gate: T1–T3
merged with their tests, T4's timing recorded, and T5 then built or ruled out by that
timing. T5 was built, which met the gate. PM-035's one-GPU-thread rule was not touched:
only CPU work moved.

- [x] T1: the technical scorer on several cores. Merged 2026-11-12 as PR #145: up to 4
  calls at once, 2.0× to 2.3× faster, scores identical. Detail:
  `docs/dev/quality/scoring-job.md` § The technical scorer runs several images at once.
- [x] T2: concurrent requests and a reused client for the OpenAI-compatible and Ollama
  captioners. Merged 2026-11-12 as PR #147: a per-provider Max concurrent requests and one
  app-wide Ollama setting, default 1, in both caption loops; a crash leaves at most N calls
  uncounted ([decision 16](../decisions/16-concurrent-caption-requests.md)). Detail:
  `docs/dev/captioning.md` § Requests in flight.
- [x] T3: parallel import and rescan, with one decode per file. Merged 2026-11-12 as
  PR #146: up to 8 files at once, and a PNG decoded once instead of four times, 7.6× faster
  on JPEGs and 18× on large PNGs. Name and stem picking stays serial. Detail:
  `docs/dev/image-import.md` § Several files at once, one decode each.
- [x] T4: time how much of a GPU scoring run is image decoding, on a real card. Timed
  2026-11-12 on two cards with `backend/scripts/bench_score_decode.py`, merged as PR #148:
  decode plus preprocess is 63–93% of each check on real ComfyUI images. Detail:
  [throughput-plan/README.md § T4](../throughput-plan/README.md#t4--time-the-decode-share-on-a-real-card-done).
- [x] T5: decode ahead of the GPU call, on the default pool. Merged 2026-11-12 as PR #149:
  each GPU scorer split into a `prepare_*` and an `infer_*` half, run by `ml/prefetch.py`
  up to 8 images ahead; scores identical to the serial path, VRAM lower, 2.3× to 3.6×
  faster. Detail: `docs/dev/quality/scoring-job.md` § The GPU checks prepare images ahead.
- **Parked:** T6, fetch a model's weights off the GPU thread. Parked on 2026-11-12 by the
  user's call, as not worth its size now. Why, and what reopens it:
  [throughput-plan/README.md § T6](../throughput-plan/README.md#t6--fetch-model-weights-off-the-gpu-thread-parked).

## Left open

None of this was part of the gate. Each item is listed so it is not found again later.

- **Found while building T2, not fixed:** `/pipeline` silently ignores `save_backup` and
  `rename_on_caption`. Both are on `CaptionPipelineRequest` and the frontend sends them,
  but `_run_pipeline_body` never reads them. Wants the user's call: wire them up, or drop
  them from the request and the pipeline form.
- **Found while building T4, half fixed by T5:** `score_images_watermark` called
  `_precompute_watermark_text_features`, a CUDA `encode_text`, on the event-loop thread
  (PM-035's class). T5 sends it through `run_on_gpu`. Still open:
  `test_gpu_executor_guard.py` cannot see a direct GPU call like that one, because it only
  follows executor dispatches, so it wants a rule for direct calls.
- **Not timed:** the repeated work and the other limits in the plan's § Open questions,
  for later.
