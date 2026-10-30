# App-stored prompts plan: moving saved prompts out of the browser

Designed and approved on 2026-10-30, with implementation starting the same day. Order and
progress are in [roadmap.md](../roadmap.md) § M5; the ruling is
[decision 3](../decisions/03-app-stored-prompts.md).

**Lifecycle**: this file is transient. As each PR lands, move its durable rationale into the
topic file that owns the code (`docs/dev/app-stored-data.md`, for most of it). When the last PR
lands, delete this folder and its `TRANSIENT_DIRS` entry in `scripts/check_docs.py`.

## Why

A user asked for LLM instructions and similar data to live in the app. Today they sit in
browser storage, so they disappear when browser data is cleared and differ between browsers
and between `localhost` and a LAN IP. This work moves that user-written data into the SQLite
database and carries over what browsers already hold, without losing it.

**Moves:** caption prompt presets, ComfyUI "Generate prompts" settings, the captioning
workflow blob (`customPrompt`, `detectPrompt`, pipeline steps), and declared empty categories.

**Stays in the browser:** page sizes, collapse state, gallery state, per-dataset filter blobs,
`CAPTION_DEFAULT_*` and job ids. These are per-browser preferences or short-lived state.

## Backend

One new migration, and register the new models in `backend/models/__init__.py`.

| Data | Storage | Routes |
|---|---|---|
| Presets | New table `caption_prompt_presets` (`id`, `name`, `model`, `style`, `prompt`, `created_at`). `model` may be `""` because legacy presets can have none. | GET, POST and DELETE under a new `app_data` router |
| Categories | New table `declared_categories` (`name` as primary key, `String(255)` matching `Dataset.category`) | GET, POST, DELETE and a rename taking its names in the body, because a name can contain `/` |
| Workflow blob | New nullable `ThresholdSettings.captioning_workflow`, `JSON(none_as_null=True)` | GET, PUT and DELETE under the same router, with a size cap on PUT |
| Comfy settings | New nullable `ComfyPlan.gen_settings`, `JSON(none_as_null=True)` | GET and PUT `/comfy/plans/{id}/gen-settings` in `backend/routers/comfy.py`, bounded like `GeneratePromptsRequest` (instructions max 20000) |

`none_as_null` matters because NULL means "never saved", and a plain JSON column stores `None`
as JSON `null`.

Reuse `sqlite_insert(...).on_conflict_do_nothing` (`backend/routers/labels.py`) for category
inserts and for creating the settings row if it doesn't exist, and `ComfyLibraryPrompt` with
its routes as the CRUD pattern.

**`POST /app-data/legacy-import`** runs in one transaction and takes any subset of the four
sections. It validates each entry separately, so one bad entry can't reject the batch, and
returns a report per entry:

- **Presets:** insert, skipping exact matches on `(name, model, style, prompt)`.
- **Categories:** union.
- **Workflow:** store only if NULL. Otherwise report `same` or `conflict`, using Python dict
  equality so key order doesn't matter.
- **Comfy:** the same rule per plan. An entry for a missing plan is reported as discarded.

## Frontend

**Writer** (`frontend/src/utils/serverWriter.ts`): serializes writes per resource, sends with
`fetch` and `keepalive`, and keeps retrying a failed write until it lands. Review of PR 3 replaced
the original "retry once, then toast", which lost a failed or queued write on close. Details are in
`docs/dev/app-stored-data.md`.

**`frontend/src/hooks/useDebouncedPersist.ts`:** add an optional `write` parameter, defaulting
to `savePersisted`, so the ten existing call sites are untouched. With a custom writer, the
hook skips any value equal to the last one written, starting from the value at mount. Without
this, the hook's write on mount would fill NULL with defaults and StrictMode's simulated
cleanup would flush immediately. Keep the unmount-only flush rule.

**Loading gate:** an outer component runs the query and shows an error with Retry on failure,
so a page never mounts on defaults. Once data arrives, the inner component mounts and stays
mounted, because it reads the value only in `useState` initializers. Queries use `gcTime: 0`,
`staleTime: 0` and `refetchOnWindowFocus: false`, and check the response's shape, because the
SPA catch-all can answer an API GET with 200 HTML. Used by `CaptioningPage` and
`GeneratePromptsModal`.

**Consumers:**

- **Presets:** a new react-query hook, `frontend/src/hooks/usePromptPresets.ts`, replaces the old
  zustand store, which is deleted along with its unused `update` action. Saving becomes async, so the save toasts in `CaptioningPage` and the input clearing
  in `PromptPresetManager` move to run after success, with an error toast on failure.
- **Comfy:** `GeneratePromptsModal` gets the gate, and its GET waits on `settled()` first. Add
  `maxLength={20000}` to the textarea. `comfy-genprompts-job-*` stays in the browser.
- **Captioning:** the gate supplies the blob. `hasStoredWorkflow` becomes "the server value is
  not NULL". Both reset paths, in `CaptioningPage` and `SettingsPage`, send DELETE through the
  writer. The filter blobs in the Settings reset stay local, and the user docs must say that
  part is per-browser.
