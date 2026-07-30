# Datasets & Gallery

Creating and organizing datasets, and browsing the images inside them.

Available from: the **Datasets** sidebar item, and the **Gallery** item on any dataset page.

## Datasets

- Create multiple named datasets, each pointing to a folder of images
- Edit datasets — rename (folder is moved on disk and all image paths are updated automatically), update the description, assign a **category**, or set the dataset's **source and license defaults** (source name, source URL, license, attribution) that every image inherits → [details](provenance.md)
- Each dataset card and compact row shows its default **source name** and its default license as a colour-coded badge, so you can see at a glance which datasets are commercially usable — and which still say *No license*
- **Sort** the dataset list by: Newest / Oldest / Recently updated / Name A→Z / Name Z→A / Most images / Fewest images / Largest / Smallest / Most captioned %
- **Density toggle** — switch between the card grid and compact rows (about a tenth of the height per dataset), next to the sort control. Your choice is remembered.
- **Category groups** — assign datasets to named categories; the page switches from a flat grid to collapsible folder sections, with "(Uncategorized)" first so a newly created dataset is always near the top. Rename or delete a category (batch-updates all datasets in it) by hovering the section header. **Collapse all** / **Expand all** is in the toolbar, and collapse state is remembered across sessions. A **New category** button creates an empty named category that also persists — useful for pre-planning a layout before datasets are assigned. Empty categories are hidden while the search box is active.
- **Category sidebar** — once you have two or more categories, a sidebar lists them with counts; click one to show only that category. **Drag-and-drop** a dataset onto a sidebar category (or onto a category section header) to reassign it; drop onto "(Uncategorized)" to remove its category. While the search box is active the sidebar dims and all matches are shown, so a result can never hide behind an unselected category.
- **Duplicate** a dataset — deep-copies all images, captions, subfolders, and metadata into a new dataset as a background job; optionally duplicate from a specific version snapshot instead of the current on-disk state. If the dataset holds videos, a **Copy N videos (size)** checkbox appears, off by default so a duplicate never quietly doubles gigabytes of footage — tick it and the clone gets its own copy of each video, its poster, and its saved crop/trim settings, with every extracted frame still pointing at the copy it came from ([Videos](video.md)). The checkbox is disabled when you pick a snapshot, because snapshots don't capture videos. Crucible checks there is room on disk before it starts

## Browsing images

- Gallery view with search (filename or caption text), pagination, and sort
- Filter by caption status, quality flags, score ranges (multi-chip — add any number of field + min/max conditions combined as AND), aspect ratio, file size, format, and detected object label. In a dataset that holds videos, a **Frames from** dropdown narrows the grid to the frames one video produced — wherever you have since moved or renamed them (see [Videos](video.md))
- Filter by **license** — any single license, any free-text license already recorded in this dataset (listed under **Used in this dataset**), or **Missing license only** to find images with no license at either the image or dataset level → [details](provenance.md)
- Organize images into subfolders (logical groupings — images stay flat on disk); move or copy images or entire subfolders to a different dataset in one operation
- Select images with the checkbox in each thumbnail's top-left corner (shift-click a card to extend a range). If the checkbox feels too small to hit comfortably, **Settings → Gallery → Selection checkbox size** scales it from 14 to 32 px
- Per-image detail view with metadata, caption editor, crop/rotate tools, and a **Source & License** panel showing the image's effective source, URL, license badge and attribution — each marked when it is inherited from the dataset rather than set on the image, with an edit button for overriding any of them ([details](provenance.md)); **keyboard shortcuts**: ← / → navigate between images, **Space** toggles selection, **Delete** opens the delete confirmation. A **Select** button in the toolbar (checkbox icon) also toggles whether the current image is in the active selection. The caption editor shows a live **token counter** (word count · GPT-2 BPE token count) that turns amber at ≥ 70 tokens and red at ≥ 77 — the CLIP truncation limit.
- **Generation Metadata** — PNG metadata from AUTOMATIC1111 and ComfyUI workflows is extracted at import and displayed per-image: prompt, negative prompt, model, sampler, steps, CFG scale, seed, VAE, size, and optional raw ComfyUI workflow JSON

## Getting images in

