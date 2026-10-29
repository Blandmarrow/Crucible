# Model residency

This file covers what keeps a loaded model resident and how its weights leave: the explicit `unload`/`evict_all` methods and the scoped unload endpoints, the inert VRAM budget setting `_evict_lru` ignores, the counted leases that stop a running job's model being released under it (`ModelInUseError`), and `_release_to_cpu`, which frees a dict-shaped entry tenant by tenant. Read it before touching a consumer's hold on a model or any unload path.

Loading, `HF_TOKEN` and the device module are `docs/dev/ml-models.md`; what each model ID's entry holds is `docs/dev/model-registry.md`; the app-level handler that maps `ModelInUseError` to 409 is `docs/dev/backend-infrastructure.md`.

### Eviction and unloads

**Explicit unload methods** — two public async methods allow callers to free VRAM on demand:

| Method | Effect |
|---|---|
| `await model_manager.unload(model_id)` | Acquires per-model lock, moves weights to CPU, deletes model + processor objects, removes entry, calls `torch.cuda.empty_cache()`. A no-op for an id that is not resident. **Raises `ModelInUseError` while the entry is leased** — `main.py` maps it to 409. |
| `await model_manager.evict_all()` | Calls `unload()` for every registered model + final `cuda.empty_cache()`, **skipping** a leased one rather than failing. Returns only the IDs it actually unloaded; `leased_ids()` names what stayed. Used by `POST /api/v1/models/unload-all` and the caption pipeline between steps. |

**`POST /api/v1/models/unload-all`** (router: `backend/routers/models.py`, prefix `/models`) — evicts every *idle* ML model from VRAM without restarting. Returns `{ "status": "ok", "unloaded": [model_id, ...], "in_use": [model_id, ...] }` — `in_use` being the leased models it left resident. Its docstring says to call it after quality scoring; **nothing does**, frontend or backend, and that is deliberate rather than an omission. Scoring frees its own models now, and it must free *only* its own — see below.

**Scoped unloads, by subsystem.** Two subsystems free VRAM without going through `evict_all`, each covering the models it loaded and nothing else:

| Caller | Scope |
|---|---|
| `DELETE /captioning/model/{id}/unload` | The one captioning model the page names. 409 while a job holds it; the page's Unload button is disabled during a run. |
| `POST /quality/models/unload`, and the auto-unload in the `quality_score` job's `finally` | The four ids in `routers/quality.py::SCORING_MODEL_IDS`. Gated by the `auto_unload_after_scoring` setting for the job; the endpoint is unconditional. |

Prefer one of these over `evict_all()` in anything new: a scoring run that swept the registry would evict a Florence-2 or SAM2 someone loaded for other work. `evict_all` remains right for the caption *pipeline*, which is a multi-step job that owns the VRAM for its whole duration. See `docs/dev/quality/scoring.md` § The model lifecycle.

