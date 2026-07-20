# Datasets & Gallery

Creating and organizing datasets, and browsing the images inside them.

Available from: the **Datasets** sidebar item, and the **Gallery** item on any dataset page.

## Datasets

- Create multiple named datasets, each pointing to a folder of images
- Edit datasets — rename (folder is moved on disk and all image paths are updated automatically), update the description, or assign a **category**
- **Sort** the dataset list by: Newest / Oldest / Recently updated / Name A→Z / Name Z→A / Most images / Fewest images / Largest / Smallest / Most captioned %
- **Density toggle** — switch between the card grid and compact rows (about a tenth of the height per dataset), next to the sort control. Your choice is remembered.
- **Category groups** — assign datasets to named categories; the page switches from a flat grid to collapsible folder sections, with "(Uncategorized)" first so a newly created dataset is always near the top. Rename or delete a category (batch-updates all datasets in it) by hovering the section header. **Collapse all** / **Expand all** is in the toolbar, and collapse state is remembered across sessions. A **New category** button creates an empty named category that also persists — useful for pre-planning a layout before datasets are assigned. Empty categories are hidden while the search box is active.
- **Category sidebar** — once you have two or more categories, a sidebar lists them with counts; click one to show only that category. **Drag-and-drop** a dataset onto a sidebar category (or onto a category section header) to reassign it; drop onto "(Uncategorized)" to remove its category. While the search box is active the sidebar dims and all matches are shown, so a result can never hide behind an unselected category.
- **Duplicate** a dataset — deep-copies all images, captions, subfolders, and metadata into a new dataset as a background job; optionally duplicate from a specific version snapshot instead of the current on-disk state

## Browsing images

- Gallery view with search (filename or caption text), pagination, and sort
- Filter by caption status, quality flags, score ranges (multi-chip — add any number of field + min/max conditions combined as AND), aspect ratio, file size, format, and detected object label
- Organize images into subfolders (logical groupings — images stay flat on disk); move or copy images or entire subfolders to a different dataset in one operation
- Per-image detail view with metadata, caption editor, and crop/rotate tools; **keyboard shortcuts**: ← / → navigate between images, **Space** toggles selection, **Delete** opens the delete confirmation. A **Select** button in the toolbar (checkbox icon) also toggles whether the current image is in the active selection. The caption editor shows a live **token counter** (word count · GPT-2 BPE token count) that turns amber at ≥ 70 tokens and red at ≥ 77 — the CLIP truncation limit.
- **Generation Metadata** — PNG metadata from AUTOMATIC1111 and ComfyUI workflows is extracted at import and displayed per-image: prompt, negative prompt, model, sampler, steps, CFG scale, seed, VAE, size, and optional raw ComfyUI workflow JSON

## Getting images in

- Drag-and-drop image files onto the gallery to add them to the dataset; a live progress bar shows how many files have been processed, and the counter persists in the top bar if you navigate away mid-upload
- **Import a folder** — the import dialog (reachable from a dataset card, the Datasets page header, or the gallery toolbar) lets you **choose the target dataset**, pick the source folder with the **"Browse…"** folder browser (or type the path), and optionally **import captions** from `.txt` sidecars next to each image (on by default). The **Preserve structure** option recursively walks subdirectories and maps each level to a logical subfolder matching the relative path; when off, all images land in the specified target subfolder
- **Rescan folder from disk** — a per-dataset-card button and a Rescan button in the gallery toolbar reconcile the dataset with its `images/` folder: new files on disk are registered (with thumbnails), added or changed `.txt` captions are applied, and files missing on disk are reported in a summary toast (DB records are never deleted). Enable **Auto-rescan dataset on open** in Settings to run this automatically each time you open a dataset gallery
- **Import captions** — a per-card button opens a folder-path dialog that matches each `.txt` file to an image by filename and overwrites its caption
- **Drag a `.txt` onto an image** — dropping a text file on a gallery card, or on the caption box in the detail view, sets that image's caption

## Manual Image Ordering

The gallery sort dropdown includes a **Custom order** option. Selecting it activates drag-and-drop reordering:

- Drag any image card to reposition it; the new order persists across sessions
- **First activation** silently initialises order from the current page arrangement so existing sequences are preserved
- **Renumber Files** button (visible in the gallery toolbar when Custom order is active) — renames all images in the current subfolder to `{slug}_001.ext`, `_002`, … in drag order; useful before export so filenames match the training sequence
- Export always follows custom order (`sort_order ASC`) with `created_at` as tiebreak — numbered filenames in Kohya and AI Toolkit formats reflect the drag sequence
- Custom order is preserved across same-dataset subfolder moves; images appended via cross-dataset moves/copies are added after the existing sequence. Crop, upscale, and LUT new-file outputs receive no order and sort last.
