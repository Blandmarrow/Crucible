# The tool loop in the send path

**Status: living.** M12 phase 3d: how a reply uses tools. A chat whose model passed the
tool-call probe ([tools.md](tools.md)) is offered the tools its privacy allows
([tool-registry.md](tool-registry.md)), and a reply may then take several calls to the
model. The send path it extends, with its order of a send, time limits and budgets, is
[sending.md](sending.md); the context and its manifest are [chat.md](chat.md).

## Where things live

- `backend/services/chat_runner.py::_execute`: the loop, `_record_round`, `_run_tools`,
  `_budget_stop`, and the constants below.
- `backend/services/analyst_context.py::build_context`: which tools a request offers, the
  `TOOLS_PROMPT` paragraph, and the manifest's `tools_offered`.
- `backend/models/chat.py::ChatToolCall`, table `chat_tool_calls` (migration
  `6c65a3a41ae0`), returned as `ChatMessageOut.tool_calls`.

## Who gets tools

`build_context` offers tools when the chat's model (the chat's own, else the provider's
default) passed the probe, `tools_ok`, and then only those `analyst_tools.offered(privacy)`
allows. It adds `TOOLS_PROMPT` to the system prompt, counts the schemas toward the
context's estimate (they travel with every request that offers them) and names them in
the manifest's `tools_offered`, which the preview shows. With no tools offered, the
request and the prompt are exactly phase 2's.

The privacy a reply's tools run under is the one its context was built with, at the
send. A toggle changed while the reply is being written applies from the next send.

## One turn, several calls

The reply row is stored before the first call, as in phase 2, and stays the turn's one
reply row (the user's ruling, decision 21's amendment of 2026-11-19). Each call to
the model is a **round**:

1. **Before every round after the first, the budget is checked again** (`_budget_stop`),
   with the grown request and a full-length reply, for a billed provider only. A round that
   could pass a budget does not run: the reply ends with `error_kind="budget"`, keeping its
   text (so `complete` when it has some, as a `length` ending does).
2. **The round streams** with the tools, under `tool_choice="auto"` while it may still use
   them. Its text streams into the reply as before; the text of a round after a tool round
   starts after a blank line.
3. **A round that ends in tool calls is a tool round.** Its usage is committed at once
   (`_record_round`), before any tool runs, so a crash or a Stop later in the turn cannot
   lose a call that may have been billed. Then `_run_tools` runs each call through
   `analyst_tools.run_tool`, stores it as a `ChatToolCall` row, and emits `tool_call` and
   `tool_result`. The next request carries the round's assistant message (its text and
   its calls, through `ToolCallOut.as_message_call`, so a Gemini thought signature goes
   back) and one `tool` message per call.
4. **Any other ending ends the turn**, and the last call's usage and the reply are stored
   by `_finish`, as in phase 2. The message's tokens are the sum of every round. The
   endings read the last call's text, not the turn's: an earlier round's "Let me look."
   followed by an empty answer is `error: empty`, its text kept.

**The caps.** `MAX_TOOL_ROUNDS` (4) rounds may call tools. After them, or once the turn's
tool results reach `TOOL_TURN_MAX_CHARS` (24,000), one last round runs under
`tool_choice="none"`, with the tools still sent: some servers refuse a request whose
history holds tool messages but which offers no tools. A model that asks for tools
anyway and writes nothing ends with `error_kind="tools"`. In one round, calls past
`MAX_CALLS_PER_ROUND` (5) are answered with an error saying they did not run, never
dropped, since a request must answer every call it carries. A round cut off at Max tokens
(`finish_reason: "length"`) ends the turn even with calls in it: their arguments may be
cut too.

## What is stored, and what history sends

`ChatToolCall` keeps a call's round, id, name, raw arguments (valid JSON or not, capped at
`ARGUMENTS_MAX_CHARS`), whether it ran (`ok` or `error`), the exact result text sent, the
privacy categories it carried, and its length. The rows cascade with their reply, and a
listener that joins mid-reply gets the calls so far in its `start` event.

**Later turns do not resend them.** History carries the reply's text only (the user's
ruling); the model calls a tool again if it needs the data.

**But the reply's manifest learns what its tools sent.** `_run_tools` merges each result's
categories into the reply's `context_manifest` (`sent`, `chars`, `items`, and the
`tool_calls` count) in the same commit as the rows, copying the dict first. The history
rule ([chat.md](chat.md) § History) reads that manifest, so a reply that quoted captions a
tool fetched is withheld once captions are turned off, even when its context sent none.
A reply that failed is skipped by history anyway.

## Events

Beside phase 2's (`start`, `delta`, `usage`, `done`, `error`, [sending.md](sending.md)
§ Events):

- `tool_call`: `round`, `call_id`, `name`, `arguments`, as the model sent them, before the
  tool runs.
- `tool_result`: the stored call, a `ToolCallOut`, once the round's calls are committed.
- `usage` comes after each tool round as well as at the end, always the turn's running
  totals.
- `start` carries `tool_calls`, the stored calls so far, also when it is replayed for a
  reply that has ended.
- Key a call on its row `id`, not its `call_id`: a server that sends no ids gets
  `call_<n>`, which repeats across rounds.

## Cancel, shutdown and errors

A Stop or a shutdown during a round or while tools run ends the turn as in phase 2:
`cancelled` or `error: interrupted`, the text so far kept. The tool rounds already
recorded stay recorded; the cut-short call is recorded under phase 2's rule, counting only
its own text, so nothing is counted twice. **A Stop that lands while a tool round's usage
is being committed** waits for that commit, which is shielded, and when it landed marks
the round recorded; otherwise `_finish` would record the same call again (the review of
PR #192 caught this). A tool call cut off before its round committed
leaves no row. A tool's own failure is an error result for the model
([tool-registry.md](tool-registry.md) § The registry), never the reply's.

## Tests

`backend/tests/test_chat_tools.py`, against `fake_llm`: a round trip (events, the stored
call, both requests' messages, one usage row per call, summed tokens, `tools_offered`),
only a probed model getting tools, privacy choosing the tools, the round cap and the
`tools` ending, the result-size cap, the per-round call cap answering every call, a bad
call as an error row, the budget stopping between rounds, a reply withheld once a tool's
category is off, a Stop and a late listener in the second round, a Stop during a round's
usage commit recording it once, and an empty answer after a tool round.
