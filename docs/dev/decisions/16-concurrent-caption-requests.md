# Decision 16: Remote caption requests run several at once

*Index: [decisions.md](../decisions.md).*

- **Date:** 2026-11-12.
- **Status:** **Decided** (2026-11-12, by the user): the setting's shape, both loops, and
  the crash undercount of up to N calls. **Leaning** on counting every started request when
  a job fails or is hard-cancelled, which the user can still overrule.
- **Decision:** M10 T2 lets a caption job keep up to N requests in flight to a remote
  captioner, and reuses one client for the job.
  - **An OpenAI-compatible provider** gets its own setting, `max_concurrent`, on its
    provider row.
  - **Ollama has no provider row**, so it gets one app-wide setting,
    `ollama_max_concurrent`, on the settings row.
  - **Both default to 1**, today's one request at a time, and are bounded at 16.
  - **Both caption loops use it**, `/run` and `/pipeline`, through one shared helper,
    `utils.InOrderCalls`, so the near-copies do not drift.
  - **Local captioners stay at one** request at a time.
- **The paid-call cap under N:**
  - **Within a job there is no overshoot.** The cap trims a caption stage's images before
    the job starts (`docs/dev/comfyui/run-budgets.md`), and the job makes one call per
    image.
  - **A user's Stop counts every call.** It sends nothing new and waits for the requests in
    flight, which are saved and counted as they finish.
  - **A failed or hard-cancelled job counts every request it started**, the ones still in
    flight included (`_record_calls_started`). This counts high, never low, matching the
    accepted rule that a call which never left the machine is still counted.
  - **Only a process death can leave calls uncounted**: at most N, against 1 before. A
    resume after that can overshoot the cap by at most N.
- **Rationale:**
  - A hosted API answers requests in parallel, and a local server with parallel slots (LM
    Studio, llama.cpp `--parallel`, vLLM, Ollama with `OLLAMA_NUM_PARALLEL`) can batch
    requests that arrive together. One request at a time leaves both idle.
  - Servers differ, and a server with one slot must keep working unchanged, so the default
    stays 1 and the user raises it per provider.
  - The finish of each image (the count, the save, the rename) stays serial and in input
    order, so the count stays exact, the progress row and event stay one unit (PM-008), and
    rename-on-caption picks the same names as before.
  - The crash undercount only matters for a billed provider, only on a crash or kill, and is
    bounded by a number the user chose.
- **Rejected alternatives:**
  - **Count a call when it is sent rather than when it finishes.** That leaves nothing
    uncounted on a crash, but the progress bar would run ahead of the captions actually
    saved. The user chose the bounded undercount instead.
  - **Ollama reuses one client but stays at one request.** Smaller, but Ollama with parallel
    slots would gain nothing.
  - **Concurrency for `/run` only.** The pipeline's copy of the loop would drift further
    from `/run`'s.
  - **Retry a 429 automatically.** PM-023 set `max_retries=0` so the configured timeout is
    honest. A 429 instead gets its own headline telling the user to lower the setting.
- **How it's verified:**
  - `backend/tests/test_in_order_calls.py` covers the helper: input order, the bound, a
    Stop, and a cancel.
  - `backend/tests/test_caption_concurrency.py` covers both loops: in-flight counts, Stop,
    a failed save's count, rename names, one client per job, and 429.
  - `backend/tests/test_run_paid_calls.py` covers the cap with N above 1.
  - The hand checks against a real provider are in `docs/dev/captioning.md`
    § Requests in flight.