- Drag-and-drop image (or video) files onto the gallery to add them to the dataset; a live progress bar shows how many files have been processed, and the counter persists in the top bar if you navigate away mid-upload. The bar counts files it could not send as **failed** and turns amber; a file Crucible simply will not take — the wrong type, say — is counted separately as **skipped**, so an ignored file does not look like a broken upload. Either way it is named in a toast with the reason, so a rejected upload never looks like a successful one
- Dataset **cards** on the Datasets page are drop targets too — drag files onto one and it shows a *Drop to upload* overlay. It takes the same file types as the gallery and reports the same way, so a video dropped on a card lands as a source and a declined file is named
- **Import a folder** — the import dialog (reachable from a dataset card, the Datasets page header, or the gallery toolbar) lets you **choose the target dataset**, pick the source folder with the **"Browse…"** folder browser (or type the path), and optionally **import captions** from `.txt` sidecars next to each image (on by default). The **Preserve structure** option recursively walks subdirectories and maps each level to a logical subfolder matching the relative path; when off, all images land in the specified target subfolder. A collapsible **Source & license** section applies a source name, URL, license, and attribution to every image in the run — the one ingest path where you can type provenance in directly; anything you leave blank falls back to a scraper sidecar or the file's EXIF → [details](provenance.md)
- **Rescan folder from disk** — a per-dataset-card button and a Rescan button in the gallery toolbar reconcile the dataset with its `images/` folder: new files on disk are registered (with thumbnails), added or changed `.txt` captions are applied, and files missing on disk are reported in a summary toast (DB records are never deleted). If two files you dropped in differ only by extension — `photo.png` and `photo.jpg` — one is renamed to `photo_001.jpg`, because both would otherwise share a single preview thumbnail and one would replace the other. The summary toast says how many were renamed; nothing else about your files is changed. Enable **Auto-rescan dataset on open** in Settings to run this automatically each time you open a dataset gallery
  - **Rescan reads the dataset's own `images/` folder only, and does not search inside subfolders you create there.** Dropping files into that folder and rescanning is the supported way to add them from outside the app. Rearranging a dataset's files into a layout of your own afterwards is not supported: rescan will report them missing even though the files are fine, and the fix is to move them back. The File Browser refuses to make that layout for you → [details](workspace.md#file-browser)
- **Import captions** — a per-card button opens a folder-path dialog that matches each `.txt` file to an image by filename and overwrites its caption
- **Drag a `.txt` onto an image** — dropping a text file on a gallery card, or on the caption box in the detail view, sets that image's caption
- **Videos** are held as *sources* rather than gallery images — stored in their own folder, counted separately from images, and turned into frames on demand → [details](video.md)

A folder import copies every file into the dataset, so it checks first that the drive has room for them. If it does not, the import fails immediately — with the free and required sizes in the job's error — instead of stopping partway through with some images copied and some not.

## Organising into subfolders

**Drag an image card onto a subfolder row** in the left sidebar to move it there — this works in any sort mode. Drop onto **(root)** to move an image back out of its subfolder; that row is always available, even when it's empty. The row highlights as you drag over it, and missing it — dropping on **All** or on empty sidebar space — does nothing.

If the card you drag is part of the current selection, the **whole selection moves**; dragging an unselected card moves just that one and leaves your selection intact. Dropping images onto the subfolder they're already in makes no changes — you'll just get an "Already in …" notice.

Whether moved files are also renamed to match the target subfolder follows the same **auto-rename** preference as the selection toolbar's "Move to subfolder" action. The toolbar route remains available for moving to a subfolder that doesn't exist yet.

## Manual Image Ordering

The gallery sort dropdown includes a **Custom order** option. Selecting it activates drag-and-drop reordering:

- Drag any image card to reposition it; the new order persists across sessions. The card you're dragging dims in place while a floating preview follows the cursor
- **First activation** silently initialises order from the current page arrangement so existing sequences are preserved
- **Renumber Files** button (visible in the gallery toolbar when Custom order is active) — renames all images in the current subfolder to `{slug}_001.ext`, `_002`, … in drag order; useful before export so filenames match the training sequence
- Export always follows custom order (`sort_order ASC`) with `created_at` as tiebreak — numbered filenames in Kohya and AI Toolkit formats reflect the drag sequence
- Custom order is preserved across same-dataset subfolder moves; images appended via cross-dataset moves/copies are added after the existing sequence. Crop, upscale, and LUT new-file outputs receive no order and sort last.
