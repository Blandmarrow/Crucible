# ComfyUI blueprints: the LLM calls

**Status: living.** The LLM side of structured generation: the outline call, which turns a
brief into a draft blueprint (the `comfy_outline` job), and the optional rewrite pass
(the `comfy_rewrite` job, M6 §3). Both go through `backend/ml/prompt_generator.py` and share
its `json_schema`-then-plain fallback and the PM-004/PM-005 outcome rules with prompt
generation (`docs/dev/comfyui/prompts.md`). What a draft must hold is
`docs/dev/comfyui/structured-expansion.md` § The spec; the blueprint, Apply and Top up are
`docs/dev/comfyui/structured.md`; the review panel is `docs/dev/comfyui/page.md`. The
family's hub is `docs/dev/comfyui.md`.

## The outline call

**One LLM call turns a brief into a draft blueprint**: `POST …/blueprint/outline`
(`{provider_id, model_name, brief, temperature, label}`, all bounded) queues a
`comfy_outline` job.

**Its own job type,** because it writes no rows. It never conflicts with a run or a prompt
job on the same plan, only with a second outline job: the 409 is
`_plan_job_active(…, ("comfy_outline",))`. Pre-flight matches `generate_prompts_job`:
404 for the provider, 400 for the model. `_run` re-checks both on dequeue and raises
rather than returns, since a silent return reads as success. One emit goes out before the
call, carrying `plan_id`, so TopBar can tie the job to its plan.

**The LLM function** is `ml/prompt_generator.generate_outline`. It makes a `json_schema`
attempt with the mandatory plain fallback, the same rule and reason as `generate_prompts`
(`docs/dev/comfyui/prompts.md`).
- The schema asks for axes and variations as **lists of named entries**, because a dict
  with free-form keys is awkward under a strict schema. `parse_outline` turns them into
  the spec's dicts. It also accepts the dict form, a `{"blueprint": …}` wrapper, `<think>`
  blocks, fences and surrounding prose.
  - **Prose is scanned with `raw_decode` at each `{`**, never cut from the first `{` to
    the last `}`. Prose in this domain is full of `{axis}` placeholders, which make that
    slice invalid JSON.
  - A `null` section reads as empty. Two entries with one name merge rather than the
    second replacing the first, which would silently halve an axis.
  - **An outline with no axes and no scenarios is None**, so the plain fallback runs and
    the job then fails (PM-004). Counted as a draft, it would report "draft ready" and let
    *Use it* replace the user's spec with nothing.
- The `<think>`/fence stripping is `_strip_reasoning`, shared with `parse_prompts`.
  **`ParsedPrompts` is untouched** (PM-005).

**The prompt layout.** The **brief goes in the user message**, with the instruction to
write descriptors that can be pasted verbatim. The plan's saved "Generate prompts"
instructions (`gen_settings.instructions`) are passed as standing rules for the templates.
They are **read, never written**: the user owns them.

**The system message asks for labels as written names** ("Sir Alric", not `knight_alric`).
With only "a short display name" to go on, Qwen3 copied each entity's id into its label
(2026-11-04). A label is shown in the editors, read into prompts by `{axis.label}`, and
slugged into the folder name by `blueprint_expand._slug`, so the id-shaped one was worse in
the first two places and gained nothing in the third.

**Standing instructions go before the format mandate, in every call.** One helper,
`_with_standing`, builds the system message for prompts, the outline and the rewrite:
the user's instructions first, the mandate last, so the mandate wins on conflict. The
outline once put them last, so an instruction such as "one prompt per line" could beat
"return ONE JSON object" and fail the draft with an error that never said why.
`test_prompt_rewrite.py` asserts the order.

**What is saved.** The result is validated as a `BlueprintSpec` **structurally only**
(`docs/dev/comfyui/structured-expansion.md` § The spec).
- A draft the schema cannot hold fails the job. The message names the deepest error
  location with Pydantic's union-branch tags removed (`scenarios.0.combine.0.take`, not
  `…constrained-str`).
  - The tags are an exact set, `_UNION_BRANCH_TAGS`. A pattern such as "starts
    uppercase" would also drop a user's invalid key (an axis named `Character`), the very
    thing the message must name.
