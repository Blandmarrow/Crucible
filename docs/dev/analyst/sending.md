# Sending a message and streaming the reply

Phase 2b of the analyst (M12): what happens between the user pressing Send and the reply
being stored. It covers budgets, the streamed call, cancel, the startup sweep, and the
provider limit shared with caption jobs. The context the call sends is
[chat.md](chat.md); the family hub is [../analyst.md](../analyst.md). A reply that uses
tools (phase 3) takes several calls, each run as this file describes; the loop between
them is [tool-loop.md](tool-loop.md).

## Where things live

- `backend/services/chat_runner.py`: the send, the background task, usage, the events,
  cancel and the sweeps.
- `backend/ml/chat_stream.py`: the one streamed chat completion, with its two time limits.
- `backend/services/llm_limiter.py`: the per-provider limit shared with captioning.
- `backend/routers/chats.py`: the routes below.

## The order of a send

`chat_runner.start`, then `_execute` in a task:

1. **Refusals first, storing nothing.** No provider (422), no model on the chat or as the
   provider's default (422), a reply already being written in this chat (409), or a budget
   the send could pass (409, below). The chat goes into `_starting` straight after its
   check, with no await between, and leaves it once its run is registered. A double-click
   gets a 409, never a second send whose rows collide on `seq`.
2. **Both rows before the call.** The user's message is stored, and the chat gets its title
   from it if it had none. Then `build_context` runs, and the assistant row is stored with
   `status="partial"`, the reply's `model`, its `context_manifest`, and the
   `findings_summary`/`image_count` that the next reply's delta reads. The context is
   built after the user row is stored, so it is in the history; the preview passes it as
   the draft instead.
3. **A background task owns the call**, kept in `_runs` by message id. The request
   handler only listens, so closing the tab or dropping the connection leaves the reply to
   finish. The task holds a provider slot (below) and builds its client with `make_client`
   (`max_retries=0`, PM-023). A flush loop writes the text so far to the row every
   `FLUSH_EVERY_S` (1 s), so a reload mid-reply shows it.
4. **Usage before the reply.** When the call ends, however it ends, the `llm_usage` row is
   committed first, in its own commit, and only then the final message: text, status,
   tokens, `related_findings`. This is the paid-call cap's rule
   (`docs/dev/comfyui/run-budgets.md`): a failure writing the reply must not lose the
   record of a call that may have been billed.
5. **The store cannot be cut short.** Once the call is over the run is marked `ending`.
   From then on a Stop, a delete or a shutdown waits for the store instead of cancelling
   it, and `_finish` runs under `asyncio.shield`. If the store itself fails (a locked
   database, say), `_finish_failed` marks the row an error as best it can and still sends
   every listener a final `error`, so none waits forever.

**What is recorded.** A call is recorded once the server accepted it, or once any text
arrived. A request to a **billed** provider that was sent but cut short before any answer
(a Stop, or the first-token limit, while the server was still working) is recorded too, as
an estimate: it may have been billed. Only an HTTP refusal (a 429 or 401 before the stream
opened) is known to have run nothing, and records nothing; for a free provider, neither
case is recorded. A cancelled reply's estimate counts the visible text only: hidden
reasoning that never arrived cannot be seen. The tokens are the server's `usage` when it sent one
(`stream_options.include_usage` asks for it). Otherwise they are a character estimate
(`CHARS_PER_TOKEN`) of the prompt and the text, marked `estimated` on both the usage row
and the message.

**Hidden reasoning counts as output** (`chat_stream.usage_tokens`). Output is the larger of
`completion_tokens` and `total_tokens - prompt_tokens`. A reasoning model bills its thinking
as output, and not every server counts it in `completion_tokens`. Gemini 2.5 Flash, through
its OpenAI-compatible endpoint, reported 57 completion tokens with a total of 833 for a
16-token prompt (2026-11-19). Read alone, `completion_tokens` would have under-counted the
budget about tenfold.

