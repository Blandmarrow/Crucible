# Workspace

The app shell: dialogs, the top bar and background jobs, hardware meters, split view, logs, and the booru lookup. Starting, restarting and shutting down the server, and its database backups, are covered in [Server](server.md); the filesystem explorer in [File Browser](file-browser.md).

Available from: the top bar, the bottom of the sidebar, and the **Logs** and **Booru Browser** sidebar items; dialogs and Split View work on every page.

## Dialogs

Confirmation prompts and the app's main dialogs — the folder picker, folder import, move-to-dataset, set-provenance, the ComfyUI and version dialogs, every dialog on the Datasets page, and every form the gallery's selection toolbar opens — can be driven from the keyboard and are announced as dialogs by screen readers. **Esc** closes the one you are in — when a folder picker is open on top of another dialog, it closes only the picker. **Tab** and **Shift+Tab** cycle through the dialog's own controls without wandering into the page behind it, and focus returns to the button you opened it from. Clicking the dimmed background closes a dialog only where it always has (the import, ComfyUI and New Category dialogs); a confirmation for something destructive never closes that way — use **Esc** or **Cancel**. While an upscale, LUT, crop, watermark-removal or thumbnail run started from the selection toolbar is in progress, its dialog stays open on **Esc**, just as its **Cancel** button is disabled: the dialog reports the result when the run ends.

Some smaller pop-ups do not yet have all of this: the detect, crop and watermark-removal forms on an image's detail page, the generation-metadata view in the gallery, the [File Browser](file-browser.md)'s import, new-folder and delete prompts, the Statistics image viewer, bucket list and settings drawer, and a few ComfyUI pop-ups (paste prompts, run defaults, set a column on all rows). Some of them close on **Esc** and some do not, and **Tab** can wander out into the page; each has a **Cancel** or close button, which always works. The two video dialogs are not among them: **Extract frames** and **Re-extract at full resolution** have the full keyboard behaviour, and the re-extract one can be closed mid-run without stopping anything → [Frame extraction](video-extraction.md) · [Re-extracting frames](video-reextract.md).

## Background jobs & the top bar

Every long-running operation — captioning, scoring, import, export, detection, versioning, generation — runs as a background job. You can keep working while it runs, and queue more jobs on top of it.

The centre of the top bar shows what is happening:

- **Running job** — a progress pill with the job's label, a progress bar, and a `done / total` count. Two things can be missing, independently. A job that cannot report progress at all shows an indeterminate bar. A job that *can* show a percentage but is in a phase that finishes no items — frame extraction while it is still finding cuts, say — shows the bar and **no count**, rather than a misleading `0 / 0`. The **×** cancels the running job.
- **Queued jobs** — an `N queued` badge followed by a chip per waiting job, each with its own **×** to drop just that one. Beyond three, the rest collapse into **+N more**; hover it to see their names.
- **Cancel all** — cancels every queued job *and* the one currently running.
- **Uploading files** — drag-and-drop uploads get their own pill with a file count; it turns amber if any file failed. It persists here if you navigate away mid-upload.
- When nothing is running the bar reads **Ready**.

Jobs run one at a time, in submission order, so queueing several runs back to back is the normal way to work.

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
- A job that finished but lost some of its items shows an amber line below the row saying how many, and why when the cause is known — a captioning run cut short by a provider timeout names the provider and its timeout, so you can act on it from [Settings → LLM Providers](settings.md). This is read from the stored job record, so it is still there after a reload or on a job you were not watching
- Plain-text filter input to search by label, type, or dataset ID
- **Refresh** button to re-fetch the list on demand
- Finished jobs are kept for **30 days**, and the 500 most recent runs are always kept regardless of age. Older rows are cleaned up when the server starts, so job history does not grow without bound. Jobs still queued or running are never removed.

**Errors tab**
- Captures JS runtime errors (`window.onerror`), unhandled promise rejections (`unhandledrejection`), and React render errors (ErrorBoundary)
- Each entry shows timestamp, type badge (`error` / `rejection` / `render`), message, source file/line, and a collapsible stack trace
- **Copy Errors** exports all entries as plain text; **Clear** removes them and resets the sidebar badge

**Persistent overlay**: a fixed bottom panel auto-opens whenever a new JS error is captured, showing the same entries without requiring navigation to the Logs page. Close it with **×** — the sidebar badge count remains until errors are cleared.

## Booru Tag Lookup

Search booru image boards for tag vocabulary when building tag lists for your training subjects:

- Searches **Safebooru** (SFW) or **Gelbooru** (requires an API key + user ID, set in Settings → API Keys or in `.env`)
- Shows tag name, category (character / artist / copyright / general / meta), and post count
- Configurable result limit (20 / 50 / 100); results cached for 5 minutes
- Copy individual tags or the full list to clipboard
