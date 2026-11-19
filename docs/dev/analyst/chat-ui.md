# The chat page and the Analyst settings tab (frontend)

Phase 2c of the analyst (M12): the browser side of the chat. It covers `ChatPage` and its
components, the `chatStore` that owns a reply's stream, how a reply is rendered, and the
Settings → Analyst tab. The routes it calls are [chat.md](chat.md) (chats, privacy,
`build_context`, the preview) and [sending.md](sending.md) (sending, the events, cancel,
budgets); the family hub is [../analyst.md](../analyst.md). How a user reads the page is
`docs/analyst.md` § The analyst chat.

## Files and query keys

| File | What |
|---|---|
| `frontend/src/pages/ChatPage.tsx` | The page: the chat list, a chat's header (title, provider, model, "What gets sent", delete) and the empty state |
| `frontend/src/components/chat/MessageList.tsx` | The stored messages with the live reply laid over them, a reply's ending and cost, the related-findings strip |
| `frontend/src/components/chat/ChatMarkdown.tsx` | A reply's text as markdown, with finding references as chips |
| `frontend/src/components/chat/ProposalList.tsx` | A reply's suggested notes: [proposals.md](proposals.md) § The page |
| `frontend/src/components/chat/Composer.tsx` | The message box, Send or Stop, and the budget line |
| `frontend/src/components/chat/ContextDialog.tsx` | "What gets sent": the chat's privacy toggles and the preview |
| `frontend/src/store/chatStore.ts` | The streams, outside React, and which chat each dataset last showed |
| `frontend/src/api/chats.ts` | The types, the routes, the privacy labels and `openReplyStream` |
| `frontend/src/components/settings/AnalystPanel.tsx` | Settings → Analyst: the budgets and each provider's privacy |

Query keys, from `api/chats.ts`:

- `chatsKey(datasetId)`, `["chats", id]`: the list, with `message_count`.
- `chatKey(chatId)`, `["chat", id]`: the chat and its messages.
- `chatPreviewKey(chatId)` plus the draft, `["chat-preview", id, draft]`: the preview, which
  also carries the budget figures. The draft is debounced (600 ms) before it joins the key,
  and `keepPreviousData` holds the last line while the next loads. A chat update invalidates
  its `["chat-preview", id]` prefix. A reply ending, and in Settings a budget or provider
  privacy change, invalidate `["chat-preview"]` for every chat, since today's budget is
  shared. A write in another pane (a note, the goal, a caption) invalidates nothing here, so
  **opening "What gets sent" invalidates the chat's preview first**: the dialog promises
  exactly what the next send would send.

The findings come from the Health page's own query (`useHealthReport`, [ui.md](ui.md)), so a
chat beside a Health pane shares one report.

## Routing

`chat` is a routed, pickable dataset page at the same sites as Health ([ui.md](ui.md)
§ Routing): `lazyPages.ts`, the `App.tsx` route `/datasets/:datasetId/chat`, `PageRenderer`,
the `PageType` union, `PAGE_OPTIONS` and `NEEDS_DATASET` (label "Analyst"), and the
Sidebar's Analyst entry.

**`PageRenderer` does not key it, and `ChatPage` keys its body on the dataset**, as Health
does: drafts and open dialogs reset on a switch. Neither key can stop a reply, because the
reply is not in the component (below). Each chat's view is keyed on the chat id, so its
draft does not follow to another chat.

## The stream lives outside React

`useJobSSE` keeps its `EventSource` inside a `useEffect`, so a remount closes it. A chat
reply must survive a pane remount, a pane move, a dataset switch and leaving the page,
so the fetch lives in `chatStore`:

- **`streams`, by chat id**, is what components render: the text so far, the reply's row
  id once `start` arrives, the user's message until its stored copy is fetched, the phase
  and `stopping`. The `AbortController`s sit in a module-level map beside it, since they
  are not data a component renders. One reply per chat, as the server's 409 enforces.
