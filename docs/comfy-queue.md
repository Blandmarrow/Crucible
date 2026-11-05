# ComfyUI Queue

How to fill a plan's queue on the **Rows** section of the ComfyUI page: the toolbar, per-row folders, the folder list, and the prompt library. Setting up ComfyUI, loading a workflow, pinning parameters and running the queue are in [ComfyUI Generation](comfyui.md). Filling the queue from a description of the whole dataset is [ComfyUI Blueprints](comfy-structured.md).

## Building the queue

From the **Rows** toolbar:

- **+ Add row** — one empty row.
- **Paste prompts…** — one row per line of pasted text.
- **Import .txt** — one row per selected file; each file becomes a single prompt (internal line breaks collapse to spaces).
- **✨ Generate prompts…** — write prompts with an LLM using any provider configured under Settings → LLM Providers → [details](settings.md). Two fields do different jobs: **Instructions** describe *how* prompts should be written and are saved with the plan in the app, so they are the same in every browser, while **Request** describes *what* to generate this time. Instructions saved before this change are copied over the first time each browser opens the updated app. If a second browser holds a different copy, you are asked which to keep. The model is told which prompts already exist so it diverges from them, and duplicates are dropped. There are two ways to run it:
  - **Generate N more** makes one batch and puts it in the review box, where you edit or delete lines before clicking **Add N rows**.
  - **Generate until N** runs in the background until the queue holds N prompts, adding rows batch by batch **without a review step**. You can close the window, leave the page, or reload — it keeps going, shows up in the progress bar at the top, and can be stopped from either place. Rows already added are always kept when it stops. Because nothing is reviewed by eye first, glance over the new rows before running them.
- **Edit prompts…** — find/replace, prepend, append, or remove across every row's prompt or just the selected ones, with an optional regex mode.
- **Set folder** — give every selected row the same destination folder (see below).
- **Delete selected** removes rows; **Reset failed** puts failed rows back to pending.

The **Blueprint** tab is not a toolbar button but its own section of the plan. Describe the whole dataset once (characters, places, camera angles, alone and in pairs) and it expands into rows, each already filed in its folder → [details](comfy-structured.md). To take those rows on through scoring, captioning and export in one press, see [Automated Runs](runs.md).

## Folders

Each row has its own **Folder**, the leftmost column, which decides where that row's images are filed — so one run can produce a whole folder structure instead of a single pile. Type a path like `salt-fen/iron-knight` into the cell, or tick several rows and use **Set folder** to give them all the same one. Blank means the run's base folder. Nested paths are written with `/`, folders that do not exist yet are created as the run starts, and the images are named after the folder rather than the plan.

**Folders** are listed under the plans, with a row count each. Click one to show only its rows (and its subfolders' rows), or **All rows** to show them all. The table groups rows under a header per folder, which you can fold. Above it, a filter box matches prompt text and folder names, and the status buttons show only pending, running, failed or done rows. Changing what is shown clears the selection, so an action never touches rows you cannot see.

A folder's **⋯** menu (or a right-click) makes a **New folder inside…**, or does **Rename…**, **Move to…** and **Delete…**. A new folder stays in the list while it is still empty. Rename and Move take the folder's rows with them. Delete removes the folder's rows, but the images they made stay in the gallery. None of these works while the plan is running.

**Rows made by the blueprint** carry a lock in their Folder cell. The blueprint counts the images in each of its folders, so moving one of those rows takes it out of the blueprint. **Set folder** asks first, and offers **Move and unlink** (the blueprint then sees its old folder as short, and the next Top up refills it), **Move hand-made rows only**, or **Cancel**. A folder holding blueprint rows cannot be renamed, moved or deleted from the list: rename it in the **Blueprint** tab instead.

## Editing rows

Cells edit in place. Editing a row's parameters after it has run resets it to pending, so it will be picked up by the next run — **changing a folder does not**, since it only affects where the *next* image goes, and a finished row keeps its image. To set one parameter column across the whole queue at once, use the ✎ in that column's header — including clearing it back to the default.

## Prompt library

A single library shared across every dataset and plan, so a set of prompts written once is reusable everywhere. Open it with **Library…**:

- **Library** tab — prompts grouped into free-text categories you name. Select any and add them to the current plan, move them between categories, or delete them.
- **Other plans** tab — browse the prompts in any other plan, in any dataset, and copy or move them into this one.

**Save to library** stores the selected rows' prompts under a category. Both directions carry prompt *text* only — the rest of a row's parameters come from the plan you add them to. Duplicates within a category are skipped.
