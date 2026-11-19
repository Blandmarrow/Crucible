# Tool calls and the tool-call probe

**Status: living.** Phase 3 of the analyst (M12): models that handle tool calls can look
things up during a reply. This file covers the stream's tool calls and the per-model
probe that decides whether a model gets tools at all (3b). The tools and their privacy
filter are [tool-registry.md](tool-registry.md) (3c); the loop in the send path is a
later PR, planned in
[../analyst-plan/README.md](../analyst-plan/README.md) § Phase 3. The send path the loop
will extend is [sending.md](sending.md); the provider rows are
`docs/dev/llm-providers.md`; the family hub is [../analyst.md](../analyst.md).

## Tool calls in the stream

`backend/ml/chat_stream.py::stream_chat(..., tools=None)`. With `tools`, the request offers
them with `tool_choice="auto"`. Without, the request carries neither key, so a provider
that was never probed sees exactly the phase 2 request.

The OpenAI stream sends a call in pieces: the id and name first, then the arguments in
fragments, each piece keyed by `index`. `ToolCallBuffer` puts them back together and
`StreamResult.tool_calls` returns them as `ToolCallOut(id, name, arguments, extra_content)`,
the arguments still raw JSON for the caller to parse. Servers differ at the edges, and the
buffer leans towards keeping two calls apart, since two calls merged into one give
arguments that are not JSON:

- A piece whose id differs from its call's id starts a new call, whatever its `index`
  says: some servers put every parallel call on index 0.
- A piece with no `index` continues the last call, unless it starts a new one: a different
  id, or, with no id, a name when the last call already has its name and some arguments
  (a server that sends each call whole in one piece). A piece that only repeats the name
  is not a new call.
- A name sent again whole is not doubled, and a name sent growing (`cru`, then
  `crucible_echo`) is replaced, not appended.
- A call that never got an id is given `call_<n>`, since the next request has to name the
  call it answers.

**Whatever else the server attached to a call is kept and sent back.** Fields beyond the
standard ones land in `ToolCallOut.extra_content`, and `as_message_call()` builds the
assistant message's `tool_calls` entry with them. Gemini's OpenAI-compatible endpoint
carries a thought signature there (`extra_content.google.thought_signature`), and Gemini 3
refuses a tool answer whose request drops it. Every request that answers a tool call
builds its entry through `as_message_call()`.

**A tool-call piece ends the first-token limit**, as text does. A reply that is only tool
calls would otherwise run on `FIRST_TOKEN_S` (180 s) throughout instead of switching to
`IDLE_S` once it has started.

## The probe

Tool support belongs to a model, not a server: one LM Studio can host a model that calls
tools well and one that writes the call as prose. So the verdict is stored per model, in
`openai_providers.tools_probe` (migration `bcbc83198eb1`), shaped
`{model: {ok, checked_at, detail}}`. `schemas.openai_provider.tools_ok(row, model)` is the
one reader: true only for a stored pass. Never probed, or failed, means the chat runs that
model without tools, as in phase 2.

`POST /providers/{id}/probe-tools` with an optional `model` (default: the provider's)
runs `services/tool_probe.py::probe`. It is one scripted round trip in two calls, the same
loop the chat will run:

1. Offer one tool, `crucible_echo(word)`, and ask the model to call it with the word
   `ember`.
2. Answer the call with a random check code, and pass if the reply repeats it.

The tool's schema is shaped like the analyst's own tools' (a nested object with no
required fields, integer bounds, an enum, a length cap), so a server that accepts the
probe's schema accepts theirs and a pass is not undone by the first real request; the
model need not fill the nested part. Gemini 2.5 Flash accepted both on 2026-11-19, and
asked for "captions of images below 512 pixels" it called `sample_captions` with
`{"filter": {"short_side_max": 512}, "limit": 3}`.

That catches every way a model can fail at tools: answering in text, calling a tool that
was not offered, arguments that are not the JSON asked for, the wrong word, calling the
tool again, or ignoring the result. Every named call is answered in the second request; a
call with no name is left out of it, since a strict server refuses one.

**A verdict or nothing.** A pass or a fail is stored, and so is a server that refuses the
first request, the one offering tools, with a `rejected` 4xx (`prompt_generator._classify`'s
term for a 4xx other than 401, 403, 404 and 429). That 4xx is not always about tools:
Gemini answers a bad key with a 400, and some servers an unknown model. So the stored
detail carries the server's own message, and a later probe replaces the verdict. Anything
else says nothing about tool support and is a 502 that stores nothing:

