# ComfyUI blueprints: the LLM calls

**Status: living.** The LLM side of structured generation: the outline call, which turns a
brief into a draft blueprint (the `comfy_outline` job), and, once M6 §3 lands, the
optional rewrite pass. Both go through `backend/ml/prompt_generator.py` and share its
`json_schema`-then-plain fallback and the PM-004/PM-005 outcome rules with prompt
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

