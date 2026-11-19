# The Health page, notes editor and goal fields (frontend)

The browser side of M12 phase 1: `HealthPage`, the notes editor, the goal fields in the
dataset dialogs and the card badge. The report and the routes they call are
[health.md](health.md) and [notes.md](notes.md); the family hub is
[../analyst.md](../analyst.md). How a user reads the page is `docs/analyst.md`.

## Files and query keys

| File | What |
|---|---|
| `frontend/src/pages/HealthPage.tsx` | The page: header counts, goal and baseline lines, findings, resolved, Dismissed, the dismiss dialog, and the two notes editors |
| `frontend/src/components/health/FindingCard.tsx` | One finding as a card, shared with the chat's related-findings strip (`compact` there); `severity.ts` holds the labels and badge classes |
| `frontend/src/components/health/useFindingActions.ts`, `useHealthReport.ts` | The "Open in gallery" and fix navigation, and the report's query, both shared with the chat (`chat-ui.md`) |
| `frontend/src/components/health/NotesEditor.tsx` | One scope of notes, as sections of items: add, edit in place, reorder, remove with an Undo toast |
| `frontend/src/components/common/FilteredImagesModal.tsx` | A finding's images ("Show images"), shared with the Stats page's bar click-through (`docs/dev/statistics-panels.md`) |
| `frontend/src/components/datasets/GoalFields.tsx`, `goalDraft.ts` | The goal and target resolution fields in `NewDatasetDialog` and `EditDatasetDialog`, and the draft's parsing |
| `frontend/src/components/datasets/HealthBadge.tsx` | The card and compact-row badge |
| `frontend/src/api/health.ts`, `frontend/src/api/notes.ts` | The clients, `FilterSpec` and `filterSpecParams`, and the notes query keys |

Query keys:

- `["dataset-health", datasetId]`, the report. Every finding is a count over the dataset's
  rows, so any write that changes captions, tags, scores, flags, licences or the image set
  must refresh it. It reaches them four ways: it is in `DATASET_CONTENT_SCOPE` and
  `PROVENANCE_SCOPE` (`constants/queryKeys.ts`); `TopBar`'s `IMAGE_MODIFYING_JOB_TYPES`
  handler invalidates it (`docs/dev/job-invalidation.md`); `TagConsolidatePage`'s
  `CACHE_KEYS` lists it; and **every hand-written invalidation of `["tag-stats", id]` is
  followed by one of `["dataset-health", id]`** (`BulkEditForm`, `ImageCard`,
  `SelectionToolbar`, `BulkDeleteForm`, `ImageDetailPage`, the versioning panels). Keep that
  pairing when adding a writer; a missing one leaves a Health pane beside the editing pane on
  old counts until "Check again". The Edit dialog invalidates it too, since the goal changes
  which findings apply.
- **The card badge refreshes after the report, not with it.** Building the report is what
  writes `health_summary`, so the report's one query function (`useHealthReport`, which the
  Health page and the chat both call) refetches `["datasets"]` itself,
  after the report arrives and only when its counts differ from the list's. Invalidating both
  together raced: the list came back first, still old, and stayed fresh for 30 s.
- `["dataset-notes", datasetId]` and `["app-notes"]`, the two note scopes (`datasetNotesKey`,
  `APP_NOTES_KEY` in `api/notes.ts`). A dataset-notes write also invalidates the report, since
  removing or restoring a dismissal note changes it.

## Routing

A routed, pickable dataset page at the six sites of `docs/dev/panes-routing.md` plus the pane picker: `lazyPages.ts`,
the `App.tsx` route `/datasets/:datasetId/health` (the generic `dsPageMatch` covers it),
`PageRenderer`'s `"health"` case, the `PageType` union, `PAGE_OPTIONS` and `NEEDS_DATASET`,
and the Sidebar's Health entry.

**`PageRenderer` does not key it; `HealthPage` keys its own body** (`HealthBody key={datasetId}`).
The page holds drafts and open dialogs, which must reset on a dataset switch, but no job or
persisted state, so a body-level key gives the reset without the route remounting.

## The finding row

The row is `FindingCard`, shared with the chat. A finding's buttons come from its data, never from the page: "Show images" and "Open in
gallery" only when `filter_spec` is set (the first sends it through `filterSpecParams`, which
drops unset keys, to `FilteredImagesModal`; the second encodes it with the finding's title as a
`?filter=` deep link, `docs/dev/gallery/state.md` § The `?filter=` deep link), the fix button
only when `fix` is set (a `PageType` reached through `usePaneNavigate`, or `"datasets"` for the
Datasets page), and "Dismiss…" always. `numbers` renders generically: known keys get a
readable label (`HUMAN_KEYS`), arrays of `{tag, count}` read as `tag (count)`, and a `hint`
string shows as guidance rather than a value. A new finding therefore needs no frontend change
unless a key wants a better label.

The dismiss dialog is a `ModalShell`; a failed dismiss keeps it open with a toast. In the
notes editor, a new note's text is cleared only once the server accepts it (a 422 past the
cap keeps it), and an edit re-seeds its draft from the item each time it opens, so a stale
draft from another pane is never saved back over a newer edit. Restore in
the Dismissed list removes the dismissal note, which is what un-dismissing is.

## The goal fields

`GoalFields` keeps the target as typed text, so blank can mean "no target" (`NumberField`
holds a number and cannot). `parseTarget` returns `null` for blank, a number in 64–16,384, or
`undefined` for anything else, which disables Create or Save and shows an inline alert. The Edit
dialog sends `target_resolution: null` for blank, the explicit null the backend reads as
"clear" (`notes.md` § The goal fields), and both fields join its `renameChanged` check.

## The badge

`HealthBadge` reads `Dataset.health_summary` and shows the worst level present, errors before
warnings; it is hidden when the summary is null (no report built yet) and when there are only
info findings, which are not worth a pill on every card. Its tooltip says when the counts last
changed (the write is skipped when they are equal), since they can lag (`health.md`
§ The card badge). The card and the compact row both
render it; change them together, as with the video badge.

## Tests

`frontend/e2e/health.spec.ts` opens the report, dismisses a finding with a reason, finds it
under Dismissed and as a preference note, opens another finding's images and restores the
dismissal; a second test checks the card badge. The goal fields are in `datasets.spec.ts`.