- **`openReplyStream`** is `fetch` and a stream reader, since `EventSource` cannot send a
  body. It splits on blank lines (sse-starlette writes CRLF) and skips `:` keep-alive
  comments. A response that is not OK becomes `SendRefused`, its message read by
  `apiErrorDetail` from the JSON body, so a 422's field list reads as an axios error would.
- **`sendMessage` resolves as soon as the request's fate is known**: true once the server
  accepted it and the stream opened, false on a refusal or no connection, when nothing was
  stored. The composer clears the draft on true and keeps it on false. The rest of the
  stream is read in the background.
- **Settling waits for the stored copy.** When the stream ends, however it ends, the chat
  and the list are invalidated and refetched first, and only then is the stream entry
  dropped. In the other order the reply would blink out between the two copies. Send stays
  off until the entry is gone, since the server would refuse a send that early. A run drops
  only its own entry (`drop` checks the run's `AbortController`), never a later run's.
- **A dropped connection is not a failed reply.** The server's task keeps writing, so the
  toast says to reopen the chat, and the refetch shows what was stored. The reply's id leaves
  `resumed` (below), so reopening does resume it, even when the dropped stream was itself a
  resume.
- **Resume.** Opening a chat whose last message is an assistant row still `partial` calls
  `resumeReply`, which reads `GET …/messages/{id}/stream`. It runs once per reply per page
  load (`resumed`): a row left `partial` with no run behind it, before the startup sweep has
  seen it, would otherwise resume, refetch as `partial` and resume again, forever.
- **Stop** posts the cancel route and lets the stream end with its `done` event. It is off
  until `start` has given the reply a row id. **Deleting a chat** first calls
  `forgetStream`, which aborts the fetch, so the server's own end of the stream is not
  reported as a lost connection. An aborted run skips the settle's refetch, and the chat
  joins `forgotten`, which `resumeReply` refuses: the server stops the reply before it
  deletes, so a refetch mid-delete can still show it `partial`, and a resume would 404 once
  the delete commits. A failed delete takes the chat back out (`unforgetStream`).
- **Which chat a page shows** (`useOpenChat` in `ChatPage`) is the pane's own in split view,
  `PaneView.chatId`, so two panes on one dataset can show two chats, and a pinned chat
  comes back with the saved layout. Outside split view it is `openChat`, kept in memory
  by dataset, with no storage key. Either falls back to the newest chat when the one it
  names is gone.

`MessageList.mergeRows` lays the stream over the stored messages: the stream's text wins for
its own row while it runs, and its user message shows until a stored message with the same
id arrives. The list follows the reply while the reader is at the bottom and leaves them
alone once they scroll up. The chat list reads only which chats are writing, as one string,
so a streamed piece re-renders the messages and not the list.

## Open beside

The chat is meant to stay visible while the user works elsewhere, and panes are how
(the plan's first attempt, `docs/dev/analyst-plan/README.md`). `useOpenChatBeside(datasetId)` calls `paneStore.openBeside`
(`docs/dev/panes-routing.md` § The layout survives a reload): the neighbouring pane, or a
new pane split off the current one, turning split view on if it is off. **A pane already
showing that dataset's chat is focused instead**, so asking twice never stacks two chats.
The active pane is set in a microtask, as `useOpenBeside` does, because the calling pane
makes itself active on the same click.

Two callers: the button beside the sidebar's Analyst item ("Open the analyst beside this
page"), from outside any pane, where the active pane is the one beside which it opens; and
"Ask the analyst" on the Health page. `RouteSyncer` only drives the first pane, so the
sidebar's links move the page on the left and leave the chat alone. The layout, and with it
the pane's chat, is saved and restored on reload.

## Rendering a reply

`ChatMarkdown` renders with `react-markdown` 10.1.0 and `remark-gfm` 4.0.1, both pinned to
an exact version (the user's ruling of 2026-11-18). Three rules:

- **Raw HTML is never rendered.** react-markdown escapes it unless a raw plugin is added, and
  none is. Do not add `rehype-raw`.
- **An image is never loaded.** The `img` component renders "[image not loaded]" with the
  address as a tooltip. A reply can quote captions, which are untrusted, and an image address
  is a way to carry data out of the page the moment it renders. Links still render, opening
  in a new tab, since a link needs a click.
- **A finding reference is a chip only for a known id.** `[[finding:id]]` (the pattern of
  `chat_runner.FINDING_REF_RE`) naming a finding in the current report, active or dismissed,
  becomes a link to `#crucible-finding-id`, which the `a` component renders as a badge built
  from the report's data. An unknown id stays as typed. A model that writes that link
  itself only gets the same chip, since the chip's text is Crucible's.

The **Related findings** strip under a reply is `related_findings` (`sending.md` § Finding
references and the strip) mapped through the current report, as compact `FindingCard`s.
An id the report no longer has is left out rather than shown as a dead card. The latest
reply's strip starts open, older ones closed.

A reply's ending reads `status` and `error_kind` (`sending.md` § The call and its time
limits): `cancelled` shows "Stopped", an `error` shows the stored `error_detail`, and a
`complete` reply with `error_kind: "length"` shows its cut-off warning. The model and the
token counts sit under it.

## Controls that save as you use them

- **A checkbox shows its pending value from the click.** React puts a controlled checkbox
  back at the end of the click event, before a mutation's pending state reaches the page,
  so reading `isPending`/`variables` leaves it unticking and reticking. `ContextDialog` and
  `AnalystPanel` keep the choice in local state from the click, and clear it in that call's
  own `onSettled`. Their mutations' `onSuccess` returns the invalidations' promise, so the
  call settles, and the pending choice goes, only once the refetch has landed.
- **The model saves when typing pauses.** `ModelPicker` reports every keystroke in its text
  mode, so `ModelField` debounces 700 ms before the PATCH. It is keyed on the provider, so a
  model still waiting when the provider changes is dropped, not saved onto the new one. A
  server change replaces the field only while the user has not typed past it. A send within
  700 ms of a model change still uses the old model, and a title being edited when the chat
  is switched is not saved. Blank
  means the provider's default: `ModelPicker`'s `emptyLabel` offers it as a real choice,
  "Provider default (…)".
- **A provider change sends `model: ""` and never `privacy`.** The server clears the chat's
  privacy override itself, and refuses `privacy` beside a provider change (`chat.md`
  § Privacy: three layers).

## Settings → Analyst

`AnalystPanel` is its own component, like `LabelsPanel`, with its own reads of
`["settings", "thresholds"]` and `["providers"]`. The budgets save on their own button
through the thresholds route, sending only the two fields; `NumberField` clamps them to
1,000 and the column's ceiling, the schema's bounds. Each provider row toggles one category
by sending the whole set, `images` left out, as `analyst_privacy`. **Back to the defaults**
sends `analyst_privacy_reset`. The tab's copy states the two facts a user cannot see from the
boxes: the defaults follow the provider's `billed` box rather than its address, and a
reasoning model needs Max tokens room.

## Tests

`frontend/e2e/chat.spec.ts`, against the fake LLM: a reply streams in, gets its strip and
names the chat; the request went out as a stream; a `[[fake:stall]]` reply is stopped with its
text kept; another is picked back up after a reload and then stopped; "What gets sent" turns
captions off for the chat and back. Not covered: rendering safety (no fake preset replies
with markdown images or HTML), a refused send keeping its draft, and a delete mid-reply.
`frontend/e2e/chat-beside.spec.ts` opens the chat beside the gallery, picks a chat in that
pane, follows the sidebar to Stats on the left, asks again without stacking, and reloads
back onto the same chat; and opens it from the Health page. A second test drives
Settings → Analyst: a provider's privacy and its reset, and a budget.
