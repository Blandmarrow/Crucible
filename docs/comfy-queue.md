# ComfyUI Queue

How to fill a plan's queue on the **Rows** section of the ComfyUI page: the toolbar, per-row folders, the folder list, and the prompt library. Setting up ComfyUI, loading a workflow, pinning parameters and running the queue are in [ComfyUI Generation](comfyui.md). Filling the queue from a description of the whole dataset is [ComfyUI Blueprints](comfy-structured.md).

## Building the queue

From the **Rows** toolbar:

- **+ Add row** — one empty row.
- **Paste prompts…** — one row per line of pasted text.
- **Import .txt** — one row per selected file; each file becomes a single prompt (internal line breaks collapse to spaces). The rows go into the folder you have picked in the folder list, or the base folder when **All rows** is shown.
- **✨ Generate prompts…** — write prompts with an LLM using any provider configured under Settings → LLM Providers → [details](settings.md). Two fields do different jobs: **Instructions** describe *how* prompts should be written and are saved with the plan in the app, so they are the same in every browser, while **Request** describes *what* to generate this time. Instructions saved before this change are copied over the first time each browser opens the updated app. If a second browser holds a different copy, you are asked which to keep. The model is told which prompts already exist so it diverges from them, and duplicates are dropped. There are two ways to run it:
  - **Generate N more** makes one batch and puts it in the review box, where you edit or delete lines before clicking **Add N rows**.
  - **Generate until N** runs in the background until the queue holds N prompts, adding rows batch by batch **without a review step**. You can close the window, leave the page, or reload — it keeps going, shows up in the progress bar at the top, and can be stopped from either place. Rows already added are always kept when it stops. Because nothing is reviewed by eye first, glance over the new rows before running them.
- **Edit prompts…** — find/replace, prepend, append, or remove across every row's prompt or just the selected ones, with an optional regex mode.
- **Set folder** — give every selected row the same destination folder (see below).
- **Copy to plan…** — copy the selected rows into another plan of this dataset, or move them there. See [Copying rows between plans](#copying-rows-between-plans).
- **Delete selected** removes rows; **Reset failed** puts failed rows back to pending.

The **Blueprint** tab is not a toolbar button but its own section of the plan. Describe the whole dataset once (characters, places, camera angles, alone and in pairs) and it expands into rows, each already filed in its folder → [details](comfy-structured.md). To take those rows on through scoring, captioning and export in one press, see [Automated Runs](runs.md).

## Folders

Each row has its own **Folder**, the leftmost column, which decides where that row's images are filed — so one run can produce a whole folder structure instead of a single pile. Type a path like `salt-fen/iron-knight` into the cell, or tick several rows and use **Set folder** to give them all the same one. Blank means the run's base folder. Nested paths are written with `/`, folders that do not exist yet are created as the run starts, and the images are named after the folder rather than the plan.

**Folders** are listed under the plans, with a row count each. Click one to show only its rows (and its subfolders' rows), or **All rows** to show them all. The table groups rows under a header per folder, which you can fold. Above it, a filter box matches prompt text and folder names, and the status buttons show only pending, running, failed or done rows. Changing what is shown clears the selection, so an action never touches rows you cannot see.

A folder's **⋯** menu (or a right-click) makes a **New folder inside…**, or does **Rename…**, **Move to…** and **Delete…**. A new folder stays in the list while it is still empty. Rename and Move take the folder's rows with them. Delete removes the folder's rows, but the images they made stay in the gallery. None of these works while the plan is running.

**Rows made by the blueprint** carry a lock in their Folder cell. The blueprint counts the images in each of its folders, so moving one of those rows takes it out of the blueprint. **Set folder** asks first, and offers **Move and unlink** (the blueprint then sees its old folder as short, and the next Top up refills it), **Move hand-made rows only**, or **Cancel**. A folder holding blueprint rows cannot be renamed, moved or deleted from the list: rename it in the **Blueprint** tab instead.

## Editing rows

Each row starts with a ⋮⋮ handle. Click it for **Move to top of folder**, **Move up**, **Move down** and **Move to bottom of folder**. Rows run in this order, so it decides which run first. To move a row into another folder, use **Set folder**.

Cells edit in place. Editing a row's parameters after it has run resets it to pending, so it will be picked up by the next run — **changing a folder does not**, since it only affects where the *next* image goes, and a finished row keeps its image. To set one parameter column across the whole queue at once, use the ✎ in that column's header — including clearing it back to the default.

## Dragging and dropping

Everything the toolbar and menus do with rows and prompts also works by dragging, if you prefer the mouse:

- Drag a row by its ⋮⋮ handle onto a folder in the list or a folder's header in the table to file it there, or between two rows to put it in that spot. Drag several at once by ticking them first.
- Drop rows on another plan in the list to copy them there; hold **Shift** as you drop to move them instead.
- Drop rows on a library category to save their prompts there.
- Drag a library prompt by its handle onto the table to add it to the folder you have picked, or onto a folder to add it there.
- Drag a folder onto another folder, or onto *(root)*, to move it with its rows. Drag a category onto another category, or onto *Top level*, to move it.
- Drop `.txt` files from your computer onto the table to import them, one prompt per file. Drop a workflow `.json` onto the **Workflow template** box to load it.

A chip next to the pointer says what the drop will do. Moving rows the blueprint made asks first.

## Prompt library

A single library shared across every dataset and plan, so a set of prompts written once is reusable everywhere. Click **Library** in the toolbar to open it as a panel beside the queue; click it again, or the ✕, to close it. The page remembers whether it was open.

- **Categories nest.** Write a category as a path, like `portraits/studio`, and it shows as a tree: `portraits` with `studio` inside it. Each category shows how many prompts it and its subcategories hold. Fold a category with its arrow.
- **Search** the box at the top to find prompts by their text or their category.
- **Edit a prompt** with its ✎. A prompt that is already in that category is refused.
- **Tick prompts** to act on them:
  - **Add to plan** puts them in the current plan as new rows, in the folder you choose. It starts on the folder you have picked in the folder list.
  - **Move to…** files them under another category. A prompt already in that category is merged into it rather than stored twice.
  - **Delete** removes them from the library.
- **A category's ⋯ menu** (or a right-click) renames it, moves it inside another category, or deletes it with every prompt in it. Renaming a category to the name of one that exists merges the two.

**Save to library** stores the selected rows' prompts under a category, which can be a new path. It saves the prompt *text* only — the rest of a row's parameters come from the plan you add them to later. Duplicates within a category are skipped.

## Copying rows between plans

Two routes, both of which keep each row's folder:

- **Copy to plan…** in the toolbar copies the selected rows into another plan in this dataset. Choose **Move** to take them out of this plan instead.
- The library panel's **Other plans** tab browses the rows of any plan, in any dataset, with each row's folder shown. Tick some and use **Copy here** or **Move here**.

A copied row carries its prompt, and every other value that the receiving plan also has as a per-row column. Values for anything else are left behind, and the receiving plan's own settings apply. The new row starts as pending, even if the original had already run.

Moving rows the blueprint made asks first, as **Set folder** does: the blueprint then counts their folder as short, and the next **Top up** refills it. A move is refused while the plan it takes rows from is running, or while one of its rows is.