**The VRAM budget setting is dead code, and has been.** `settings.max_vram_mb` never reaches `ModelManager`: `model_manager = ModelManager()` at the bottom of `model_manager.py` passes no argument, so `_evict_lru` compares against the class default of 20000 and the user's setting is inert. That is the defect — **not** that eviction never runs. It does: `_used_vram()` sums every resident entry, so JoyCaption's `_evict_lru(17000)` fires the moment anything else is loaded (with `aesthetic`'s 3500 resident, 3500 + 17000 > 20000), and this branch's fifth resident model makes it more reachable still. Both latent bugs that used to sit behind that are fixed: eviction can no longer select a model a running job is using, because every consumer now holds a lease that `_evict_lru` skips (§ Leases), and the dict-shaped `.cpu()` that skipped the move to host memory is gone (§ Releasing an entry's weights). Wiring the setting through is still **its own branch** — it changes the ceiling for the first time, and a lower ceiling now means a load can find nothing evictable while a job holds its models, so that branch has to decide what an over-budget load does.

### Leases

A model a job is using must not be released under it: `unload` runs `del entry.model`, and before leases an unload mid-run — the Captioning page's Unload button stayed live during a run — failed every image the job had left, with a device mismatch on the one in flight and `AttributeError` on the rest (GPT-032; PM-030). `ModelManager.lease(entry)` is a sync context manager holding a **counted** lease (`ModelEntry.leases`; `in_use` is the read-only `leases > 0`). While any is held, `unload` raises `ModelInUseError`, `evict_all` skips the entry, and `_evict_lru` never picks it. The check in `unload` and the increment in `lease` share `_sync_lock`, so no lease can slip in between.

**Take the lease immediately after the `load_*` that returned the entry, with no `await` in between**, and hold it for as long as anything reads `entry.model`. An entry that left the registry first — an executor thread's `_evict_lru` can evict between a loader's return and the caller's lease — is marked `released`, and `lease` refuses it with a `RuntimeError`, so that race fails once up front rather than on every image. Scope, by consumer:

| Consumer | Lease scope |
|---|---|
| Caption `/run`, detection batch job, LaMa `batch_inpaint` | The whole job: an `ExitStack` around the body, entered after each load. LaMa needs the whole job because `inpaint_image_sync` re-reads the registry per image and raises when the entry is gone. |
| Caption pipeline | Per step, closed **before** the step's `evict_all`, which would otherwise skip the model it is there to free. |
| `quality_score` | Per stage (each `score_*`/`extract_*` call). A whole-job lease would stop LRU freeing `aesthetic` while `dino` loads and raise peak VRAM. All released before the job's `finally` auto-unload. |
| Manual box, mask refine, `embed-references`, tag consolidation | Around the inference call only. |
| Upscaler | Per image, taken inside the `_sync_lock` block that looks up or inserts the entry — `lease` takes that lock itself and a `threading.Lock` is not reentrant, so this one site uses the two private halves `_acquire_locked`/`_release_lease`. It reloads on a miss, so a per-image lease is enough. |

Internal callers never see the refusal as a failure: `evict_all`, the scoring job's `finally` and `POST /quality/models/unload` all catch `ModelInUseError` per id, skip that id and report it as `in_use`.

**A hard cancel can outlive a lease, harmlessly.** Only `JobQueue.stop()`'s `task.cancel()` can end an `await run_in_executor` while its thread is still running inference, so the lease exits before the thread does. `.cpu()` swaps `param.data`, so the thread's own references stay valid, and the process is exiting anyway. A user cancel is cooperative and lands between awaits, never under one.

### Releasing an entry's weights

**`unload` and `_evict_lru` free an entry through `_release_to_cpu`, because `ModelEntry.model` is not always a module.** Several loaders store a **dict** of tenants — `aesthetic`'s `{"clip", "mlp", "preprocess"}`, `nsfw`'s `{"model", "processor", "nsfw_idx"}` and `sam2`'s (below) — so the `entry.model.cpu()` both call sites used to do raised `AttributeError` on the dict, straight into a swallowing `except`. `_release_to_cpu` walks a dict's values and tries each independently (a dict entry mixes weights with a transform and an int, neither of which has `.cpu()`, and one of those must not abort the loop), and passes a plain entry through unchanged.

**`sam2`'s entry carries every model it uses as a tenant.** `SAM2ImagePredictor` is not an `nn.Module` — its network is `predictor.model` — so `predictor.cpu()` raises and is swallowed; the dict therefore holds `{"predictor", "sam2_model", "device"}`, and `sam2_model` is what actually moves SAM2's weights. Grounding DINO used to sit in a module-global `_gdino_cache` that no unload path could see, so "Unload all" reported `sam2` freed while ~0.7 GB stayed resident for the life of the process (GPT-034). `sam2_predictor._ensure_gdino(model_dict)` now loads it lazily **into the same dict** (`gdino_model`, `gdino_processor`, `gdino_device`; `_gdino_lock` only serialises the load), so `_release_to_cpu` frees it with no unload hook and the sam2 lease covers it too. A future model loaded "on the side" of an entry belongs in that entry's dict for the same reason.

**"The `del` + `empty_cache()` reclaim it anyway" was the wrong model, and it shipped a leak.** That reasoning holds only when the `del` drops the *last* reference before the flush runs, and an unload cannot know that it does. The `quality_score` job binds `handle = entry.model` — a second reference to `aesthetic`'s dict — and its `finally` runs the auto-unload while that local is still live. So `del entry.model` dropped one reference of two, `empty_cache()` had nothing to hand back, and the frame died a moment later with no second flush to follow: ~3.5 GB stayed with the process until it exited. The fix is both ends. `_release_to_cpu` moves the weights regardless of who else holds them, and `_run` clears `handle`/`entry`/`dino_entry`/`nsfw_entry` before awaiting the unloads so the flush has nothing live to skip.

**Test this at the entry shape, not at the id.** `backend/tests/test_quality_model_unload.py` originally monkeypatched `unload` itself and asserted on the ids it received, which is why the leak passed CI: the ids were right and nothing was freed. The three cases added with the fix drive the **real** `unload` over a dict-shaped entry of fakes and assert each tenant was moved — including one end-to-end through the scoring job. A stub of the function under test cannot see this class of bug.
