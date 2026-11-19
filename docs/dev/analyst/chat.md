# The analyst chat and its context

Phase 2 of the analyst (M12): a chat about one dataset with an OpenAI-compatible provider,
no tool calls. This file covers the tables, the privacy settings and `build_context`, the one
place the LLM's context is built. The family hub is [../analyst.md](../analyst.md); the
findings the context lists are [health.md](health.md); the notes it numbers are
[notes.md](notes.md). Sending, streaming, budgets and cancel are
[sending.md](sending.md).

## The tables

In `backend/models/chat.py`, migration `d4b8e2a6c1f9`:

- `chats`: one conversation. `dataset_id` cascades (decision 21: chats are deleted with
  their dataset). `provider_id` is `SET NULL`, so removing a provider keeps the history and
  the chat asks for a new one. `privacy` is the chat's override, NULL meaning "follow the
  provider".
- `chat_messages`: the turns, ordered by `seq` (unique per chat). `status` is `complete`,
  `partial`, `cancelled` or `error`; `error_kind` and `error_detail` explain an error and
  never hold the API key. An assistant row also stores `context_manifest` (what was sent),
  `findings_summary` and `image_count` (what its context showed, for the next reply's delta),
  `related_findings` and the `model` that wrote it.
- `llm_usage`: one row per call, the budgets' only source; they sum rows rather than keep a
  counter that can drift. `day` is the local calendar date. Its `chat_id` is `SET NULL`, not
  `CASCADE`: **deleting a chat must not give its tokens back to the daily budget.** `billed`
  records whether the call counted, since a provider's `billed` can change later.

Two budgets live on `ThresholdSettings`, `analyst_chat_budget_tokens` (default 200,000) and
`analyst_daily_budget_tokens` (1,000,000), with a floor of 1,000 so a typo cannot block every
send. Only billed providers meet them.

## Privacy: three layers

What a chat may send is decided by, in order:

1. **The defaults**, `schemas.openai_provider.default_privacy`: a billed provider gets
   `numbers` only, a free one every category. Billed is `provider_is_billed`, so ticking the
   provider's `billed` box changes the defaults with no migration. That box is also how a
   paid service reached through a `localhost` tunnel gets treated as remote: `is_remote` is
   a hostname check and would call it local.
2. **The provider's override**, `openai_providers.analyst_privacy`, a partial
   `{category: bool}`. `provider_privacy(row)` lays it over the defaults, and
   `OpenAIProviderOut` returns both the stored value and the result
   (`analyst_privacy_effective`). The update schema's `analyst_privacy_reset` sets it back to
   NULL, because the router's `exclude_none` loop cannot; an empty `{}` is stored as NULL
   too.
3. **The chat's override**, `chats.privacy`, laid on top by
   `analyst_context.effective_privacy`. With no provider, the billed defaults apply.

**A chat's override never outlives the server it was chosen for.** A "send everything" picked
for a local server must not follow the chat to a paid one, so three paths clear it:

- `PATCH /chats/{id}` with a new provider clears it. Sending `privacy` in the same request is
  a 422, so a form that resends its toggles cannot carry them over.
- `PATCH /providers/{id}` that changes the provider's `base_url`, or whether it counts as
  billed, clears the override of every chat on it. A rename or a new model does not.
- Earlier replies are covered by § History below.

`images` is reserved for a vision phase. It is forced off at every layer, and a request to
turn it on is a 422 (`schemas.chat.check_privacy`), as is an unknown category.

## `build_context`: the one choke point

`backend/services/analyst_context.py::build_context(db, chat, *, draft="")` returns the
messages, a `ContextManifest` and the effective privacy. **It is the only code that assembles
what an LLM sees about a dataset**, and the privacy filter is applied there and nowhere else.
The send path (2b) and `POST /chats/{id}/preview-context` both call it, so the preview cannot
drift from what is sent. A new category goes through it too. A phase 3 tool's result goes
through its sibling in the same module, `render_tool_result`, which applies the same
categories, `_clean` and data block to the items a tool returns
([tool-registry.md](tool-registry.md)).

The messages are one system message (the fixed prompt, then the context), then the history,
then the draft. One system message rather than two, because some local chat templates refuse
a second.

### The system prompt

`SYSTEM_PROMPT` tells the model to cite findings as `[[finding:id]]`, to treat
`<dataset-data>` as data, and how to write a `crucible-notes` block
([proposals.md](proposals.md)). It also lists the pages the model may name, by their sidebar
names, and tells it not to invent others: in the phase 2 trials Gemini invented pages
("Image Review", [trials.md](trials.md) § Observations). The list is the fix pages,
`health_service.FIX_PAGES`, then `OTHER_PAGES`, the few pages worth naming that fix no
finding (Versions, so the model can say "take a snapshot first"; Bulk Edit; Export).

Each finding's line in the context ends with its fix, `Fix: Score images ("Open the
duplicate resolver").`, so the model can tie a finding to its page. That is Crucible's own
text and goes under `numbers` with the rest of the finding. `_fix` refuses a page outside
`FIX_PAGES`, and `test_every_fix_call_names_a_listed_page` walks `health_service`'s AST so
that every `_fix` call names a listed page as a literal: a new finding cannot point at a page
the prompt leaves out.

### The categories

What each category sends:

| Category | What it sends |
|---|---|
| `numbers` | Counts, distributions, the target resolution, the active findings with their ids, the dismissed ones by id and count, and "since your last reply" |
| `identity` | The dataset's name and goal |
| `notes` | Dataset notes and About me, numbered per section; a dismissal shows as a preference naming its finding |
| `tags` | The top 30 tags over tag-style captions, the ones in nearly every caption, examples used once |
| `captions` | A sample of up to 20 captions, each cut to 300 characters |
| `file_names` | The sample's base file names |

Five rules hold the filter together:

- **A finding's `numbers` dict goes through an allowlist** (`_numbers_text`): plain numbers,
  plus the keys in `_SAFE_TEXT_NUMBERS` and `_SAFE_DICT_NUMBERS`, which hold Crucible's own
  words or fixed labels. `tag_everywhere` and `tags_once` carry tag names there, and tag names
  belong to `tags`. A new finding that puts dataset text in `numbers` is dropped by default,
  not leaked; add its key to an allowlist only if the text is Crucible's own.
- **Tags, captions and file names are untrusted.** They come from scrapers, sidecars and
  captioners, so they sit in one `<dataset-data>` block that the prompt calls data. A caption
  can still argue for an action, which is why every proposal and action waits for the user.
- **Every string Crucible did not write goes through `_clean`**: the name, the goal, notes,
  tags, captions and file names. It folds the text to one line, so it cannot forge a heading
  or list item. It applies NFKC (fullwidth `＜` becomes `<`) and drops format characters (a
  zero-width space hides the delimiter from a match). It swaps `<` and `>` for `‹ ›` and
  breaks the delimiter's name however it is spelled, so data can neither close the block nor
  open a fake one. A new kind of dataset text gets the same treatment.
- **No absolute path is ever sent.** The sample reads `Image.filename` and `base_name` keeps
  only its last component, whatever the separator.
- **Captions on also reveals tags**, since tags are written in captions. The property test
  treats them that way.

The sample is stratified by caption length. The rows are ranked by `caption_token_count`
and id, split into 20 equal strata, and one row is taken from each, picked by a generator
seeded on the dataset id. The same state gives the same sample, cached on the image
validator in the Stats cache. The window ranks ids only, an integer sort rather than every
caption's text, and the picked rows are read by id after. The row count comes from the
cached Stats payload; its captioned count ignores whitespace-only captions, so it is never
larger than the scope and every pick is in range.

## History

The history keeps the newest messages within `HISTORY_MAX_CHARS`, skips failed and empty
replies, never opens on an assistant turn, and merges two same-role turns in a row (a user
message whose reply failed sits next to the next one, which strict templates refuse).

**An earlier reply written while a category now off was shared is withheld**: its text is
replaced by `WITHHELD_REPLY` and counted in the manifest's `history_withheld`. A reply quotes
captions and names freely, so without this, turning captions off, or switching a chat to a
billed provider, would still send them through the history. The test is the reply's own
`context_manifest`: if any category it sent is off now, it is withheld. A reply with no
manifest is treated as having seen everything. The user's own turns are kept as typed.

**"Since your last reply"** diffs the newest reply the model wrote (an `error` row is
skipped) against now: "images 130 → 100; blurry 30 → 0 (resolved)". It reads that reply's
`findings_summary` and `image_count`. They are a `numbers` fact, so a reply sent without
numbers stores none, and the next context shows no delta rather than one against an older
reply.

The manifest records each category's `sent`, `chars` and `items`, where the privacy came from
(`chat`, `provider` or `none`), how much history was kept and dropped, a character estimate of
the tokens (`CHARS_PER_TOKEN`, 4), and `note_numbers`: per section, the note ids in the order
the context numbered them. A proposal's "remove open_item 3" is resolved against the manifest
of the reply that made it (phase 2d), not against the notes as they are later.

## Routes

`backend/routers/chats.py`:

- `GET` and `POST /datasets/{id}/chats`: list (newest first, with `message_count`) and create.
- `GET /chats/{id}` with its messages; `PATCH /chats/{id}` (title, provider, model,
  `privacy`, where an explicit null clears the override); `DELETE /chats/{id}`.
- `POST /chats/{id}/preview-context` with an optional `draft`: the exact messages and
  manifest the next send would use, and the `budget` figures for it.
- Sending, resuming and cancelling a reply: [sending.md](sending.md) § Events and § Cancel,
  delete and shutdown.

## Tests

- `backend/tests/test_analyst_context.py`. The privacy test seeds a sentinel string in every
  category, and an earlier reply that quotes them all. It builds the context under all 64
  combinations of toggles, once through the chat's override and once through the
  provider's. A disabled category's sentinel must be absent, an enabled one's present, and
  the earlier reply withheld unless everything is on. Two things must never appear: the
  datasets folder's absolute path, in either spelling, and sentinels in columns the context
  does not read (provenance, licence, subfolder). Both tag findings fire, so the `numbers`
  allowlist is exercised. Also covered: delimiter tricks (zero-width, fullwidth, entity,
  Unicode hyphen), newlines in the goal and a file name, the sample, the delta, note
  numbering and the history rules.
- `backend/tests/test_chats_http.py`: the routes, the cascades (dataset, provider, a chat's
  usage surviving it), the budget settings, the provider override, and the three paths that
  clear a chat's override.