- A draft with semantic errors is saved, and the review panel shows them.
- **The brief is saved when the job is queued**, creating the blueprint (empty spec,
  random seed) if needed, so a failed draft never costs a retype.
- The draft goes to `blueprint.draft`. **The spec is never touched.**
- `result_data` is `{plan_id, axes, scenarios}`.
- **Cancellation is polled once, after the call**, the only slow step. A Stop pressed
  during it saves no draft and ends the job `cancelled` (`raise_if_cancelled`). The call
  itself is not aborted; as with prompt generation, it runs to the provider's timeout.

**Using or discarding the draft.** `DELETE …/blueprint/draft` clears it (idempotent) for
both *Use it* and *Discard*. *Use it* clears it only after the spec holding it is saved
(`docs/dev/comfyui/page.md`), so a failed save never loses the draft.

There is no LLM in CI. `backend/tests/test_blueprint_outline_http.py` replaces
`generate_outline` (imported lazily in `_run`, so patching the module attribute reaches
it), and `backend/tests/test_parse_outline.py` covers the parser. A real provider is
verified by hand.

## The rewrite pass

**An optional pass that rewrites a blueprint's template prompts into prose**, for image
models that read sentences. Off by default, switched on per scenario (`Scenario.rewrite`).
The outline schema leaves the flag out, so the LLM never sets it. It is its own job type,
`comfy_rewrite`, not `comfy_prompts`: that job appends rows and reports "N generated",
while this one updates rows and reports different numbers.

**Which rows it touches.** `blueprint_rewrite.select_targets` picks rows that:
- belong to a current leaf whose scenario has `rewrite` on, by the *saved* spec
- have `prompt_origin = 'template'` (`docs/dev/comfyui/queue.md` lists its writers)
- have not completed, and hold a prompt value

**The job rewrites only the scenarios the user reviewed.** The route stores them in the
job's `config["scenarios"]` and `_run` narrows to them. Otherwise a scenario switched on
while the job waited in the queue would be rewritten against a budget sized without it.
A leaf matches by the scenario name stored at the last Apply, so a scenario renamed since
has no targets (the dialog reads "Apply first") until it is applied again.

A row the user edited is `manual`, so **the pass never overwrites a hand edit**. A failed
row is rewritten and reset to pending, as a hand edit resets it.

**`write_rewrites` re-checks every row in its UPDATE.** The WHERE clause asks again for
`template` and not completed, so an edit or a run that landed while the call was out wins,
and the pass counts only rows it actually wrote. `values` is copied, never mutated in place,
and keeps every other key.

**The row routes that write `values` 409 while a rewrite is live**: `PATCH /rows/{id}` with
`values`, `rows/set-value` and `rows/bulk-edit` (`_refuse_during_rewrite`). Two reasons:
- The table PATCHes a whole row from its cache. An edit to any cell during the pass would
  put back the template text the pass had just replaced, then mark it `manual`, so it would
  never be rewritten again.
- The pass reads `values` and UPDATEs it in separate statements, so a write landing
  between them would be lost.

A folder-only PATCH writes no values and goes ahead.

**The LLM side is `RewriteSession`**, a caller object that owns one client for the run.
Probe state lives on it, never on `ParsedPrompts` (PM-005), and `generate_prompts` is
untouched.
- **The schema probe runs once.** With the mode unknown, it tries `json_schema` first. A
  refusal of it, or content that does not parse, gets one plain request in the same attempt.
  Whichever request parses fixes the mode for the rest of the run, so LM Studio with a
  thinking model costs two requests once, not on every batch. **A refusal alone never fixes
  the mode:** a 400 for one leaf whose context overflows says nothing about the schema.
- **Its parser is strict: `parse_rewrite`, never `parse_prompts`.** It accepts only a JSON
  list of non-empty strings, bare or as `{"prompts": [...]}`, then demands one per input.
  - `parse_prompts` falls back to one prompt per line. A reply cut off at the token limit
    (`{"prompts": ["An iron knight stands in the`) is one line for one template, so it
    would have been written as the prompt and marked `rewritten`, never to be retried.
  - The scan skips past every value it decodes, so a list nested in an off-schema object
    (`{"rewritten": [...]}`) is no answer either.
  - **`finish_reason == "length"` fails the attempt whatever the count.**
