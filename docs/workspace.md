# Workspace

The app shell: the top bar, background jobs, server control, hardware meters, split view, dialogs, logs, the file browser, and the booru lookup.

## Dialogs

Confirmation prompts and the app's main dialogs — the folder picker, folder import, move-to-dataset, set-provenance, and the ComfyUI and version dialogs — can be driven from the keyboard and are announced as dialogs by screen readers. **Esc** closes the one you are in — when a folder picker is open on top of another dialog, it closes only the picker. **Tab** and **Shift+Tab** cycle through the dialog's own controls without wandering into the page behind it, and focus returns to the button you opened it from. Clicking the dimmed background closes a dialog only where it always has (the import and ComfyUI dialogs); a confirmation for something destructive never closes that way — use **Esc** or **Cancel**.

Some smaller pop-ups opened from a page or the selection toolbar — the bulk caption, score and detect forms among them — do not yet have all of this. They always have a **Cancel** button; use it rather than **Esc**. The two video dialogs are not among them: **Extract frames** and **Re-extract at full resolution** have the full keyboard behaviour, and the re-extract one can be closed mid-run without stopping anything → [details](video.md).

## Background jobs & the top bar

Every long-running operation — captioning, scoring, import, export, detection, versioning, generation — runs as a background job. You can keep working while it runs, and queue more jobs on top of it.

The centre of the top bar shows what is happening:

- **Running job** — a progress pill with the job's label, a progress bar, and a `done / total` count. Two things can be missing, independently. A job that cannot report progress at all shows an indeterminate bar. A job that *can* show a percentage but is in a phase that finishes no items — frame extraction while it is still finding cuts, say — shows the bar and **no count**, rather than a misleading `0 / 0`. The **×** cancels the running job.
- **Queued jobs** — an `N queued` badge followed by a chip per waiting job, each with its own **×** to drop just that one. Beyond three, the rest collapse into **+N more**; hover it to see their names.
- **Cancel all** — cancels every queued job *and* the one currently running.
- **Uploading files** — drag-and-drop uploads get their own pill with a file count; it turns amber if any file failed. It persists here if you navigate away mid-upload.
- When nothing is running the bar reads **Ready**.

Jobs run one at a time, in submission order, so queueing several runs back to back is the normal way to work.

## Starting up

`manage start` — `.\manage.ps1 start`, `./manage.sh start`, or **Start** in `Crucible.bat` — opens your browser on `http://localhost:8000` immediately, before the server is ready. Until it is, that page is a placeholder: the Crucible mark animating over "Starting Crucible…". It swaps itself for the app the moment the server answers, so there is nothing to click and no need to reload. A first launch, or the first one after an update, also rebuilds the frontend and loads PyTorch, so a couple of minutes is normal; past a minute the page says as much and offers a manual **Reload now** button.

Only `start` does this — `dev` leaves your browser alone. Two other things worth knowing:

- To launch without a browser (a headless machine, or you simply have the tab open already), set `CRUCIBLE_NO_BROWSER=1`: `CRUCIBLE_NO_BROWSER=1 ./manage.sh start`, or `$env:CRUCIBLE_NO_BROWSER=1` before `.\manage.ps1 start` on Windows. Nothing opens and no placeholder runs; startup is otherwise identical.
- If something else already holds port 8000 — usually a copy of Crucible you forgot to stop — the placeholder steps aside silently and no browser opens, so what you see is the server's own "address already in use" error rather than a page from the other instance.

## Restarting & shutting down

Two buttons sit at the right of the top bar. Both ask for confirmation first, and both are disabled while the other is in progress.

- **Restart** (circular arrow) — restarts the server in place, without needing terminal access. Any running jobs are interrupted. A full-screen overlay covers the app while it waits, and the page reloads itself the moment the server answers. If it takes longer than about 25 seconds the overlay explains what it is waiting for and offers a manual **Reload now** button — it never gives up on its own, because a cold start that loads PyTorch can legitimately take a while.
- **Shut down** (power icon) — stops the server process. On Windows the terminal window closes by itself after a clean shutdown.

> **The server only comes back on its own if you started it with `manage`** (`manage.ps1 start` / `./manage.sh start`, or `Crucible.bat`). Those scripts run the server in a loop that relaunches it after a restart. If you started uvicorn by hand, Restart stops the server and you will need to start it again yourself.

