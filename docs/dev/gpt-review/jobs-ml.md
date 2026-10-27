# ML-lifetime and ComfyUI findings

These are the verified findings for model lifetimes and the ComfyUI job. The job-queue and SSE
findings (GPT-012, 013, 014) are fixed; see PM-027
(`docs/dev/postmortems/PM-027-shutdown-cancel-swallowed-by-job-handler.md`). Read
`docs/dev/backend-infrastructure.md`, `docs/dev/frontend-jobs.md` and `docs/dev/ml-models.md`
first. The probes are `docs/dev/gpt-review/probes/backend/probe_caption_unload.py`, `probe_comfy.py` and
`probe_gdino.py`. All of them are mock-level: no GPU, no model download and no real ComfyUI.
Conventions are in `docs/dev/gpt-review/README.md`.

## GPT-032 — [P2] Caption-model unload during a run fails every remaining image

**Verdict:** CONFIRMED, **worse than claimed**. Partly known: `docs/dev/ml-models.md` notes that
`ModelEntry.in_use` is never set.

**Mechanism.**
- `DELETE /captioning/model/{id}/unload` (`routers/captioning.py:717-718`) and unload-all
  (`routers/models.py`, `evict_all`) are unconditional.
- `model_manager.unload` moves the weights to CPU **and `del entry.model`**.
- The caption job holds the entry with no lease.
- `routers/quality.py`'s unload has a 409 guard for a running `quality_score` job. Captioning has
  no equivalent.
- The Unload button on `CaptioningPage` stays enabled during a run.

**Observed** (`probe_caption_unload.py::test_032_unload_during_inference`, a fake Florence entry
over 2 images):
- An unload during image 1 returns 204.
- Image 1 fails with a device mismatch.
- Image 2 fails with `'ModelEntry' object has no attribute 'model'`.
- The job ends `completed` with `failed_count: 2`.

An unload at *any* point mid-run fails every remaining image.

**Fix notes.**
- Add an in-use lease spanning executor inference, honoured by `unload`, `evict_all` and LRU
  eviction. Make it defer or 409.
- At minimum, add the quality-style job guard to the captioning and unload-all routes.
- Audit the other inference consumers too: detection, tag consolidation (which already sets
  `in_use`), and upscale.

## GPT-033 — [P2/P3] ComfyUI cancel interrupts whatever is running; timeout leaves prompts queued

**Verdict:** CONFIRMED (mock). **The code contradicts the docs.**

**Mechanism.**
- `ComfyClient.interrupt()` (`services/comfy_service.py:99-106`) posts a global `/interrupt` with
  no prompt id.
- The cancel branch in `routers/comfy.py` (~1388-1393) calls it without checking that our prompt
  is the one executing.
- The timeout branch (~1394-1398) raises without removing the submitted prompt, and the next row
  is submitted on top of it.

**Observed** (`probe_comfy.py`, `httpx.MockTransport`):
- With a canvas prompt running and ours queued, cancel interrupts the *canvas* prompt, and ours
  stays queued.
- After a timeout, both of our prompts remain queued, with no `/queue` delete and no interrupt.

**Realistic in single-user use**: the sync-from-canvas workflow (`docs/dev/comfyui-sync.md`)
assumes the user also generates from the ComfyUI canvas against the same server.

**Wrong docs:** `docs/dev/comfyui.md` says "ComfyUI's own queue is never stacked" and calls
`/interrupt` "sufficient here because … nothing of ours ever waits in ComfyUI's queue".

**Fix notes.**
- Check `/queue` before acting: if our prompt is running, interrupt it; if it is pending, delete
  it via `POST /queue {"delete":[id]}`.
- On timeout, delete or interrupt our prompt and surface any cleanup failure.
- Correct the docs.

## GPT-034 — [P3, GPT said P2] Unload-all leaves SAM2's Grounding DINO resident

**Verdict:** CONFIRMED (mock). The behaviour is KNOWN: `docs/dev/detection-inference.md`
documents the process-lifetime cache. What is undocumented is that unload-all does not release
it.

**Observed** (`probe_gdino.py::test_034_unload_all_leaves_gdino`): after `evict_all()`, which
returns `['sam2']` and leaves an empty registry, `sam2_predictor._gdino_cache` still holds the fake
CUDA model and `.cpu()` was never called. It is roughly 0.7 GB in fp32; that figure is an estimate,
not a measurement.

It is unverified whether `_release_to_cpu` actually frees SAM2's own predictor. `SAM2ImagePredictor`
may have no `.cpu()`, and the call would then be swallowed. Check this with `sam2` installed.

**Fix notes.** Register Grounding DINO as an owned child of the sam2 entry, or a registry entry of
its own with an unload hook, so that unload of sam2 or of everything releases it. It needs the same
lease as GPT-032.
