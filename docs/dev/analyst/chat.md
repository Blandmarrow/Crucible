# The analyst chat and its context

Phase 2 of the analyst (M12): a chat about one dataset with an OpenAI-compatible provider,
no tool calls. This file covers the tables, the privacy settings and `build_context`, the one
place the LLM's context is built. The family hub is [../analyst.md](../analyst.md); the
findings the context lists are [health.md](health.md); the notes it numbers are
[notes.md](notes.md). Sending, streaming and budgets arrive in phase 2b.

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
   NULL, because the router's `exclude_none` loop cannot.
3. **The chat's override**, `chats.privacy`, laid on top by
   `analyst_context.effective_privacy`. **A provider change clears it** (`PATCH /chats/{id}`):
   a "send everything" chosen for a local server must not follow the chat to a paid one. With
   no provider, the billed defaults apply.

`images` is reserved for a vision phase. It is forced off at every layer, and a request to
turn it on is a 422 (`schemas.chat.check_privacy`), as is an unknown category.

## `build_context`: the one choke point

`backend/services/analyst_context.py::build_context(db, chat, *, draft="")` returns the
messages, a `ContextManifest` and the effective privacy. **It is the only code that assembles
what an LLM sees about a dataset**, and the privacy filter is applied there and nowhere else.
The send path (2b) and `POST /chats/{id}/preview-context` both call it, so the preview cannot
drift from what is sent. A new category, or a phase 3 tool, goes through it too.

The messages are one system message (the fixed prompt, then the context), then the history,
then the draft. One system message rather than two, because some local chat templates refuse
a second. The categories:

| Category | What it sends |
|---|---|
| `numbers` | Counts, distributions, the target resolution, the active findings with their ids, the dismissed ones by id and count, and "since your last reply" |
| `identity` | The dataset's name and goal |
| `notes` | Dataset notes and About me, numbered per section; a dismissal shows as a preference naming its finding |
| `tags` | The top 30 tags over tag-style captions, the ones in nearly every caption, examples used once |
| `captions` | A sample of up to 20 captions, each cut to 300 characters |
| `file_names` | The sample's base file names |

Four rules hold the filter together:

- **A finding's `numbers` dict goes through an allowlist** (`_numbers_text`): plain numbers,
  plus the keys in `_SAFE_TEXT_NUMBERS` and `_SAFE_DICT_NUMBERS`, which hold Crucible's own
  words or fixed labels. `tag_everywhere` and `tags_once` carry tag names there, and tag names
  belong to `tags`. A new finding that puts dataset text in `numbers` is dropped by default,
  not leaked; add its key to an allowlist only if the text is Crucible's own.
- **Tags, captions and file names are untrusted.** They come from scrapers, sidecars and
  captioners, so they sit in one `<dataset-data>` block that the prompt calls data, and
  `_neutralise` breaks any copy of the delimiter inside them. A caption can still argue for
  an action, which is why every proposal and action waits for the user.
- **No absolute path is ever sent.** The sample reads `Image.filename` and `base_name` keeps
  only its last component, whatever the separator.
- **Captions on also reveals tags**, since tags are written in captions. The property test
  treats them that way.

The sample is stratified by caption length: the rows ordered by length and id, split into 20
equal strata, one row from each, picked by a generator seeded on the dataset id. The same
state gives the same sample, and it is cached on the image validator in the Stats cache.

The history keeps the newest messages within `HISTORY_MAX_CHARS`, skips failed and empty
replies, never opens on an assistant turn, and merges two same-role turns in a row (a user
message whose reply failed sits next to the next one, which strict templates refuse).

**"Since your last reply"** diffs the newest assistant row's `findings_summary` and
`image_count` against now: "images 130 → 100; blurry 30 → 0 (resolved)". It is a `numbers`
fact, so a reply sent without numbers stores no summary and the next one shows no delta.

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
  manifest the next send would use.

## Tests

- `backend/tests/test_analyst_context.py`. The privacy test seeds a sentinel string in every
  category and builds the context under all 64 combinations of toggles. A disabled category's
  sentinel must be absent, an enabled one's present, and the datasets folder's absolute path
  never there. Also covered: the delimiter, the sample, the delta, note numbering and the
  history shape.
- `backend/tests/test_chats_http.py`: the routes, the cascades (dataset, provider, a chat's
  usage surviving it), the budget settings and the provider override.
