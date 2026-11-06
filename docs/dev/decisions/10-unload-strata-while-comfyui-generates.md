# Decision 10: Unload a local Strata server while ComfyUI generates

*Index: [decisions.md](../decisions.md).*

- **Date:** 2026-11-06.
- **Status:** **Decided** (2026-11-06, by the user) for the direction: Crucible frees the
  GPU from a local Strata server around ComfyUI work. **Leaning** for the mechanism below,
  which was settled by measurement after the ruling.
- **Decision:** with a new Settings → ComfyUI option on (`llm_gpu_handoff`, off by default),
  Crucible sends a local Strata server `POST /v1/unload` before every ComfyUI generation (a
  run's `handoff_to_comfy`, the start of every `comfy_generate` job) and in a run's
  `free_*_model` stages. It never sends a load: Strata reloads on the next chat request.
  Detail: `docs/dev/comfyui/gpu-handoff.md`.
- **Rationale:**
  - On a 16 GB card Strata takes 15.6 GB, so it and a ComfyUI generation cannot run
    together, and the user wants captioning and prompt writing (Strata) and generation
    (ComfyUI) on one such machine.
  - Measured on a 16 GB card (2026-11-06): the unload frees the card to 0 MiB in about 2 s and
    keeps the server running; a reload takes about 40 s warm and happens by itself on the
    next request. An explicit load step would have to know when the next LLM step comes;
    the implicit one needs nothing but a long enough provider timeout.
  - Off by default: on a card with room for both, unloading only costs a reload, and other
    users' setups are unknown.
- **Rejected alternatives:**
  - **Stopping and starting the Strata process** (`strata launch --stop`, then a launch).
    It needs a command line configured on the server, a 2-minute cold start on every pass,
    and an app with no authentication that runs processes. The HTTP unload does the same
    job without any of it.
  - **A smaller Strata that fits beside ComfyUI.** Slower on every request, and still
    starved while ComfyUI paints near 16 GB.
  - **Keeping LLM steps out of runs that generate on such a machine.** It would work today, but a
    run's rewrite and caption stages are the point of M6.
- **How it's verified:** `backend/tests/test_llm_gpu_handoff.py` against a fake Strata; by
  hand on a 16 GB card, a small run with rewrite, generate and caption, watching `nvidia-smi`.