**A reasoning model needs room in Max tokens.** The thinking comes out of the same
`max_tokens`. The live check (2026-11-19) shows it. With a limit of 1,024, Gemini 2.5 Flash
stopped after two sentences (`finish_reason: "length"`, 41 visible tokens). With 2,048 it
finished, 918 output tokens in all. The `length` ending's message points at the provider's
Max tokens.

## The call and its time limits

`chat_stream.stream_chat` sends `stream=True` with `max_tokens` from the provider. A
provider's `timeout_s` is tuned for one caption, and a healthy chat reply can stream for
minutes, so the chat has two limits of its own instead of one total:

- **`FIRST_TOKEN_S`** (180 s): until the first text arrives, covering a long prompt on a slow
  local model.
- **`IDLE_S`** (60 s): the longest the stream may then go quiet.

Each limit is an `asyncio.wait_for` around one step of the stream; a test shortens them by
monkeypatching `chat_runner`. The client still gets the provider's `timeout_s`, which the
SDK applies per read.

How a reply ends:

| Ending | `status` | `error_kind` |
|---|---|---|
| The stream finished with text | `complete` | none |
| `finish_reason: "length"` | `complete`, or `error` with no text | `length`, with a pointer to Max tokens |
| Finished with no text | `error` | `empty` |
| A failure | `error` | `prompt_generator._classify`'s `transient`, `rejected` or `fatal` |
| A time limit | `error` | `timeout`; the text so far is kept |
| The user's Stop | `cancelled` | none; the text so far is kept |
| Shutdown, or a crash found at startup | `error` | `interrupted` |

**A refusal is not stripped.** Captioning strips refusal sentences from a caption
(`docs/dev/captioning/failures.md`), but in a chat a refusal is an answer the user should
see.

**The key stays out.** It is read from the provider row when the send starts and lives only
in the task. An error message is passed through `scrub`, which replaces the key and cuts the
message to 500 characters, before it reaches the row or an event. Log lines name the chat
and the error kind; the one that logs an unexpected failure's message scrubs it first. The
provider snapshot `_Provider` keeps the key out of its `repr`.

## Events

`POST /chats/{id}/messages` answers with server-sent events (sse-starlette, as the job
stream uses). The browser reads them with `fetch` and a stream reader, since `EventSource`
cannot send a body:

- `start`: `chat_id`, `message_id`, `user_message_id`, `content`, the text so far, and
  `tool_calls`, the stored tool calls so far.
- `delta`: `text`, one piece.
- `tool_call` and `tool_result`: a reply that uses tools ([tool-loop.md](tool-loop.md)
  § Events).
- `usage`: `tokens_in`, `tokens_out`, `estimated`, when a call was recorded: the turn's
  running totals.
- `done` or `error`: the stored assistant message (`ChatMessageOut`, with its
  `tool_calls`), the last event.

Each listener has its own queue (`chat_runner.listen`), added when the stream's generator
first runs, so a client gone before then leaves none behind. Leaving stops the listening,
never the task. `GET /chats/{id}/messages/{mid}/stream` resumes after a reload: while the reply
is running, a `start` with the text so far and then the rest live. Once it has ended, its
`start` and final event at once.

## Finding references and the strip

The reply may write `[[finding:id]]` (the system prompt asks it to). After the stream ends,
`_related` stores the strip shown under the reply. It takes the reply's references that name
a current finding, in order, then the most severe open findings until there are
`RELATED_MIN` (3), at most `RELATED_MAX` (6). An id the report does not know is never
stored, so the model cannot invent a button. How the page renders a reference and the strip
is [chat-ui.md](chat-ui.md) § Rendering a reply.

## Budgets

