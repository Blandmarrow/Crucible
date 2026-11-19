# Note proposals

Phase 2d of the analyst (M12): a reply can end with a `crucible-notes` block, and each line
becomes a suggested change to the notes that the user accepts or rejects. This file covers
the block's format and parser, the `chat_proposals` table, accept, reject and undo, and the
chat columns on the notes. The notes themselves are [notes.md](notes.md); the context that
numbers them is [chat.md](chat.md); the page that shows the suggestions is
[chat-ui.md](chat-ui.md); the family hub is [../analyst.md](../analyst.md).

## Where things live

- `backend/services/note_proposals.py`: the parser, `store`, `accept`, `reject`, `undo`.
- `backend/models/chat.py::ChatProposal`, migration `8f55ae96965a`.
- `backend/routers/chats.py::act_on_proposal`: `POST /chats/{id}/proposals/{pid}/{action}`,
  where the action is `accept`, `reject` or `undo`, answering the `ProposalOut`.
- `frontend/src/components/chat/ProposalList.tsx`, and `stripNoteBlocks` and `ASK_FOR_NOTES`
  in `frontend/src/api/chats.ts`.

## The block

The system prompt (`analyst_context.SYSTEM_PROMPT`) shows the model the format: one change
per line, inside a fenced block whose info string is `crucible-notes`.

```text
add decision: Dropped images under 768px; the LoRA trains at 1024.
edit preference 2: The trigger word zxc comes first.
remove open_item 3
```

The sections are `preference`, `decision`, `open_item` and `about_me`. The model may be a
small local one, so the parser is **forgiving about form and strict about meaning**:

- **Forgiving**: case, a list marker before a line (`-`, `*`, `1.`), a colon or a space
  after the section, `#` before a number, a plural or spaced section name ("Open items",
  "about me"), and a block whose closing fence never came.
- **Strict**: an unknown op or section, a number that is not digits ("three"), or an op with
  no text is dropped, never guessed at. A misspelt section is dropped rather than matched
  to the nearest one, since a wrong section is a wrong note. The section ends at a word
  boundary ("preferenceful" is no section), a number takes all its digits, and the text
  needs a separator and cannot be a bare colon: without those, `edit decision 23` with its
  text left out read as item 2 with the text "3".
- **A closing fence counts only at the start of a line**, so a ``` inside a note's text
  does not end the block and drop the lines after it.
- At most `MAX_PROPOSALS` (20) per reply, each text cut to `MAX_TEXT_CHARS` (2,000), the
  size of one note.

`backend/tests/fake_llm.py`'s `notes` and `notes-malformed` presets are the corpus: two
well-formed adds, and five lines that must all be dropped.

## Numbers mean what the reply was shown

An edit or a removal names an item by its number in the context's notes listing. **The
number is resolved against the reply's own `context_manifest.note_numbers`**, the numbering
that context showed, never against the notes as they are later: a note added or reordered
since would otherwise shift "remove open_item 3" onto a different note. `store` resolves
each number once, when the reply is stored, into `target_item_id`, and records the item's
text then as `target_text`, which is what the card shows struck out. A number past the end
of the numbering is dropped. With `notes` off for the chat, the numbering is empty, so only
adds survive.

## When proposals are stored

`chat_runner._finish` calls `note_proposals.store` only for a reply that ended `complete`
with no `error_kind`. A stopped reply or one cut off at Max tokens may hold half a block,
and a half block can still parse, so it proposes nothing. The proposals go in the same
commit as the reply. A failure while parsing is logged and leaves the reply without
proposals rather than failing the store.

**The stored reply keeps its block**, as the model wrote it, and the history sends it back
that way. Only the display hides it: `stripNoteBlocks` in the frontend removes closed and
unclosed blocks alike, and while a reply is being written it also hides a last line that
starts a fence and has not yet reached its newline ("```crucible-no"), so a block does not
flash as text while it streams. A reply whose block held nothing readable, and no other
text, says so rather than showing an empty bubble.

