# ComfyUI Generation

Queue prompts and parameters against a running ComfyUI server and import the results straight into a dataset. Crucible drives *your* workflow rather than reimplementing one: you supply a workflow export, pin the inputs you want to vary, and fill a queue of rows. Each row is submitted to ComfyUI as one prompt, and every image it produces is imported into the dataset with a thumbnail, generation metadata, and optionally the prompt as its caption — ready to caption, score, curate, and export like any other image.

Available from: the **ComfyUI** sidebar item on any dataset page.

## Setup

Settings → **ComfyUI** tab. Both fields are global (shared by every dataset) and require **Save**:

- **Server URL** — base URL of your ComfyUI server, default port 8188 (e.g. `http://127.0.0.1:8188`). **Test connection** reports whether it is reachable before you save. The server is contacted by Crucible's backend, not by your browser — a ComfyUI on another machine, or in a container, needs an address that resolves from wherever Crucible is running.
- **Workflow folder** — the folder **Scan folder…** searches by default. **Browse…** picks it. This is a path on the machine running Crucible.

Optionally, install the **CrucibleBridge** extension into ComfyUI so **Sync from canvas** can pull the workflow you currently have open — see [extras/ComfyUI-CrucibleBridge/README.md](../extras/ComfyUI-CrucibleBridge/README.md). Without it, syncing falls back to the last prompt you queued in ComfyUI.

## Plans

A **plan** holds one workflow template, the parameters pinned on it, and a queue of rows. A dataset can have as many plans as you like — typically one per workflow, or one per experiment.

- **+ New plan** creates one; plan names are unique within a dataset. **Rename** and **Delete** act on the selected plan (deleting a plan discards its rows, but never the images it generated).
- The plan splits into two sections: **Rows** (the queue) and **Workflow & Pins** (the template).

## Loading a workflow

Four ways to get a workflow into a plan, from the **Workflow & Pins** section:

| Route | Use it when |
|---|---|
| Paste into **Workflow template** | You have the API JSON on the clipboard |
| **Load .json file** | The export is a file on the machine running your browser |
| **Scan folder…** | You keep exports in a folder on the Crucible machine — lists every `.json` found, badged by format, and only API-format files can be loaded |
| **Sync from canvas** | You want whatever is open in ComfyUI right now, without re-exporting |

**Only API-format workflows work.** This is the most common first-run problem: a normal ComfyUI save (and the `.json` in your `workflows` folder) is *UI-format*, which describes the canvas — nodes, links, positions — and cannot be executed as-is. Crucible detects it and asks for an API export instead. In ComfyUI use **Workflow → Export (API)**, or enable dev mode and use **Save (API Format)**. This is why a folder scan often finds many files but offers none of them.

**Sync from canvas** shows what it found — the live canvas (via the bridge) or your last queued prompt — before replacing anything, along with any pins that will be dropped because the parameter no longer exists in the new workflow. Pins that still match are kept.

**Import images from** controls which nodes' images are imported. Left alone (**Auto — SaveImage outputs**) it imports whatever your Save nodes write. Selecting nodes explicitly imports their images even if they are previews — so a workflow whose only output is a **PreviewImage** node still works, and nothing accumulates in ComfyUI's output folder. If your workflow has no Save node and you have not selected an output node, the panel warns you; runs would otherwise produce nothing to import.

## Pinning parameters

A pin exposes one workflow input so the queue can drive it. In **Workflow nodes**, search for a node and pin an individual input, or pin every scalar input on a node at once. Each pin gets an **alias** — the name you will see in the queue — and one of two roles:

- **Run default** — one value used by every row, edited from the chips above the queue. Good for steps, CFG, sampler, or model name.
- **per row** — gets its own column in the queue table, so each row can set it. A blank cell falls back to the run default, and then to the workflow's own value.

