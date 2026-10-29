# Labels — the frontend

The four surfaces a user labels through — the Settings Labels tab, the detail page's
labels panel, the detail-view hotkeys and the gallery toolbar's bulk modal — and the
shared pieces behind them: the `["labels"]` query and its invalidation helper, the
render-time bounds check on a persisted label filter, the `GalleryPage` persistence edits
a new filter field costs, and the per-image assign queue the hotkeys depend on. The
endpoints these call are `docs/dev/labels.md` (`POST /labels/assign` in particular); how
the gallery and export filters behave server-side is `docs/dev/label-consumers.md`. The
gallery's label filter control itself is `docs/dev/gallery/filters.md` § Gallery filters.

## Files and the query key

| File | Contents |
|---|---|
| `src/api/labels.ts` | `labelsApi = { list, counts, create, update, remove, reorder, assign }` |
| `src/hooks/useLabels.ts` | `useLabels`: `useQuery(["labels"], staleTime 5 min)` + memoized `byId`/`byHotkey`, and `isLoaded` (the query's `isSuccess`) for the bounds checks below. `useAssignImageLabels(imageId)`: the one assign mutation for a single image, shared by the hotkeys and the detail panel — see § Hotkeys |
| `src/components/settings/LabelsPanel.tsx` | The Settings tab body — the 20-colour palette, inline rename, hotkey capture, up/down reorder, delete via `ConfirmDialog` naming `usage_count` |
| `src/components/settings/HotkeyCaptureButton.tsx` | Captures the next keypress while focused |
| `src/components/common/LabelPicker.tsx` | `LabelCheckList` (searchable checkbox list + `footer` slot) and `LabelPicker` (the same behind a trigger). The one control behind all four picking surfaces — see `docs/dev/styling.md` § Anchored popovers, including why `placement` is not a style choice |
| `src/components/image/LabelsPanel.tsx` | Detail-page block: removable chips + an inline `LabelPicker`, one `assign` per tick through `useAssignImageLabels` |
| `src/components/gallery/LabelsBulkModal.tsx` | Dumb modal (form state only) with *Add* and *Remove* `LabelCheckList`s |
| `src/utils/keyboard.ts` | `isTextEntryTarget(e)`, adopted by both of `ImageDetailPage`'s pre-existing keydown effects |

The query key is a bare `["labels"]` with no dataset in it, because the
vocabulary is app-wide. Writers call `invalidateLabelScope(qc)` from
`constants/queryKeys.ts` rather than listing keys inline — there are four writers
(Settings, the detail panel, the hotkey, the toolbar), which is exactly the drift
that file exists to prevent. It takes **no `datasetId`** and invalidates every key
at its bare prefix, matching `invalidateProvenanceScope`: the bulk assign it
mostly serves labels a selection that can straddle datasets, so scoping to one
pane's id left other datasets' grids and card dots stale.

## Bounds-checking a persisted label filter

Both the gallery and the Export page bounds-check a label filter against the
vocabulary and drop ids whose label was deleted — the `licenseFilter` precedent.
Without it the grid silently shows zero images with nothing on screen explaining
why (the picker is hidden entirely while the vocabulary is empty, so there is not
even a control to open). In the gallery, dropping anything also resets paging.

The check is **derived during render**, gated on `useLabels().isLoaded` — the
`frameVideoId` guard in `GalleryPage` is the precedent, and `isLoaded` (the
query's `isSuccess`) is what separates "still fetching" from "the vocabulary is
genuinely empty", which `labels` alone collapses into `[]`. It is deliberately not
a mount-once `useRef` latch, which had three escapes: an empty vocabulary never
reconciled at all, a label deleted later in the session never reconciled either,
and the Export page's dataset-switch effect re-seeded the filter from a different
blob past a ref that had already fired. `handleResetToDefaults` there resets the
label trio alongside the other fourteen filters, or the debounced persist writes
it straight back into the blob it just cleared.

## GalleryPage persistence: the six edits

`GalleryPage` persistence needs **six** edits, because the filter blob is
hand-rolled there rather than using `useDebouncedPersist`: the inline type in
`loadSavedState`, the `useState` seed that reads it, `liveStateRef` (both lines),
the debounced write (the `JSON.stringify` literal **and** the dep array — a field
whose own change moves no other dep is otherwise only written by the unmount
flush, and a reload drops it), the unmount blob (the destructure **and** its
literal), and `handleResetFilters`. Neither write site merges with what is
stored, so a field present in one literal and missing from the other is deleted
on whichever path runs. See `docs/dev/persistence.md`.

**A debounced field is two of those seeds, not one.** `search` and
`detectionLabel` each commit out of a `*Input` draft through a 350 ms timer whose
only bail-out is `input === committed`, so a restore must seed **both halves**
from the one stored value, and every clear (Reset filters, the input's ×) must
clear both together. Seeding or clearing one half leaves the pair unequal, which
is exactly the state that makes the timer fire on mount and throw away the
restored page — PM-012, in `docs/dev/postmortems.md`. Persist only the committed
value; a persisted draft leaves the restore no correct choice.

## Hotkeys

`labelHotkeysEnabled` lives in `store/uiPrefsStore.ts`, not only in localStorage,
so toggling it in Settings re-renders an already-mounted detail pane in split
view.

**Conflict prevention is structural, not a blocklist.** The `[a-z0-9]` charset
cannot express Escape, Space, ArrowLeft/Right or Delete — the five keys
`ImageDetailPage` already binds — so no reserved-key set is needed and none can
go stale when a sixth binding is added. The instinct to add
`RESERVED = new Set([...])` is the version that rots. Cross-label collisions are
a server 409, pre-empted in the UI by naming which label owns the key.

The handler guards, all required: the pref is on; `paneCtx.paneId === activePaneId`;
no modal open; no modifier held; **`!e.repeat`** (a held key would fire dozens of
assigns — the guard the two existing effects do not need and this one does); and
`!isTextEntryTarget`, which is load-bearing because the caption editor is a
`<textarea>` on this page and typing "a" would otherwise label the image.
Semantics are **toggle**, so a mistyped key is undone with the same key.

**The assigns for one image are queued.** A toggle is a whole `add` or `remove`
decided on the client, so two in flight at once can reach the server in either
order. A fast double-tap sent remove + add on separate connections, the server
applied the add first, and the label ended up removed while the chip said
attached; the same key then *added* on the next press. Both the hotkey and the
detail panel therefore write through `useAssignImageLabels(imageId)`, whose
TanStack `scope` runs every assign for that image one at a time, in the order
made. It refetches only once the last queued assign settles, which is also the
rollback for a failed one. A refetch between two queued assigns would fetch a state
the next one is about to change, and overwrite its optimistic flip. For the same
reason the handler cancels any `["image", imageId]` refetch already in flight
before it flips the chip.

**Add-or-remove is read from the query cache at keypress time**, never from a value
the effect closed over. The effect re-subscribes only after React commits, so a
key pressed just after a refetch rendered toggled against the *previous* label
set: an "add" of a label already attached, which does nothing. The double-tap
journey in `frontend/e2e/labels.spec.ts` failed on every run against the
unqueued code.

`ImageDetailPage` mounts the detail panel with ``key={`labels-${image.id}`}`` —
**prefixed**, because `ProvenancePanel` is a sibling in the same children array
and already uses a bare `image.id`, and two siblings sharing one key is a
reconciliation error that renders the panel twice rather than warning about it.