- **Categories:** `DatasetsPage` replaces `emptyCategories` state with a query plus mutations.
  This removes whole-list overwrites, so a stale tab can't erase or resurrect a category.

## Copying existing browser data

A new `legacyImport` module starts once from the `else` branch of `frontend/src/main.tsx`,
without blocking render.

1. It reads the legacy keys. The presets key needs zustand's `{state: {presets}}` wrapper
   removed. The Comfy scan must exclude `comfy-genprompts-job-*`.
2. It skips default values: a Comfy entry equal to `GEN_DEFAULTS`, a workflow equal to its
   defaults, or an empty category list. The old code wrote those on every visit, so otherwise
   an empty copy could beat a real one.
3. It posts the sections not yet marked done, with a 15-second timeout, since the axios client
   sets none. Each section's query waits on this promise, which never rejects.
4. After a successful response, it records the sections in a `crucible-legacy-import-v1`
   localStorage marker. **Legacy keys are kept** for one release as a rollback copy.
5. Conflicts are saved under that marker and shown in a small `ModalShell` dialog offering
   **Use this browser's copy** or **Keep current**. Closing the dialog doesn't lose them.
   Entries the server rejected stay in the browser and are named in a toast.

Each consumer PR adds its own import section, so no data is left behind between PRs. A crash
before the marker is written is safe, because re-running the import changes nothing.

## Accepted limits

- **Last save wins** between tabs and across `--lan` for the workflow blob and Comfy settings.
  These become shared per install, with no per-user separation.
- **Tab close:** a write that would take in-flight keepalive bodies over about 60 KiB can be lost
  if the tab is closing. A localStorage journal was rejected, because replaying it could overwrite newer data from another browser.
- **Deleted presets can return** when a second browser does its first import, since an import
  only adds.
- **A tab left open on the previous build** can still write a preset to the old key after this
  browser has marked presets done, and that preset won't be imported. It's a window of one
  update, and re-import on change would need a hash of the key to fix it.
- **Backup restore:** restoring a database backup from before the import won't re-import,
  because the marker lives in the browser. The legacy keys are still there during the
  one-release window.

## PR notes

The PR list itself is in the roadmap. What each one needs beyond the design above:

1. **Backend.** Tests in a new `test_app_data_http.py` on `conftest.api_env`, plus additions to
   `backend/tests/test_http_smoke_crud.py`. `api_env` uses `create_all`, so run
   `scripts/check_migrations.py` to exercise the migration. Re-check the Alembic head before
   merging: `comfy-per-row-subfolders`, `feat/scoring-arc` and `style-embedding-measurement`
   all carry migrations from older revisions, so whichever lands second must re-parent.
2. **Presets.** Extend `frontend/e2e/insecure-origin.spec.ts` with cleanup, since the DB is now
   shared and CI retries once. Add a legacy-import spec and a reset helper in
   `frontend/e2e/helpers.ts`. PM-024's reference to the deleted store would fail `check_docs.py`,
   so annotate that line without rewriting the history.
3. **Comfy settings.** Includes the conflict dialog, moved here from PR 2 because presets merge
   and never conflict, so this is its first user. Extend `frontend/e2e/comfy.spec.ts`, which has
   no coverage of this modal today.
4. **Workflow blob.** Rewrite `frontend/e2e/captioning-run-gate.spec.ts` to seed through the API
   and clean up, because its first test's blob would otherwise leak into the second. Before
   merging, run `manage.ps1 start` against real browser data (the `run-app` skill): open
   `localhost`, then the machine's LAN IP, and check the conflict dialog and saved prompts.
5. **Categories.** Extend `frontend/e2e/datasets.spec.ts`.
6. **One release later.** Delete the legacy keys and the import code, then this folder. Leave
   the presets key alone in any browser whose marker lists `rejected` names, since those
   presets exist nowhere else, and offer them as a download before they're ever removed.

An Opus review agent checks the diff before each of PRs 2–5 opens.

## Docs, in the PR that changes the behaviour

- **`docs/dev/`:** `app-stored-data.md`, `persistence.md`, `frontend-core.md`, `datasets-page.md`, `settings.md`,
  `comfy-prompts.md`, `comfyui.md`, `database.md`, and the CLAUDE.md Documentation Map's word
  counts. Add a map row for any new topic file.
- **User-facing:** `docs/captioning.md`, `docs/comfyui.md`, `docs/datasets.md`,
  `docs/settings.md`, `docs/server.md` (the startup backup now covers this data), and
  `README.md` if it mentions any of this.

## Verification

Every PR runs the gates in CLAUDE.md § Verification. New backend tests cover import
idempotency, first upload wins, `same` versus `conflict`, one bad entry not rejecting the batch,
a discarded missing plan, and NULL staying SQL NULL.

New e2e journeys:

- Seed legacy keys in one browser context, load the app, and confirm the data appears in a
  **second context with empty storage**. This is the actual request.
- Opening the modal or Captioning page without editing leaves the server value NULL.
- A conflict shows the dialog and both choices work.
- Reset clears the server value.
