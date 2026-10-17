# Frontend workflow findings

These are the verified findings reproduced in real Chromium against the e2e harness, which uses a
throwaway DB on :8199. The specs are in `docs/dev/gpt-review/probes/frontend/`; see `docs/dev/gpt-review/README.md` for how to run them.
Read `docs/dev/image-detail.md`, `docs/dev/gallery.md`, `docs/dev/frontend-jobs.md` and
`docs/dev/styling.md` as relevant.

## GPT-027 — [P1, GPT said P2] An unsaved caption draft is saved onto a cached neighbouring image

**Verdict:** CONFIRMED, and re-run independently. NEW. Raised to P1 because ordinary
arrow-key navigation **silently writes the wrong training caption**.

**Mechanism** (`frontend/src/pages/ImageDetailPage.tsx`):
- **Copy effect (~998-1003)**: copies `captionData` into the editor only when `!captionDirty`.
  Its only dependency is `[captionData]`.
- **Reset effect (~1005-1007)**: resets `captionDirty` when `imageId` changes.
- **Navigation to a cached image**: if the next image's `["caption", imageId]` query is warm
  (global staleTime is 30 s, `App.tsx:37`), `captionData` and `imageId` change in the same render.
  - The copy effect sees the stale `dirty=true` and skips.
  - The reset effect then clears the flag, but the copy effect does not run again.
- **Save** (~1097): uses the new `imageId` with the old text.

**Observed** (`gpt027.spec.ts`):
1. Visit A, then B, then A again.
2. Type "DRAFT-FOR-A" on A without saving, and press Next.
3. B's textarea shows "DRAFT-FOR-A" and Save is disabled.
4. Type " +edit" and Save.

Afterwards the API reports A = "ORIG-A" and B = "DRAFT-FOR-A +edit". The control, where B is not
cached, shows "ORIG-B" correctly.

**Fix notes.**
- Key the draft and dirty state to the image id, so they reset atomically when the id changes.
  For example, store `{imageId, text, dirty}` together, or remount the editor with
  `key={imageId}`.
- Preserve same-image edits across refetches.
- Regression: invert the spec into `frontend/e2e/caption-edit.spec.ts`.
- Check the other per-image editors on the page for the same two-effect pattern: tags, labels and
  provenance.

## GPT-030 — [P2] A folder import that fails asynchronously shows only "Import started"

**Verdict:** CONFIRMED. NEW. `docs/dev/image-files.md` describes only the success and
partial-failure toasts.

**Observed** (`gpt014_030.spec.ts`): importing a nonexistent folder through the gallery's Import
modal. The job fails with "Folder not found: …", but over 8 s the only toast is "Import started",
and the TopBar then says "Ready".

**Where:**
- `ImportFolderModal.tsx:53-62` toasts and closes on submit.
- `GalleryPage.tsx:697-708` watches only for `completed`.
- `DatasetsPage.tsx:344-345` silently clears the job id when it sees `failed`.

**Fix notes.**
- Handle `failed` and `cancelled` in a watcher that outlives the modal, and toast the server's
  `error`.
- Word the submit toast as "submitted", not "started".
- Fix alongside GPT-014 (`docs/dev/gpt-review/jobs-ml.md`), since both need terminal handling that cannot be missed.

## GPT-028 — [P2] A cross-dataset delete leaves the other split-view pane showing deleted images

**Verdict:** CONFIRMED. NEW. Cross-dataset selection itself is intended (`docs/dev/gallery.md`,
`selectionStore.datasetByImageId`).

**Observed** (`gpt028.spec.ts`): with one image selected in each pane (A and B), Delete was run from
A. The toast says "Deleted 2 images" and the API shows both deleted. B's pane still shows the tile
and "1 images" 5 s later.

**Where:** `SelectionToolbar.tsx:255-263` calls `invalidateDatasetContentScope(qc, datasetId)` for
the initiating dataset only.

**Fix notes.**
- Capture every affected dataset id before clearing the selection, using `datasetByImageId`, and
  invalidate each one.
- Audit the other multi-dataset selection actions: move, copy, labels, captions and provenance.

## GPT-029 — [P3, GPT said P2] Bare modal overlays lack dialog semantics and keyboard containment

**Verdict:** CONFIRMED, half known. `docs/dev/styling.md` acknowledges that SelectionToolbar's
bare `fixed inset-0` overlays have no hook. The DatasetsPage modals are **not** documented: there
are five `.dialog-bg` overlays and no `useModalBehavior` anywhere in `DatasetsPage.tsx`.

**Observed** (`gpt029.spec.ts`, `gpt029_extra.spec.ts`): in New Dataset and in Edit Captions:
- there is no `role=dialog`;
- Escape does not close the overlay;
- Tab escapes to BODY;
- Cancel returns focus to BODY.

New Dataset's "Name" label is also not associated with its input. The control, the Import-folder
modal (which uses the hook), passes. `frontend/e2e/modal-a11y.spec.ts` does not cover these
modals.

**Fix notes.**
- Move the overlays onto `useModalBehavior` (`docs/dev/styling.md`) and give each an accessible
  name.
- Associate labels with their inputs.
- Extend `modal-a11y.spec.ts`.

## GPT-031 — [P3] An invalid bulk-caption regex is reported as "No captions matched"

**Verdict:** CONFIRMED. NEW.

**Observed** (`gpt031.spec.ts`): Find & Replace with regex `(` returns 200
`{affected:0, skipped:1}`. The modal closes, the selection is cleared, and the toast says "No
captions matched". A valid non-matching regex such as `zzz+` gives an identical response and toast.

**Where:**
- `services/caption_service.py:110-112` catches the compile error and returns `skipped=N`.
- `components/caption/BulkEditForm.tsx:64-66` treats `affected===0` as "no match".

**Fix notes.**
- Return a 4xx from `compile_user_regex` failures with the error detail.
- Keep the modal open with the pattern and the selection intact.
- Check the other bulk regex endpoints (`docs/dev/bulk-ops.md`) for the same swallow.
