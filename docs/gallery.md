# Gallery

Browsing, filtering, selecting and sorting the images inside a dataset, and the per-image detail view. Creating a dataset and getting images into it is covered in [Datasets](datasets.md); filing images into subfolders, and renaming or re-nesting them, in [Subfolders](subfolders.md).

Available from: the **Gallery** item on any dataset page.

## Browsing images

- Gallery view with search (filename or caption text), pagination, and sort. The search box has a **×** to clear it in one click
- **Your filters are remembered per dataset** — the search text, the score chips, the detected-object label and every dropdown come back as you left them when you return from an image, switch pages, or reopen Crucible entirely, along with the page you were on. **Reset filters** clears the lot
- Filter by caption status, quality flags, score ranges (multi-chip — add any number of field + min/max conditions combined as AND), aspect ratio, file size, format, and detected object label. In a dataset that holds videos, a **Frames from** dropdown narrows the grid to the frames one video produced — wherever you have since moved or renamed them (see [Frame extraction](video-extraction.md#while-it-runs-and-afterwards))
- Filter by **license** — any single license, any free-text license already recorded in this dataset (listed under **Used in this dataset**), or **Missing license only** to find images with no license at either the image or dataset level → [details](provenance.md)
- Filter by **label** — a dropdown listing every label with the number of images carrying it here, a search box for a long vocabulary, and an **Unlabelled** option → [details](labels.md)
- The toolbar keeps the filters together on the left and **Import folder / Rescan / Upload** together on the right, so a narrow window wraps the filters under themselves rather than pushing the buttons around
- Organize images into subfolders (logical groupings — images stay flat on disk); move or copy images or entire subfolders to a different dataset in one operation → [details](subfolders.md)
- Select images with the checkbox in each thumbnail's top-left corner (shift-click a second **checkbox** to select the run between them; shift-clicking the card itself just toggles that one image). If the checkbox feels too small to hit comfortably, **Settings → Gallery → Selection checkbox size** scales it from 14 to 32 px
- **Select all** in the toolbar selects every image matching your current filters — the whole dataset, or just the subfolder, license or video's frames you have narrowed to — across every page, without leaving the one you are on. The small **⌄** beside it opens the alternative, *This page only*, for when you want just what is on screen. Selecting is always additive: picking a few images, paging on and selecting more, or gathering one subfolder and then another all build up one selection, and only **Deselect all** (the same button once everything is selected) or the selection toolbar's clear empties it. Anything you then do from the selection toolbar applies to the whole set, and a destructive action confirms with the real number. Very large selections stop at 20,000 images; you will be told when that happens, rather than quietly getting fewer than you asked for
- **Deleting an image that is open in another program** — an image viewer or editor, say — leaves that image in place instead of half-deleting it. From the selection toolbar or **Bulk Edit**, everything else is deleted and a message says how many were kept; from the detail view the delete is refused with the reason. Close the program and delete again
- If you select a page some other way — the *This page only* menu item, a shift-click range, or checking every thumbnail — a line appears above the grid offering the rest: *All 50 on this page selected — **Select all 1,240 matching filters***
- The pagination row under the grid says which page you are on out of how many, and how many images match — *Page 3 of 25 · 1,240 images*
- **What a card shows** — the thumbnail carries the selection checkbox (top-left), a quality-flag cluster for NSFW, AI artifacts, duplicate, blurry, watermark, near-uniform and stale scores (top-right), the aesthetic score (bottom-right), and a thin **style match meter** along the bottom edge. The meter's length is the image's percentile among this dataset's own style-similarity scores — the reading that means the same thing whichever embedding model produced them → [details](style-similarity.md#reading-the-score). Images with no style score show no meter; switch it off entirely in Settings → Gallery. Below the thumbnail: the filename, an optional license badge (off by default, Settings → Gallery), a generation-info button when the image carries metadata, the dimensions, and the caption
- Per-image detail view with metadata, caption editor, crop, upscale and LUT tools, and a **Source & License** panel showing the image's effective source, URL, license badge and attribution — each marked when it is inherited from the dataset rather than set on the image, with an edit button for overriding any of them ([details](provenance.md)); **keyboard shortcuts**: ← / → step between images *within the folder and filters you were browsing* — including on past the end of a page, so a narrowed gallery never drops you into the rest of the dataset — **Space** toggles selection, **Delete** opens the delete confirmation. The arrows keep up with a job that is adding images: while a ComfyUI generation, a folder import, a rescan, a frame extraction or a version restore fills the dataset, new images become reachable with ← / → without leaving the page, and the counter between the arrows carries a third, dimmer number — *12 / 100 · 347* — for how many images match your filters in total, climbing as the run goes. (It is left off when it would only repeat the number beside it.) A **Select** button in the toolbar (checkbox icon) also toggles whether the current image is in the active selection. The caption editor shows a live **token counter** (word count · GPT-2 BPE token count) that turns amber at ≥ 70 tokens and red at ≥ 77 — the CLIP truncation limit.
- **Generation Metadata** — PNG metadata from AUTOMATIC1111 and ComfyUI workflows is extracted at import and displayed per-image: prompt, negative prompt, model, sampler, steps, CFG scale, seed, VAE, size, and optional raw ComfyUI workflow JSON

## Sorting

The gallery sort dropdown covers newest/oldest, name, aesthetic score, style similarity, colourfulness, brightness, and **Custom order** (below). Your choice is remembered per dataset, and **Settings → Gallery** sets the default for datasets you have not sorted yet.

Two entries are for video frames:

- **Video timeline** — frames in the order they appear in their source video, earliest first
- **Shot order** — frames grouped by the shot they were cut from, in shot order

Both are the natural way to review a triage extraction pass, and unlike browsing by subfolder they keep working after you have moved or renamed the frames. Images that did not come from a video sort to the end. Pair either with the **Frames from** filter to see one video's frames on their own → [Frame extraction](video-extraction.md#while-it-runs-and-afterwards).

## Manual Image Ordering

The gallery sort dropdown includes a **Custom order** option. Selecting it activates drag-and-drop reordering:

- Drag any image card to reposition it; the new order persists across sessions. The card you're dragging dims in place while a floating preview follows the cursor
- **First activation** silently initialises order from the current page arrangement so existing sequences are preserved
- **Renumber Files** button (visible in the gallery toolbar when Custom order is active) — renames all images in the current subfolder to `{slug}_001.ext`, `_002`, … in drag order; useful before export so filenames match the training sequence
- Export always follows custom order (`sort_order ASC`) with `created_at` as tiebreak — numbered filenames in Kohya and AI Toolkit formats reflect the drag sequence
- Custom order is preserved across same-dataset subfolder moves; images appended via cross-dataset moves/copies are added after the existing sequence. Crop, upscale, and LUT new-file outputs receive no order and sort last.