- a timeout, a 5xx, or a 401, 403 or 404 (`fatal`);
- a refusal of the second request, the tool's answer, of any kind: the model did call the
  tool, and the refusal is as likely to be about how Crucible sent the answer;
- a reply cut off at Max tokens, before the call or inside its arguments, whose message
  says to raise it. A reasoning model needs room for its thinking
  ([sending.md](sending.md) § The order of a send);
- the whole probe passing its deadline (below).

**It is a real call, so it follows the chat's rules.**

- Each call holds one of the provider's shared slots (`llm_limiter`) and gets its client from
  `make_client`. The time limits are the chat's (`chat_runner.FIRST_TOKEN_S` and `IDLE_S`).
  The provider snapshot is `chat_runner.provider_snapshot`.
- Each call is recorded in `llm_usage` under the rule a reply follows: once the server
  accepted it, once text arrived, or, for a billed provider, once it was sent and not
  refused. The rows have no chat, so a billed probe counts toward today's budget and no
  chat's.
- A billed provider's probe is refused (409) before it starts when today's budget could not
  cover both calls at full length. `chat_runner.billed_used` is the sum both checks read.
- Error text goes through `chat_runner.scrub`, so the key never reaches the response.
- A second probe of the same model while one runs is a 409. The guard is taken before the
  first await, the budget read, so a double-click cannot slip a second probe in.
- **The whole probe has a deadline**, `PROBE_TOTAL_S` (300 s), past which it ends with no
  verdict. The chat's limits are per chunk, and a reasoning model's hidden thinking
  arrives as chunks that are not text, so they never stop it. On 2026-11-19, with the GPU
  fully loaded by other work, Qwen 3.8 27B in LM Studio ran 723 s and reached no verdict.

## Live results

Through `scripts/analyst_trials.py --probe-only` ([trials.md](trials.md)), 2026-11-19:

| Model | Verdict |
|---|---|
| Gemini 2.5 Flash (OpenAI-compatible endpoint) | passed, 2 s |
| Qwen 3.8 27B, LM Studio | no verdict: the GPU was fully loaded by other work, and the first reasoning chunk took 125 s. Re-run on an idle GPU |

**The verdict is stored only against the server that was probed.** `PATCH /providers/{id}`
sets `tools_probe` to NULL whenever it clears the chats' privacy, on a new `base_url` or a
change in whether the provider bills; a rename or a new default model keeps it. The probe
ends its read transaction before the calls, which can take minutes, and re-reads the row
before storing. If the address or billing changed meanwhile, the stale verdict is
returned but not stored. The stored dict is copied before it is changed (CLAUDE.md § Never
mutate a loaded JSON column in place). Two probes of different models on one provider that
finish within moments of each other can lose one verdict to the other's write; a re-probe
puts it back. **A stored entry that does not validate is skipped**, by `tools_ok` and by
`OpenAIProviderOut`, so a hand edit or an older shape never turns every provider listing
into a 500.

## The fake LLM

Two rules in `backend/tests/fake_llm.py` make tool round trips work unscripted
(`docs/dev/backend-tests.md` § The fake LLM server): a trailing `tool` message is answered
in text quoting it, and an offered `crucible_echo` is called with the word the prompt asks
for. The second is what lets e2e, which cannot script replies, see a probe pass.

## Tests

`backend/tests/test_tool_probe.py`:

- `ToolCallBuffer`'s fragments by index and the edges above.
- `stream_chat` assembling streamed calls against the fake, a request without tools
  carrying neither key, and a tool-call piece handing over to the idle limit.
- The probe passing, both calls' requests and its usage rows.
- Each way of failing stored, and a later pass replacing it.
- No verdict storing nothing, with the key scrubbed from the 502: a 5xx, a 401, a cut-off
  before the call and inside its arguments, a refused tool answer, the deadline.
- A double-click running one probe, and malformed stored verdicts skipped.
- `ToolCallBuffer` keeping calls apart, and `extra_content` kept and sent back.
- The billed daily budget refusing before any call, the model choice and the 404/422s.
- The verdicts cleared on an address or billing change and kept on a rename.

`backend/tests/test_fake_llm.py` pins the fake's two rules.
