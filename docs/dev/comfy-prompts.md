# ComfyUI prompt generation (LLM)

Writing ComfyUI queue prompts with an LLM: the one-shot endpoint, the durable
`comfy_prompts` background job, the output parser, and `GeneratePromptsModal`. The queue
itself (plans, pins, rows, running) is `docs/dev/comfyui.md`.

### Generating (`backend/routers/comfy.py`, `backend/ml/prompt_generator.py`)

`POST /comfy/generate-prompts` (`{provider_id, model_name?, system_instructions?, instruction,
batch_size≤10, existing, temperature}`) makes **one** LLM batch call via
`ml/prompt_generator.py` (text-only `AsyncOpenAI` sibling of the captioner; not
model-manager-tracked) and returns `{prompts}` — `existing` is the anti-similarity context
(the model is told those already exist and to diverge).

- **Structured output**: enforced decoder-level via `response_format: json_schema` **with a
  mandatory plain-call fallback whenever that attempt errors OR parses to zero prompts** — LM
  Studio + Qwen3 (thinking) returns *empty content* under a schema constraint, so error-only
  fallback is not enough. `max_tokens` is floored at 8192 (the provider's captioning-tuned
  value truncates thinking models mid-reasoning; a zero-prompt `finish_reason=="length"`
  raises a "raise max tokens" hint).
- **`parse_prompts`** (unit of truth for splitting) returns a `ParsedPrompts(prompts, filtered)`
  NamedTuple: it strips closed AND unclosed `<think>` blocks, then prefers JSON (bare array or
  `{"prompts": []}`), falling back to line splitting with list-marker/commentary stripping.
  Filtering is **line-split-branch only** — a JSON array element is a deliberate unit, so
  filtering there would make the reliable path lossy.
- **Two regexes, both precision-first**: `_COMMENTARY_RE` (openers; single words must be
  followed by whitespace/end, so `sure-footed mountain goat` is not chatter — a bare `sure\b`
  matches the hyphen) and `_META_LINE_RE` (self-referential lines, requires `prompt(s)` to
  co-occur, applied **only to the first and last surviving line**, since a bare leading "these"
  would reject `these towering cliffs at dawn`).
- **Why precision over recall**: under-filtering used to be visible and reversible in the
  review textarea, but the `comfy_prompts` job inserts rows directly, so an over-filter
  silently discards a paid generation — hence a `filtered` count in `result_data` and
  mandatory tests (`backend/tests/test_parse_prompts.py`, which asserts the two lines above
  *survive*).

### The `comfy_prompts` job

The durable "generate until N": a background job that loops batches and **commits rows per
batch**, so closing the modal or navigating away no longer discards calls already paid for.
That is the whole point — the old client-side loop kept its working state in component state,
so unmounting mid-loop threw away LLM calls that had already been billed.

`POST /comfy/plans/{plan_id}/generate-prompts` (`GeneratePromptsJobRequest`: the sync body's
fields plus `target_count 1..200` and `use_existing_context`, minus `existing`). Every field is
bounded because `model_dump()` is persisted into `config`, which `JobOut` returns verbatim —
the provider `api_key` must never reach it. `plan_id` is injected manually (path param, so
`model_dump()` omits it) because the 409 guard reads `config["plan_id"]`.

`target_count` is **absolute** ("until the plan holds N prompts"), making a resume after a stop
idempotent. Pre-flight, each a real HTTP error rather than a job that dies minutes later: plan
404, prompt pin 400 (otherwise a full run can burn and then insert nothing), provider 404 /
model 400, per-plan 409, and `existing >= target_count` → 400. The 409 is scoped by **both**
`job_type` and `config["plan_id"]`: a live *run* on the same plan, or a prompt job on another
plan, must not block it (`backend/tests/test_conflict_paths_http.py` pins both directions —
each 409 is paired with a case that has to fall through to the next 400).

**"Existing" means *effective* prompts, not rows with a stored value.** The count comes from
`_plan_prompt_texts` → `effective_prompt` (row value → pin run default → workflow template), so
a row with empty `values` still counts whenever the prompt pin's default or the template node
supplies text — which is also what that row would actually render. Anything displaying or
gating on "how many prompts the plan holds" must use this number; see the Frontend section.

