# PM-030: a shared resource was released or stopped without knowing who held it

### Symptom

Three failures, one shape, from the external review (GPT-032, GPT-034, GPT-033):

- **Unload mid-run failed every remaining image (GPT-032).** Clicking *Unload model* on the
  Captioning page during a run (the button stayed enabled) answered 204. The image in
  flight then failed with a device mismatch, every later one with `'ModelEntry' object has
  no attribute 'model'`, and the job ended `completed` with every image failed. The same
  exposure applied to detection (Florence-2, SAM2, SAM3), quality scoring, LaMa inpainting
  and the upscaler, through `DELETE /captioning/model/{id}/unload` (which accepts any
  registry id) and `POST /models/unload-all`. In detection it also **erased data**: a
  failed inference with *Overwrite* on still deleted the image's existing detections.
- **Unload-all left Grounding DINO resident (GPT-034).** `evict_all()` returned `['sam2']`
  and left an empty registry, while `sam2_predictor._gdino_cache` still held Grounding DINO
  on the accelerator (~0.7 GB) for the life of the process. SAM2's own network did not
  move either: `predictor.cpu()` raised on the non-module predictor and was swallowed.
- **ComfyUI cancel killed the user's canvas prompt (GPT-033).** With a prompt queued from
  the ComfyUI canvas running and ours pending, *Cancel* sent a bare `POST /interrupt`,
  which stopped the canvas prompt and left ours queued. A row timeout left our prompt in
  ComfyUI's queue and submitted the next row on top of it.

### Root cause

Each release path acted on a resource with no record of who was using it:

- **Models.** `ModelEntry.in_use` was a bool that only tag consolidation set and only LRU
  eviction read. `unload` and `evict_all` ignored it, and the per-model `asyncio.Lock`
  that `docs/dev/ml-models.md` said "serializes inference" was held only while loading.
  So a job's hold on its model existed only in the job's local variables, invisible to the
  route that freed it.
- **Grounding DINO.** It lived in a module global beside the registry rather than in the
  `sam2` entry, so the only release mechanism the app has, `_release_to_cpu` over an
  entry, could not see it.
- **ComfyUI.** `/interrupt` with no body means "whatever is executing", and the
  `comfy_generate` job assumed that was ours. `docs/dev/comfyui.md` stated the assumption
  as fact ("ComfyUI's own queue is never stacked"), which was true only of *our* prompts
  and only on the success path.

### Generalizable rule

- **A release must be refusable by its holder.** Flag any path that frees, deletes or
  stops a shared resource (a registry entry, a cache, a remote job) without consulting a
  record that the resource's users maintain. Every consumer must register its hold (here,
  `ModelManager.lease`) for exactly as long as it reads the resource, taken with no
  `await` between acquiring the resource and registering the hold.
- **Anything loaded on the side of a resource belongs inside it.** A module-level cache
  next to a registry is invisible to that registry's release. Flag a `global _cache` in a
  module whose models the registry manages.
- **Act on a shared external service by identity, never by "whatever is current".** Flag
  a global stop or clear call (`/interrupt` with no id, `{"clear": true}`) where a
  targeted one exists, and any cleanup that assumes the only work in a shared queue is
  ours.
- **A destructive overwrite must be gated on there being a replacement.** Flag a "delete
  then insert the new result" whose delete runs when computing the result failed.

### Why it wasn't caught the first time

- Every lifecycle test drove the unload *or* the job, never one while the other ran, and
  the one unload test that mattered monkeypatched `unload` itself. The Quality page's
  409 guard protected only its own route, so it never prompted the same question about
  the captioning route.
- The ComfyUI path had no mock server, so the run body's cancel and timeout branches had
  no coverage, and the doc's "never stacked" claim was never tested against a second
  client.
- Grounding DINO's cache was documented as "process-lifetime" and read as a choice, not
  a leak.

### Fix

- `f5f05f5`: counted leases on `ModelEntry`. `unload` raises `ModelInUseError` (409),
  `evict_all` skips leased entries, and leasing an already-released entry fails up front.
- `b5e9b2b`, `e45d267`: caption run and pipeline leases; the Unload button is disabled
  mid-run and surfaces a refused unload.
- `a907518`: detection leases; overwrite gated on `inference_ok`.
- `daca56f`, `5290ba8`: quality per-stage leases with per-id unload handling; LaMa
  whole-job lease; upscaler per-image lease.
- `0cd8efa`: Grounding DINO and `sam2_model` are tenants of the `sam2` entry dict.
- `98f4606`, `a9ba07b`: `ComfyClient.cancel_prompt` deletes and interrupts only our own
  prompt (by id and `client_id`). The run takes it out of ComfyUI on every failure or
  cancel, and aborts rather than stack when it cannot.

### Status & date

MITIGATED. A new model consumer that skips the lease, or a new release path that bypasses
`ModelManager`, reintroduces the class.
Last reviewed for staleness: 2026-10-28.