- **Errors are classified by duck typing**, from `status_code` and the class names in the
  hierarchy, because `openai` is not in `requirements-ci.txt`:
  - `transient`: a timeout, a dropped connection, a 429 or a 5xx
  - `mismatch`: no JSON list, or the wrong number of prompts
  - `rejected`: any other 4xx, which is about this request (a 400, 413 or 422)
  - `fatal`: a 401, 403 or 404, a token limit (whose message says to raise max tokens), or
    an error with no status
- **`max_requests` is a hard stop checked before each HTTP request**, so the budget can
  never be overspent, even by a probe's second request.

**The loop is `blueprint_rewrite.run_pass`**, one leaf at a time (no concurrency, by the
user's ruling). It takes its session, writer, cancel flag and clock as arguments.
- **A call is one HTTP request**, retries included, since that is what the user pays for.
  - Per leaf, the cap is ceil(rows / batch) + 2. It is checked before each attempt, and a
    probing attempt sends two requests, so a leaf can run one over. The run budget cannot:
    `RewriteSession` checks it before every request.
  - The run's budget is the expected calls plus a quarter, at least two.
- **A transient error is retried twice**, after 2 s × 2^k × a jitter of 0.5–1.5, capped at
  30 s. **The sleep runs in half-second slices that poll the cancel flag**, and the flag is
  polled before every attempt.
- **A mismatch is asked again at once**, with no backoff, and the leaf cap bounds it.
- **A rejected request fails its leaf** without a retry. The next leaf may well pass.
- **A fatal error stops the pass** (`provider_error`): the next leaf would only repeat it.
- **A leaf that fails keeps its template prompts**, and the pass moves on. More than
  max(2, leaves // 4) failed leaves stop it (`too_many_failures`).
- **A failed write stops the pass** (`write_error`), a locked database say. What is already
  committed stays, and the result is still recorded rather than lost with the exception.

**The routes.**
- `GET …/blueprint/rewrite?batch_size=N` returns `{rows, leaves, calls, budget}`.
- `POST …/blueprint/rewrite` takes `{provider_id, model_name, batch_size, temperature,
  confirm_budget, label}`.
  - **400:** no prompt pin, the model, or nothing to rewrite. **404:** no blueprint, or the
    provider.
  - **409, under `_plan_lock`:** a live `comfy_generate`, `comfy_prompts` or `comfy_rewrite`
    job (`_ROW_JOB_TYPES`), or a budget other than the one the user reviewed.
- **`comfy_rewrite` is in every row writer's guard**: Apply, Top up, `run_plan` and
  `generate_prompts_job`. A run loads its rows when it starts, so the two may never overlap.
- **`_run` follows `docs/dev/comfyui/prompts.md`'s rules.** It re-checks the plan, pin and
  provider on dequeue and raises on a miss. It holds no session during a call. One emit
  carrying `plan_id` goes out before the first call. Its budget is the confirmed one, or less
  if rows went while it waited.
- **Outcomes.** `result_data` is `{plan_id, rewritten, requested, leaves, leaves_done,
  leaves_failed, failed_paths, calls, budget, mode, stop_reason, error}`, and `total_items`
  is set to `rewritten`. `leaves_done` is counted, never derived as `leaves - leaves_failed`:
  a run that stops early leaves some leaves neither.
  - Nothing rewritten fails the job (`Prompt rewrite failed: …`, PM-004).
  - A partial result completes.
  - On cancel, `result_data` is committed before `raise_if_cancelled`.

**What it does to Top up and the preview** is `docs/dev/comfyui/structured.md` § Top up.

The tests inject fakes throughout:
- `test_prompt_rewrite.py`: the session against a fake client
- `test_blueprint_rewrite.py`: the loop against a fake session and clock
- `test_blueprint_rewrite_http.py`: the job, with `prompt_generator.RewriteSession`
  patched, and `write_rewrites` against the real database. A fake writer cannot test the
  UPDATE's WHERE, which is the re-check.
