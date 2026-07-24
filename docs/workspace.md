# Workspace

The app shell: the top bar, background jobs, server control, hardware meters, split view, dialogs, logs, the file browser, and the booru lookup.

## Dialogs

Every dialog in the app can be driven from the keyboard and is announced as a dialog by screen readers. **Esc** closes the one you are in — when a folder picker is open on top of another dialog, it closes only the picker. **Tab** and **Shift+Tab** cycle through the dialog's own controls without wandering into the page behind it, and focus returns to the button you opened it from. Clicking the dimmed background closes a dialog only where it always has (the import and ComfyUI dialogs); a confirmation for something destructive never closes that way — use **Esc** or **Cancel**.

## Background jobs & the top bar

Every long-running operation — captioning, scoring, import, export, detection, versioning, generation — runs as a background job. You can keep working while it runs, and queue more jobs on top of it.

The centre of the top bar shows what is happening:

- **Running job** — a progress pill with the job's label, a progress bar, and a `done / total` count. Jobs that cannot report a total (a few show no percentage) display an indeterminate bar instead. The **×** cancels the running job.
- **Queued jobs** — an `N queued` badge followed by a chip per waiting job, each with its own **×** to drop just that one. Beyond three, the rest collapse into **+N more**; hover it to see their names.
- **Cancel all** — cancels every queued job *and* the one currently running.
- **Uploading images** — drag-and-drop uploads get their own pill with a file count; it turns amber if any file failed. It persists here if you navigate away mid-upload.
- When nothing is running the bar reads **Ready**.

Jobs run one at a time, in submission order, so queueing several runs back to back is the normal way to work.

## Restarting & shutting down

Two buttons sit at the right of the top bar. Both ask for confirmation first, and both are disabled while the other is in progress.

- **Restart** (circular arrow) — restarts the server in place, without needing terminal access. Any running jobs are interrupted. A full-screen overlay covers the app while it waits, and the page reloads itself the moment the server answers. If it takes longer than about 25 seconds the overlay explains what it is waiting for and offers a manual **Reload now** button — it never gives up on its own, because a cold start that loads PyTorch can legitimately take a while.
- **Shut down** (power icon) — stops the server process. On Windows the terminal window closes by itself after a clean shutdown.

> **The server only comes back on its own if you started it with `manage`** (`manage.ps1 start` / `./manage.sh start`, or `Crucible.bat`). Those scripts run the server in a loop that relaunches it after a restart. If you started uvicorn by hand, Restart stops the server and you will need to start it again yourself.

Jobs that were running when the server stopped are not resumed — they are marked failed with "Interrupted by server shutdown or restart" so nothing shows as stuck forever in the [Logs](#logs) history.

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

**Errors tab**
- Captures JS runtime errors (`window.onerror`), unhandled promise rejections (`unhandledrejection`), and React render errors (ErrorBoundary)
- Each entry shows timestamp, type badge (`error` / `rejection` / `render`), message, source file/line, and a collapsible stack trace
- **Copy Errors** exports all entries as plain text; **Clear** removes them and resets the sidebar badge

**Persistent overlay**: a fixed bottom panel auto-opens whenever a new JS error is captured, showing the same entries without requiring navigation to the Logs page. Close it with **×** — the sidebar badge count remains until errors are cleared.

## File Browser

A three-panel filesystem explorer built into the app:

- Left panel: drive roots + quick-access shortcut to the datasets folder
- Centre panel: breadcrumb navigation, file list with **sort by Name / Size / Modified date** (click column header to toggle ascending/descending), **Images only** toggle to hide non-image files, context menu (rename / delete / import into dataset)
- Right panel: image preview + dimensions/format/size metadata + generation metadata (A1111 / ComfyUI)
- Create folders, rename files and directories, delete items (syncs DB records automatically)
- Import any folder of images directly into an existing dataset without leaving the browser

## Booru Tag Lookup

Search booru image boards for tag vocabulary when building tag lists for your training subjects:

- Searches **Safebooru** (SFW) or **Gelbooru** (requires API key + user ID in `.env`)
- Shows tag name, category (character / artist / copyright / general / meta), and post count
- Configurable result limit (20 / 50 / 100); results cached for 5 minutes
- Copy individual tags or the full list to clipboard