`chat_runner.budget_status` sums `llm_usage` rows; it keeps no counter. **Only billed usage
counts, and only a billed provider is refused.** Billed means `provider_is_billed` at call
time, recorded on the row. For each send the chat's billed total and today's billed total
(`local_day`, the machine's calendar date) are compared with the two settings. The
comparison uses the most the send could use: the context's estimate plus the provider's
`max_tokens` for the reply. A send that would pass either budget is a 409 whose message names
both numbers and where to raise them. `POST /chats/{id}/preview-context` returns the same
figures as `budget`, so the composer can warn before Send.

## Cancel, delete and shutdown

- `POST /chats/{id}/messages/{mid}/cancel` stops the task, waits for it to store the text so
  far as `cancelled` (with its usage), and returns the message. On a reply that already
  ended, or one already storing its reply, it waits and returns the reply as stored.
- `_stop` waits with `asyncio.wait`, which neither passes a cancel of the caller into the
  task nor swallows one aimed at the caller (PM-027).
- Deleting a chat (`DELETE /chats/{id}`) or a dataset (`DELETE /datasets/{id}`) first stops
  its replies, through `cancel_chat` and `cancel_dataset`. Otherwise the task would write
  to rows the delete removed. Their usage stays, with `chat_id` NULL.
- At shutdown, `lifespan` calls `stop_all`: each reply ends `error: interrupted`, with what
  it used recorded. At startup, `mark_interrupted` turns any `partial` row a crash left into
  the same, beside the job queue's own sweep.

## One limit per provider, shared with captioning

A provider's `max_concurrent` is how many requests its server takes at once. By the user's
ruling of 2026-11-18, a request now holds one slot of `llm_limiter.slot(provider_id, ...)`
while it is in flight. That covers the chat's whole stream and each caption request in
`/captioning/run` and `/captioning/pipeline`, so a caption job and the chats on one provider
no longer exceed it together. The job queue runs one job at a time, so two caption jobs never
overlapped anyway. A job's `InOrderCalls` bound is unchanged and still sets its own window
(`docs/dev/captioning/jobs.md` § Requests in flight).

- **One limit per provider and event loop, resized in place.** The `size` a caller passes
  only sizes a limit when it is first made. A changed `max_concurrent` reaches it only
  through `resize`, which the providers route calls after the update. A caption job keeps
  the setting it read at its start, and that stale copy must not split the limit in two.
  A shrink takes effect as slots come back.
- **First come, first served**, and a waiter cancelled while queued leaves without a slot.
- **A caption Stop drops requests still waiting for a slot.** The caption calls pass the
  job's cancel flag as `stop`. A request still queued when Stop comes raises `SlotSkipped`
  and is never sent, and both finish loops skip it without counting a failure. Without
  this, Stop would be followed, minutes later, by requests queued behind a chat.
- **Not covered:** Ollama, which has no provider row and keeps its own per-job bound, and
  ComfyUI prompt generation (`prompt_generator`), which builds its own client.
- **Paid-call counting.** A caption request waiting for a slot has already been started as
  far as `InOrderCalls` is concerned. If the job then ends early, `_record_calls_started`
  counts it as sent, and a skipped request still advances `done_items` past it. For an
  automated run's paid-call cap that over-counts, which is the safe direction.

## Tests

`backend/tests/test_chat_send.py`, against `fake_llm`, covers:

- the stream and what is stored, the history on a second send, references and the strip;
- hidden reasoning counted as output (`usage_tokens`);
- an estimated usage, `length`, a 429 recording nothing, and a 401 whose key never reaches
  a row, an event or Crucible's log;
- an idle timeout keeping its text and usage, and the refusals storing nothing;
- budgets refusing a billed send, counting a failed billed call and leaving a free provider
  alone;
- cancel, a second send refused while a reply runs, and two sends at once;
- a Stop while the reply is being stored, and a store that fails;
- a billed request cut short before any answer recorded, a free one not;
- a reply finishing with nobody listening, and a late listener resuming mid-reply;
- the startup sweep, a dataset deleted mid-reply, and shutdown;
- a restricted chat's request to the provider carrying no disabled category;
- a chat and a caption job queuing on one provider slot, and a caption Stop dropping the
  requests still queued.

`backend/tests/test_llm_limiter.py` covers the limiter on its own: order, resize in place,
a waiter giving up on `stop`, and a fresh limit per event loop.