## Accept, reject and undo

A proposal is `pending`, `accepted`, `rejected` or `stale`. Each action is one commit, and
**each starts by claiming the proposal** with a conditional `UPDATE … WHERE status = :from`
(`_claim`). SQLite's write lock serialises two requests at once (two panes on one chat, a
retry), and the second finds the status already moved and gets a 409. A plain read of the
status first let two accepts both see `pending` and add the note twice. When the change
then fails, `_abandon` rolls back the claim with it. A rollback expires the session's
objects, so nothing after it reads the proposal's attributes; `stale` is written by id.

- **Accept** applies the change through `notes_service` with `actor="llm"` and the chat's
  id, on the user's say-so. An add creates the item (a decision is dated now, as the notes
  editor does), and the item's `source_chat_id` names the chat. An edit or a removal first
  checks that its target still exists, is live, belongs to this chat's dataset and **still
  reads `target_text`**; if not, the proposal turns `stale` and the request is a 409. The
  text check matters: the card shows `target_text` struck out, so accepting over a note
  reworded by hand since would replace text the user never reviewed. The notes' own rules
  still hold: past the size cap is a 422 and the proposal stays pending.
- **Reject** marks it `rejected`. Nothing else changes.
- **Undo** goes back to `pending`. On a rejected proposal it only changes the status. On an
  accepted one it reverses the change first, as the user: an add is removed, an edit put
  back to `applied_before`, a removal restored. **A note changed by hand since is not
  undone over the top**: undoing an add or an edit is a 409 when the note's text is no
  longer the proposal's.
  A restore keeps its own refusals (the cap, a newer dismissal of the same finding).

Removing a dismissal note un-dismisses its finding, as removing it by hand does. The card
says so on a pending removal of one, since the model sees the dismissal in its context and a
caption could argue for removing it; the card's refresh invalidates the health report.

## The chat columns on the notes

`dataset_note_items.source_chat_id`, `app_note_items.source_chat_id` and
`note_item_history.chat_id` all point at `chats.id` with `ON DELETE SET NULL`: **deleting a
chat must never delete a note**, or its history. A chat's proposals cascade with its
messages. The migration adds the three columns through `batch_alter_table` rebuilds, since
SQLite cannot add a foreign key with `ALTER TABLE`; the history table's named CHECK
constraint survives the rebuild.

`target_item_id` has no foreign key on purpose: it names an item in either note table, and
an item gone since is exactly what `stale` reports.

## The page

`ProposalList` renders a reply's proposals under its text: the section, the old text struck
out for an edit or a removal, the new text, and Accept and Reject, or the outcome and Undo.
A pending edit or removal whose note is no longer among the live notes
(`datasetNotesKey`, `APP_NOTES_KEY`), or reads differently from `target_text`, shows as out
of date with no buttons, as the server would refuse it. Each action invalidates the chat, the notes scope it touched, the health report
and the preview, since the notes are context.

**"Ask it to note this"** sits under the latest reply when it finished with no proposals. It
sends `ASK_FOR_NOTES`, a narrow follow-up asking for the block alone, for a model that
agreed to something but wrote no block.

## Tests

- `backend/tests/test_note_proposals.py`: the parser (forgiving forms, the malformed corpus,
  numbers and words it must not split, other fences, a fence inside a line, an unclosed
  block, the count cap); three accepts at once applying the change once; proposals from a notes reply, accept,
  reject and undo with the history's actor and chat; numbers resolved through the manifest
  despite a reorder; hand edits never overwritten (accept goes stale, the undo of an edit or
  an add is refused); `stale`; the cap; `about_me`
  going to the app notes; a cut-off reply proposing nothing; and deleting the chat keeping
  its notes with the links nulled.
- `frontend/e2e/chat-notes.spec.ts`: "Ask it to note this", then the notes reply's two
  suggestions accepted, undone, rejected and accepted again, ending on the Health page.
