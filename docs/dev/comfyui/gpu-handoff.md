# ComfyUI — the Strata GPU handoff

**Status: living.** How Crucible frees the GPU from a local Strata LLM server before
ComfyUI generates (M6 §9, [decision 10](../decisions.md#10-unload-a-local-strata-server-while-comfyui-generates)).
The run stages it hooks into are `docs/dev/comfyui/run-stages.md`; the provider rows it
reads are `docs/dev/llm-providers.md`.

## Why it exists

Strata (an OpenAI-compatible server tuned for mixture-of-experts models) sizes its expert cache to the whole card it starts
on. On a 16 GB card (measured 2026-11-06) it holds 15.6 GB, so it and
a ComfyUI generation cannot share the card. On a card with room for both (24 GB
and up) unloading would only cost a reload, so the behaviour is a setting, off by default.

## What Strata offers, measured

- `GET /health` answers `{"service": "strata", "loaded": <bool>, …}`. That is how a provider
  is recognised as Strata: by asking, never by its name or port.
- `POST /v1/unload?wait=1` with a JSON body (`{}`; a bodiless POST is a 415) drops the model
  in about 2 s and frees the card to 0 MiB. The server keeps answering. `wait=1` waits up to
  300 s for a request in flight; without it a busy server answers 409.
- **The next chat request reloads the model by itself** (`auto_load`), in about 40 s warm,
  and answers normally; a cold start is about 2 minutes. So Crucible never sends a load: a
  provider's `timeout_s` must cover the reload; 600 s leaves room for a cold one.

## The code

`backend/services/llm_server.py`. Every function is best effort: it returns notes and never
raises, so failing to free the GPU never fails the job that asked.

- `strata_health(base_url, api_key)` — the `/health` body if the server is Strata, else
  `None` (unreachable included). `server_root` strips the base URL's `/v1`.
- `unload_strata(...)` — the unload, returning a note.
- `free_local_llm_servers()` — reads `ThresholdSettings.llm_gpu_handoff`; when on, unloads
  every **loaded** Strata among the providers that are not `_is_remote`, deduplicated by
  server root (two provider rows can name one server). Empty list when off.

It is called from three places:

| Site | When |
|---|---|
| `run_service._handoff("to_comfy")` | A run's `handoff_to_comfy` stage; notes appended to the stage's |
| `run_service._free_llm` | `free_rewrite_model` / `free_caption_model` on a Strata provider, only when on: checked before `_unload_lmstudio`, whose API Strata lacks. Off, the setting is read first and nothing new is sent: `_unload_lmstudio`'s 404 note says the model stays loaded, as before §9 |
| the `comfy_generate` job's `_run` (`backend/routers/comfy.py`) | The start of every generation, a run's or one started by hand. After a run's handoff it costs one `/health` call |

**Our client waits 60 s for the unload, not the 300 s Strata allows** (`_UNLOAD_TIMEOUT_S`).
This runs on the single job worker, so a request in flight on the server can only be
another install's (two installs on one machine sharing one Strata); then the unload is a note and
the generation goes ahead. Sharing one Strata between installs weakens the handoff anyway:
the other install's next request reloads it, even mid-generation.

"Local" is `schemas/openai_provider._is_remote`'s: a Strata reached by its LAN address or
host name, even on this machine, is not unloaded.

There is deliberately **no load site**. The reload is implicit, so Crucible never has to
decide when an LLM step is next. One consequence: ComfyUI should have freed its own models
before Strata reloads, or Strata sizes its cache to a crowded card and runs slower. Inside a
run `handoff_to_local` does that before scoring and captioning. Outside a run nothing does:
a caption job started by hand right after a hand-started generation meets a slower Strata.

## The setting

`ThresholdSettings.llm_gpu_handoff`, migration `a3c7e9b1d5f2`, server default `0`, in
`threshold_service.DEFAULTS` and the `/settings/thresholds` schemas. Settings → ComfyUI
shows it as a checkbox that saves on change.

## Testing

`backend/tests/test_llm_gpu_handoff.py`, with a `FakeStrata` behind
`run_service._LLM_TRANSPORT`. `llm_server` reads that transport at call time, so the autouse
`_no_real_llm_server` fixture in `backend/tests/conftest.py` covers it too. The tests cover
the setting off (no request to Strata at all, in `free_local_llm_servers` and in a run), the per-server dedupe, a failing unload as a note, a
run's two unloads, and a hand-started generation. Nothing tests a real Strata; that check is
by hand on a machine with a 16 GB card.