Jobs that were running when the server stopped are not resumed — they are marked failed with "Interrupted by server shutdown or restart" so nothing shows as stuck forever in the [Logs](#logs) history.

## Database backups

Everything Crucible knows — captions, scores, detections, version history — lives in one SQLite file (`dataset_manager.db`, next to `manage.ps1`/`manage.sh`). When the server starts it checks that file for corruption and, if it is healthy, saves a timestamped copy into a `backups/` folder beside it, keeping the **five most recent**. This happens in the background a moment after startup; nothing waits on it.

Three things worth knowing:

- A start within **15 minutes** of the newest backup does nothing — no check, no copy. Only five copies are kept, so a run of quick restarts would otherwise discard the whole backup history in a few minutes, which is the opposite of what it is for. Restarting to force a fresh copy therefore does not work; a backup is a periodic safety net, not a snapshot button. For a point-in-time copy you control, use [dataset versioning](versioning.md).
- A database that fails the integrity check is **not** backed up. That is deliberate — copying a damaged file in would push the newest good backup one slot closer to deletion. The failure is written to the server's console output.
- Backups are full copies on the same disk. They protect against a bad edit or a corrupted database, not against losing the drive. If a dataset matters, copy a backup somewhere else.

To restore one, stop the server, rename the backup over `dataset_manager.db`, and start again. Image files are not part of the backup — for those, use [dataset versioning](versioning.md).

## Hardware meters

The bottom of the sidebar shows live **CPU**, **RAM**, and **GPU** meters, refreshed every few seconds. The GPU row shows VRAM use and is labelled with your card's name — useful for checking headroom before loading a large captioning model, or confirming VRAM was actually released after a model unload. Rows show "No data" on machines where a reading is unavailable (for example, no supported GPU).

## Split View

Split the main content area into two independently operating panes:

- Toggle via the **Columns** icon in the top-right toolbar
- Split any pane horizontally or vertically with the split buttons in the pane header, split panes can be split again
- Each pane has its own page selector and dataset selector — run Gallery in one pane and Stats in another, for example
- Drag the resize handle between panes to adjust the split ratio
- Close all panes to return to single-view

## Logs

A global **Logs** page (sidebar nav item) with two tabs:

**History tab**
- Lists up to 200 recent background jobs, newest first
- Each row shows status badge (`pending` / `running` / `done` / `failed` / `cancelled`), label, dataset ID chip, relative timestamp (absolute on hover), duration, and item progress count
- Failed jobs display their error message below the row in red
- Plain-text filter input to search by label, type, or dataset ID
- **Refresh** button to re-fetch the list on demand
- Finished jobs are kept for **30 days**, and the 500 most recent runs are always kept regardless of age. Older rows are cleaned up when the server starts, so job history does not grow without bound. Jobs still queued or running are never removed.

**Errors tab**
- Captures JS runtime errors (`window.onerror`), unhandled promise rejections (`unhandledrejection`), and React render errors (ErrorBoundary)
- Each entry shows timestamp, type badge (`error` / `rejection` / `render`), message, source file/line, and a collapsible stack trace
- **Copy Errors** exports all entries as plain text; **Clear** removes them and resets the sidebar badge

**Persistent overlay**: a fixed bottom panel auto-opens whenever a new JS error is captured, showing the same entries without requiring navigation to the Logs page. Close it with **×** — the sidebar badge count remains until errors are cleared.

## File Browser

A three-panel filesystem explorer built into the app:

- Left panel: drive roots + quick-access shortcut to the datasets folder
- Centre panel: breadcrumb navigation, file list with **sort by Name / Size / Modified date** (click column header to toggle ascending/descending), **Media only** toggle to hide anything that is neither an image nor a video, context menu (rename / delete / import into dataset)
- Right panel: preview + size and modified date. For an image, also dimensions/format and generation metadata (A1111 / ComfyUI); for a video, a player you can scrub
- The video preview steps aside for anything that changes the file. Start a rename, or open the delete or import dialog, and the player is replaced by **Preview paused** until you are done — a file still being played cannot be renamed or deleted on Windows, and the player is released so the change goes through. Cancel, and the preview comes back
- If a video will not play, a short line under the filename says why. When Crucible can see from the file that no browser decodes it, it says so and confirms the file itself is fine — importing, posters and frame extraction all read it directly. When it cannot tell, it says both possibilities plainly: either the browser has no decoder, or the file could not be loaded (it may have been renamed, moved or deleted)
- Videos are listed with a film-strip icon and counted in the status bar
- Create folders, rename files and directories, delete items (syncs DB records automatically — renaming or deleting an image or video that belongs to a dataset keeps its record in step). Deleting a dataset's file or folder also removes the thumbnail or poster that went with it and updates the dataset's image and video counts, and it is refused while that dataset is busy with another job
- Some things cannot be renamed, and Crucible says which: a dataset's own folder (rename the dataset from the Datasets page instead, so its record follows), any folder *above* one, the `images` / `videos` / `thumbnails` / `.versions` folders a dataset is built from, and any folder holding images or videos that belong to a dataset — including one holding only their thumbnails or posters. Renaming a *file* is refused when the new name is already taken in that folder, or when it would claim the thumbnail name another image owns
- Moving a file into a *different* dataset is not done here: use the gallery's **Move to dataset** action, which carries the caption, provenance and thumbnail with it. The same applies to a whole folder — moving one that holds images or videos belonging to a dataset is refused wherever you point it, *including* somewhere else inside that same dataset, because the app only ever looks for a dataset's media in the images and videos folders it created for it → [how that affects rescan](gallery.md#getting-images-in). A file that belongs to a dataset also has to stay directly in that dataset's own images or videos folder, so moving one into a subfolder is refused too. Folders of your own, and files no dataset has registered, still move anywhere
- Import any folder of images directly into an existing dataset without leaving the browser; tick **Include videos** to bring video files in as well

## Booru Tag Lookup

Search booru image boards for tag vocabulary when building tag lists for your training subjects:

- Searches **Safebooru** (SFW) or **Gelbooru** (requires an API key + user ID, set in Settings → API Keys or in `.env`)
- Shows tag name, category (character / artist / copyright / general / meta), and post count
- Configurable result limit (20 / 50 / 100); results cached for 5 minutes
- Copy individual tags or the full list to clipboard
