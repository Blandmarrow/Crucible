# Dataset notes and the goal

The context the analyst carries between chats, modelled on how this repository keeps its own:
per dataset, standing **preferences** (like `CLAUDE.md`), dated **decisions** (like the
decision log) and **open items** (like the roadmap); app-wide, notes **about me**. Plus the
two goal fields on `Dataset`. The family hub is [../analyst.md](../analyst.md); the health
report that dismissals feed is [health.md](health.md).

## Items, not a document

Notes are stored as separate items, not one text blob: a weak model can propose "add an
item to Decisions" or "remove item 4" (phase 2), but cannot reliably rewrite a whole document.
The UI renders a scope's items as one page.

Three tables, in `backend/models/notes.py`:

- `dataset_note_items`: `section` (`preference`, `decision`, `open_item`), `text`,
  `position` within its section, `decided_at` (decisions only), and for a dismissal
  `finding_id`, `fingerprint` and `permanent`. `dataset_id` cascades.
- `app_note_items`: the same without the dataset and dismissal columns; one section,
  `about_me`.
- `note_item_history`: one row per add, edit, remove or restore, with the text before and
  after and the actor (`user`, or `llm` from phase 2). **Two nullable foreign keys**, one per
  item table, with a CHECK that exactly one is set, rather than one polymorphic id: a
  polymorphic id has no foreign key, so a dataset's history would outlive the dataset.

Phase 2d added `source_chat_id` to both item tables and `chat_id` to the history, all
`ON DELETE SET NULL` (deleting a chat must never delete a note). They record which chat's
accepted proposal made a change; [proposals.md](proposals.md) covers them. The writers in
`notes_service` take `chat_id` and, for that path, `commit=False`, so a proposal's status
and its note change land in one commit.

**Notes are not dataset content.** They cascade with their dataset, are never part of a
snapshot, and `duplicate_dataset` does not copy them.

## Soft delete, history and the cap

`backend/services/notes_service.py` is the only writer. Removal sets `deleted_at`; restore
clears it and puts the item last in its section. Every change to an item's text or presence
(add, edit, remove, restore) adds its history row in the same commit, and is the undo; a
reorder or a new `decided_at` changes no text and records none. `decided_at` is kept on
decisions only (ignored for the other sections) and stored as naive UTC, converted from any
offset the client sends.

**Each scope has a size cap**, counted in characters over its live items:
`DATASET_NOTES_CAP_CHARS` (4,000) per dataset and `APP_NOTES_CAP_CHARS` (2,000) app-wide. A
write past it is a 422 naming both numbers, and so is a restore that would pass it. The cap is
what keeps notes small enough to sit beside the findings in a small model's context.

Editing a removed item is a 409: restore it first.

## A dismissal is a note

Dismissing a finding (`health.md` § Dismissals and resurfacing) writes a `preference` item
whose text is the reason (or "Dismissed the health finding …" when none was given), whose
`finding_id` names the finding, and whose `fingerprint` records the finding's count then. One
mechanism, not two: the report hides the finding, and the analyst will read the reason as a
standing preference. A new dismissal of the same finding soft-deletes the old one, so a
finding never has two reasons. Removing the note undoes the dismissal. Restoring an older
dismissal while a newer one is live is a 409, for the same reason. Dismissals count toward
the dataset's cap like any other note, since the analyst reads them.

## Routes

`backend/routers/notes.py`. Each scope has list (`include_deleted` optional; the response
carries `used_chars` and `cap_chars`), add, `PATCH` (`text`, `position`, `decided_at`, where
an explicit null clears `decided_at`), `DELETE` (soft, returns the item) and
`POST .../restore`:

- `/datasets/{id}/notes`, `/datasets/{id}/notes/{item_id}`; an item of another dataset is a 404.
- `/app-notes`, `/app-notes/{item_id}`.

## The goal fields

Three columns on `Dataset` (migration `a9c4e2f7b1d8`):

- `goal`: free text, what the dataset is for, separate from `description` (which usually says
  where the images came from). `""` means none; it is stripped on write, so a goal of spaces
  is none. At most `GOAL_MAX_LEN` (2,000) characters.
- `target_resolution`: the short side the dataset trains at, `TARGET_RESOLUTION_MIN` to
  `TARGET_RESOLUTION_MAX` (64 to 16,384); NULL means the health report's default. A number
  beside the goal so no check has to parse prose.
- `health_summary`: the card badge's counts (`health.md` § The card badge).

`PATCH /datasets/{id}` applies the first two through `_apply_goal_fields`, on both the rename
and the plain path. `target_resolution` is read through `model_fields_set`: absent leaves it
alone, an explicit null clears it. All three are in `list_datasets`' hand-built `DatasetOut`;
`test_list_datasets_fills_every_dataset_out_field` in `backend/tests/test_notes_http.py`
fails on any `DatasetOut` field that path leaves at its default. Writing the goal bumps
`Dataset.updated_at`, which evicts the Stats and health caches; that is correct, since the
goal changes which findings apply. `duplicate_dataset` copies the goal and target.