`_run` re-checks plan/pin/provider on dequeue (they can change during a queue wait) and
**raises** if gone — a bare `return` is reported as success. It seeds a case-insensitive `seen`
set from the plan's existing prompts *and* everything generated this run (the old client loop
deduped only against the textarea); `use_existing_context` now controls only what the LLM is
*shown*. Same call cap as before (`max(12, ceil(to_generate/batch) × 3)`). Rows commit per
batch via `_next_sort_order`, then progress is emitted — plus one emit **before** the first LLM
call, which can take up to the provider's `timeout_s` (default 300 s) and is the first event
carrying `plan_id` (and `requested`), which
TopBar's invalidation depends on. `total_items` is set to `created` at the end, since the
worker re-reads it and emits it as both `done` and `total`; the shortfall lives in
`result_data` (`{created, requested, filtered, calls, stop_reason, plan_id}`).

Outcome discipline (PM-004 — a capability claimed from a proxy signal; here `_run` returning
without raising, when `generate_prompts` returns `[]` without raising on unparseable prose):

- `created == 0` → **fail**, with a reason derived from `stop_reason` and the same
  `"Prompt generation failed: "` prefix as the sync path's 502.
- `0 < created < requested` → **completed** plus a loud shortfall toast. The rows exist;
  failing would imply they don't.
- Provider error with `created > 0` → stop and complete. Discarding good rows because call 8
  of 10 timed out is the worse error.
- Cancel follows the house idiom (`docs/dev/backend-infrastructure.md`): `cancel_requested` →
  `break` → commit rows + `result_data` → **then** `raise_if_cancelled`, since a plain return
  lets the worker overwrite the cancel with `completed`. Checked twice per iteration — before
  a new paid call, and after committing a batch (a Stop during a call still yields paid-for
  prompts). The post-commit check is guarded on `created < total`: a Stop landing on the
  iteration that reaches the target is a **completed** job, not a cancelled one, and reporting
  it as "stopped — N kept" would understate a run that did everything asked.
- **A Stop waits out the batch in flight, and that batch is kept.** Cancellation is polled
  between calls, so Stop can take until the provider returns (its `timeout_s`, default 300 s) and the
  prompts from that call are then committed. This is a decision, not a limitation: aborting the
  call is possible (run `generate_prompts` as a task, cancel it on the flag — `AsyncOpenAI` is
  httpx-based and drops the connection), and it was considered and **declined** in favour of
  never discarding a generation already paid for in GPU time. Don't re-open it without new
  evidence that the wait actually hurts; the UI covers the gap by saying so ("Stopping…", plus
  the hint that the in-flight batch is kept).

**Trade-off, chosen deliberately**: this runs on the shared single-worker `job_queue`, so
prompt generation now serialises behind captioning/ComfyUI runs — unlike the old client-side
loop, which ran outside the queue. Bounded by `target_count ≤ 200` and the call cap, and
cancellable from TopBar (which the old loop was not, once the modal closed). A dedicated queue
for network-bound jobs is the known escape hatch if this proves painful; don't re-litigate it
without that.

### Frontend (`GeneratePromptsModal.tsx`, `ComfyPage.tsx`, `TopBar.tsx`)

`GeneratePromptsModal` — provider select + `ModelPicker`; two text fields: standing
*Instructions* (HOW prompts are written → `system_instructions`, persisted per plan in
localStorage via `loadPersisted`, with provider/model/batch/temperature/target) and the
per-call *Request* (WHAT to generate). Two paths, deliberately different in kind: *Generate N
more* = one sync call appending to the review textarea → *Add N rows* → `rows/bulk` (sends
queue prompts + textarea lines as diverge-from context); *Generate until N* = the
`comfy_prompts` job, which writes rows itself.