Exactly one pin can be marked as **★ the prompt** (always per-row). It is the target for everything prompt-shaped: pasting prompts, importing `.txt` files, the prompt library, generated prompts, and **Prompt as caption**. Until a pin is marked as the prompt, those tools stay disabled.

Integer parameters — seeds above all — take a mode that applies when a row leaves the cell blank:

| Mode | Effect |
|---|---|
| **Fixed** | Use the run default, or the workflow's value |
| **🎲 Random per row** | A new random integer for every row |
| **+1 Increment per row** | The run default (or the workflow's value) plus the row's position in the run |

## Building the queue

From the **Rows** toolbar:

- **+ Add row** — one empty row.
- **Paste prompts…** — one row per line of pasted text.
- **Import .txt** — one row per selected file; each file becomes a single prompt (internal line breaks collapse to spaces).
- **✨ Generate prompts…** — write prompts with an LLM using any provider configured under Settings → LLM Providers → [details](features.md#settings). Two fields do different jobs: **Instructions** describe *how* prompts should be written and persist with the plan, while **Request** describes *what* to generate this time. Generate a batch at a time, or run *generate until N* and let it loop; the model is told which prompts already exist so it diverges from them, and duplicates are dropped. Review and edit the results before adding them as rows.
- **Edit prompts…** — find/replace, prepend, append, or remove across every row's prompt or just the selected ones, with an optional regex mode.
- **Delete selected** removes rows; **Reset failed** puts failed rows back to pending.

Cells edit in place. Editing a row that already ran resets it to pending, so it will be picked up by the next run. To set one column across the whole queue at once, use the ✎ in that column's header — including clearing it back to the default.

## Prompt library

A single library shared across every dataset and plan, so a set of prompts written once is reusable everywhere. Open it with **Library…**:

- **Library** tab — prompts grouped into free-text categories you name. Select any and add them to the current plan, move them between categories, or delete them.
- **Other plans** tab — browse the prompts in any other plan, in any dataset, and copy or move them into this one.

**Save to library** stores the selected rows' prompts under a category. Both directions carry prompt *text* only — the rest of a row's parameters come from the plan you add them to. Duplicates within a category are skipped.

## Running

The **Run** bar takes a target **Subfolder** for the generated images and a **Prompt as caption** toggle that writes each row's prompt into its image's caption (and its `.txt` sidecar). Then choose:

- **Run pending** — every row still waiting to run. The normal case. Failed rows are *not* included until you **Reset failed**.
- **Run selected** — only the rows you ticked, whatever their status.
- **Run all** — every row regardless of status; re-runs completed prompts and generates fresh images.

Rows run one at a time, submitted to ComfyUI as ordinary prompts, with live progress on the page and in the top bar. The gallery fills in as images import — you do not have to wait for the run to finish. Each plan runs one job at a time, but different plans can run concurrently.

Anything that goes wrong stops at the row: it is marked failed with the error attached, the run continues, and nothing half-imported is left behind. Cancel from the progress bar to interrupt ComfyUI and return the in-flight row to pending. A run gives up if the server becomes unreachable, leaving untouched rows pending, so fixing the server and re-running costs nothing.

## Common problems

**Test connection fails, or a run reports it cannot reach ComfyUI.** Check that ComfyUI is running, and that the URL is reachable *from the machine running Crucible* — `127.0.0.1` means the Crucible host, so a ComfyUI elsewhere on the network, in Docker, or in WSL needs its real address rather than localhost.

**"This looks like a UI-format export" — or a folder scan finds files it will not load.** The workflow is a canvas save, not an API export. In ComfyUI use **Workflow → Export (API)** and load that file instead.

**Rows fail with a message about output nodes.** The workflow has no Save node, or the nodes chosen in **Import images from** no longer exist in it after a sync. Pick the node whose images you want under **Import images from**.

**The prompt tools are greyed out.** No pin is marked as **★ the prompt** yet — mark one in **Workflow & Pins**.
