# App-stored data: user-written state kept on the server

**Status: living.**

This file covers user-written data that follows the install rather than the browser: where it is stored, the one-time import of what browsers already held, the conflict dialog, and the pieces a form uses to read and save such a value. What stays in browser storage is in `docs/dev/persistence.md`.

User-written data that must follow the install rather than the browser lives in the database, behind `backend/routers/app_data.py` (prefix `/app-data`) and two routes in `backend/routers/comfy.py`. The ruling is in `docs/dev/decisions/03-app-stored-prompts.md`, and the migration from browser storage is planned in `docs/dev/server-state-plan/README.md`, which also says how far it has got.

| Data | Storage | Routes |
|---|---|---|
| Caption prompt presets | `caption_prompt_presets` | `GET`/`POST /app-data/presets`, `DELETE /app-data/presets/{id}` |
| Declared empty categories | `declared_categories` | `GET`/`POST`/`DELETE /app-data/categories`, `POST /app-data/categories/rename` |
| Captioning workflow | `ThresholdSettings.captioning_workflow` | `GET`/`PUT`/`DELETE /app-data/captioning-workflow` |
| ComfyUI "Generate prompts" form | `ComfyPlan.gen_settings` | `GET`/`PUT /comfy/plans/{id}/gen-settings` |

Three rules a change here has to keep:

- **NULL means never saved, and both JSON columns use `JSON(none_as_null=True)`.** A plain `JSON` column stores Python `None` as JSON `null`, which is not SQL NULL, so "never saved" would silently stop being true. The import's first-copy-wins rule and the Captioning page's fallback to Settings' defaults both depend on it.
- **Category names travel in the body or query, never the path**, because a name can contain `/`.
- **Saving `gen_settings` must not bump `ComfyPlan.updated_at`.** `ComfyPage` keys the workflow editor on `plan.id + plan.updated_at`, and this form saves as the user types, so a bump would remount the editor underneath them. Both write sites pin the column to itself to suppress `onupdate`.

`POST /app-data/legacy-import` is the one-time carry-over of what a browser still holds, in one transaction, and is safe to re-run. Presets merge, skipping exact matches. Categories union. A single-value section is stored only if NULL, and otherwise reports `same` or `conflict` by dict equality, so key order doesn't matter. Entries are validated one at a time, so a bad one is reported instead of costing the batch a 422, and a Comfy entry naming a missing plan is reported as `discarded`. The route holds a module-level lock, because presets have no unique constraint and two tabs loading together would each read "no match" and insert the same one.

On the client, `frontend/src/utils/legacyImport.ts` runs that import once per load, started from `main.tsx`'s `else` branch without blocking render. Every query for this data awaits `runLegacyImport()` first, so a page can't read the server before the copy arrives; it never rejects, so a failed import leaves the page working and its section unmarked for the next load. What's been copied is recorded per section under `LEGACY_IMPORT_MARKER_KEY`, and it's the marker that stops a re-import, not the server's dedupe: a preset deleted after importing would otherwise come straight back on reload. The old keys are read but never deleted, as a copy to roll back to for one release.

A form that reads a saved value in its `useState` initializers has two pieces to use:

- **`frontend/src/components/common/ServerStateGate.tsx`** holds it back until the value arrives. Starting from defaults instead would let its auto-save write those over the real data, so a failed load offers Retry, never defaults. It keeps the first value it's given, because the form owns the state from then on. That value must come from a read made after it mounted (`isFetchedAfterMount`): `gcTime: 0` caches nothing between mounts only while no other observer holds the query, and a second pane's gate shares it with the first.
- **`frontend/src/utils/serverWriter.ts`** (`writeToServer`) sends the writes, passed through `useDebouncedPersist`'s `write`. One goes at a time per path, the latest replacing any queued, so a slow request can't land after a newer one. A failed write stays owed and keeps retrying until it lands or a newer one replaces it, and the user is told once it has failed twice; a 4xx stops instead, since retrying can't help. `serverWritesSettled` is what a reopening form's load awaits so it can't read the value a write is about to replace, and it **rejects** while a write is failing, because a form that loaded then would start from the old value and its next save would throw away the one being retried. It uses `fetch` for `keepalive`, which axios lacks, so a write outlives the page. On `pagehide` whatever is owed goes at once, queued or waiting to retry, since no timer runs after that. The keepalive limit of 64 KiB counts everything in flight, so a write that would pass it goes without, and can be lost if the tab is closing.

`frontend/src/components/common/LegacyConflictsDialog.tsx`, mounted once in `App.tsx`, asks what to do when this browser's copy lost to a different one already on the server. Those copies stay in the marker's `conflicts` until the user chooses, so closing the dialog decides nothing and it asks again on the next load. Choosing this browser's copy reloads the page, because a form already open holds the old copy and its next save would put it back.

`frontend/src/hooks/usePromptPresets.ts` is how a component reads and changes presets. Its `save` resolves to whether it worked, so a caller clears its form and reports success only then, and it reports failures itself. Show its `emptyMessage` rather than a literal when the list is empty: while the query is pending or has failed, "No presets saved yet." is what convinces someone their presets are gone.