- **The queue's existing prompts come from `GET /comfy/plans/{id}/prompts`** (`listPlanPrompts`,
  keyed `["comfy","prompts",planId]` as in `PromptLibraryModal`), never derived from rows in the
  client. They feed both the diverge-from context and the "N in queue" count that gates
  *Generate until*. **Invariant: the number shown here and the server's `existing >=
  target_count` gate must be the same number.** They were not — the client counted only rows
  with a stored value while the server counted effective prompts, so with a prompt pin default
  or template text the modal read "0 in queue", enabled the button, and the POST came back 400.
  The query sets `staleTime: 0` (overriding App.tsx's global 30 s) so every open re-reads it;
  `ComfyPage`'s bulk-add and `TopBar`'s per-batch invalidation cover the two windows in which
  the count can move while the modal is open.
- **Closing is never blocked.** ×/Cancel/backdrop all go through `handleClose`, which aborts an
  in-flight sync batch via `AbortController` (threaded into `comfyApi.generatePrompts` as
  `signal`) and closes. Gating close on the sync call — as this once did — locks the modal for
  up to the provider's configured timeout (default 300 s) with no escape and no cancel, which is
  worse than losing a
  batch the user chose to walk away from. An `axios.isCancel` error is a close, not a failure,
  so it is not toasted. *Generate until N* is unaffected: outliving this modal is its point.
- `jobBusy` (job running or starting) drives the tooltips; `jobBlocks` adds the prompts query's
  loading state, since acting on a not-yet-loaded count starts a run the server rejects.
- The sync path and *Add N rows* ARE disabled while the job runs: `_run` seeds its dedupe set
  once at start, so rows inserted mid-run would be invisible to it and get duplicated.
- The attached job is **derived** from `jobStore` (scan for a live `comfy_prompts` job whose
  `plan_id` matches, falling back to the id the start mutation returned — the queue's own
  pending/running events carry no `plan_id`), never mirrored into state.
- **No `useJobSSE` here.** `useAllJobsSSE` is mounted once in TopBar and never unmounts, so
  `jobStore` already holds every event; a modal-scoped subscription would re-create exactly
  the coupling this feature removes. Don't "fix" this by adding one.
- Re-attach after a hard reload (which empties `jobStore`) uses a per-plan persisted job id
  plus a one-shot `jobsApi.get` that re-seeds the store; a `failed` job from
  `mark_interrupted_jobs` is reported as "ended early, n prompts kept" — after a restart the
  rows really are there.
- While running: `JobProgressBar` (props are only `{message, percent}` — don't extend it, it
  is shared with versioning). The modal is keyed by `plan.id` so per-plan state initializers
  re-run on a plan switch.
- **Stop is NOT the optimistic-cancel idiom TopBar uses for its own button.** It sets a local
  `stopping` flag and waits for the server's terminal event. Cancellation is cooperative and
  only checked between LLM calls, so at Stop time a paid batch is almost always still in flight
  and *will* be committed (`_run` commits it, then breaks). Flipping `jobStore` to `cancelled`
  optimistically fired TopBar's terminal toast right then with the count as of that instant —
  **"stopped — 0 prompts kept"** — and `promptTerminalRef` then suppressed the real terminal
  event, so the rows that did land were never accounted for and the toast contradicted the
  queue. The server's terminal event carries the true count (the worker's `cancelled` emit has
  no `done`, so `jobStore`'s merge preserves the last `_emit`); let it do the talking. The
  button reads "Stopping…" meanwhile, because a whole batch can still land.

`ComfyPage` owns the start mutation, passing `planId` through the **variables** (as
`runMutation` does) and no invalidation of its own. **Cache invalidation and the outcome
toast live in `TopBar`**, in a dedicated branch — not `LIVE_IMAGE_JOB_TYPES` /
`IMAGE_MODIFYING_JOB_TYPES`, since this job creates no images and those invalidations would
all be pointless. A page-level watcher dies on unmount, which is exactly what this feature
exists to survive. It invalidates `["comfy","rows",plan_id]`, `["comfy","prompts",plan_id]` and
`["comfy","plans",dataset_id]` on advance and on terminal, and toasts the outcome (success /
shortfall / failure / stopped).
