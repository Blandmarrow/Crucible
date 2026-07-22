# Source & License Provenance

Crucible records where each image came from and what you are allowed to do with
it. For a training set assembled from mixed sources — scraped boorus, stock
purchases, commissioned work, self-generated ComfyUI output — this is what lets
you answer "can I train a commercial model on this set?" and produce an
attribution file for a dataset you publish.

Four fields travel with every image:

| Field | What it holds |
|---|---|
| **Source name** | Where it came from — `Danbooru`, `Unsplash`, `Client X` |
| **Source URL** | Link back to the original post or page |
| **License** | One entry from the vocabulary below, or your own free text — pick **Other (free text)…** in any license *editor* and type it (up to ~58 characters) |
| **Attribution** | Who to credit — `Photo by Jane Doe` |

A fifth, **scrape metadata**, is captured automatically from scraper sidecars
(post id, upload date, tags, the raw payload) and shown read-only on the image
detail page.

## Inheritance: dataset defaults

Setting a license on ten thousand images one at a time is not a workflow. So
provenance is set at two levels:

- **Dataset defaults** — edit them in the dataset's **Edit** modal on the
  Datasets page, or when you create it. Each dataset card (and compact row)
  shows the current default license as a colour-coded badge, so you can see
  which datasets are commercially usable — and which still say *No license* —
  without opening anything.
- **Per-image values** — set on the image detail page, or in bulk from the
  gallery.

An image that has no value of its own **inherits the dataset default**. This is
resolved every time the value is read, so editing a dataset's default
immediately changes every image that has not overridden that field — no
migration, no rebuild. The image detail page marks each inherited field so you
can tell which values are the image's own.

To go back to inheriting, clear the field: empty the text input and save, or —
for License, which is a dropdown — choose **Inherit from dataset**. In the bulk
**Set source/license** action the equivalent is the **Inherit** mode.

**One exception:** copying or moving images to a *different* dataset writes the
inherited values out as real values first. Otherwise an image that inherited
`CC BY-NC` from a scraped dataset would silently pick up `Owned` from the
dataset you moved it into.

## The license vocabulary

| License | Commercial use | Attribution required |
|---|---|---|
| Unknown | Unknown — treated as no | No |
| Owned / self-created | Yes | No |
| Public domain | Yes | No |
| CC0 1.0 (no rights reserved) | Yes | No |
| CC BY 4.0 | Yes | Yes |
| CC BY-SA 4.0 | Yes (share-alike) | Yes |
| CC BY-NC 4.0 | **No** | Yes |
| CC BY-NC-SA 4.0 | **No** (share-alike) | Yes |
| CC BY-ND 4.0 | Yes (no derivatives) | Yes |
| Licensed for commercial use | Yes | No |
| Research / non-commercial only | **No** | Yes |
| Synthetic (AI-generated) | Yes | No |
| No license granted | **No** | No |

Anything outside this list is stored as free text and shown as-is. Choose
**Other (free text)…** at the bottom of any license *editor* — on the image
detail page, in the dataset Edit modal, in the import dialog, or in the bulk
**Set source/license** action — and a text box appears for you to type it
(roughly 58 characters). The gallery's license *filter* has no such entry: it
filters by the values that exist, so a free-text license is not one of its
options. A license Crucible doesn't recognise in a scraper sidecar's `license`
field is kept the same way rather than dropped.

**"No license granted" is not the same as "Unknown", and neither is the same as
blank.** Blank means *nothing recorded* — the image still inherits its dataset's
default, and "Exclude unlicensed images" drops it. *Unknown* is a recorded answer
meaning the rights could not be established; it is a real license value, so
"Exclude unlicensed" does **not** drop it — only the commercial-use filter does.
*No license granted* is the source explicitly reserving all rights ("all rights
reserved" in a scraper sidecar maps here); it too is a recorded value, so it
overrides the dataset default rather than inheriting past it.

"Commercial use" filters are deliberately conservative: **unknown counts as no**.
An image whose rights were never established must not slip into an export you
made specifically to be commercially safe.

## What gets captured automatically

**Folder import** checks each image in this order — the first source that
supplies a field wins:

1. **Values you typed in the import dialog** — applied to every image in the run.
2. **A scraper sidecar** next to the image — either `pic.png.json` (what
   gallery-dl writes by default) or `pic.json`. gallery-dl and Grabber layouts
   are understood: `category` → source name, `post_url`/`file_url` → source URL,
   `author`/`uploader`/`user` → attribution, `license` → license. Several
   alternative key names are accepted for each field; the ones listed are just
   the common ones. The whole file is kept as scrape metadata, so nothing is
   lost.
3. **Attribution embedded in the image** — EXIF `Artist`/`Copyright` on a JPEG,
   or the `Author`/`Copyright` text chunks of a PNG. Deliberately never the
   license: a copyright notice is a rights claim, not a license id, and guessing
   one would put unverified images into the commercial-use bucket.

Anything still unset stays empty and inherits the dataset default. A very long
value from a sidecar is shortened to fit rather than failing the import.

The other two ways images arrive capture less, because less is available:

- **Rescan** (the Datasets page's rescan, and auto-rescan on open) reads the
  sidecar and the embedded attribution — everything except the import dialog's
  values, which it has no dialog to read.
- **Drag-and-drop onto the gallery** reads only the embedded attribution. Your
  browser uploads the image bytes and nothing else, so a scraper sidecar sitting
  beside the file on disk is never seen. **Use folder import for scrape
  folders** — dragging one in loses its provenance silently.

Images generated on the **ComfyUI** page are recorded as `Synthetic
(AI-generated)` from source `ComfyUI`, with the plan and model noted in scrape
metadata. Each plan carries an **Output is synthetic (self-created)** checkbox in
*Workflow & Pins*, on by default. Turn it off for a plan that derives from
licensed material: the output then inherits the dataset's source, URL, license
and attribution defaults instead, so a run over a CC BY-NC dataset stays visible
to the commercial-use filter rather than being relabelled "synthetic".

One thing the toggle cannot do: with it **on**, a dataset that records a default
`source_url` or `attribution` still supplies those two, because a blank image
field means "inherit" and there is no honest URL or credit line to write for a
generated image. Set them per image, or keep generated and sourced material in
separate datasets.

Derived images — crops, upscales, LUT-graded copies, detection crops — carry
their parent's source and license. A derivative of a CC BY-SA image is still
CC BY-SA.

## Labeling images you already have

Select images in the gallery and use **Set source/license** in the selection
toolbar. Each field has three modes:

- **Keep** — leave it exactly as it is.
- **Set** — write this value onto every selected image.
- **Inherit** — clear it, so the image follows its dataset's default.

This is how an existing library gets labeled: set a dataset default, then
override the handful of images that differ.

## Finding unlicensed images

- **Gallery** — the license dropdown filters by any license, or by
  **Missing license only**. Turn on *Settings → Gallery → License badge on
  cards* to see each image's license at a glance (off by default, since a badge
  on every card is noise for a single-source dataset).
- **Stats** — the **Licenses** panel breaks the dataset down by effective
  license and calls out how many images have none. Click a row to open those
  images.

## Export

Every export writes two manifests at the top level of the output directory,
in all three formats (Kohya, AI Toolkit, plain):

- **`CREDITS.md`** — human-readable, grouped by license and then by source.
  Attribution-required licenses come first, because those are the entries a
  redistributor has to act on.
- **`licenses.csv`** — one row per exported file:
  `file,source_name,source_url,license,attribution`. The `file` column is
  relative to the output directory, since images sit one level down.

Both record the *resolved* license, so an image that inherited its license from
the dataset shows the real value, not a blank.

They are written whenever an export stops early, not only when it succeeds — if
you cancel it, or it fails on an unreadable image or a full disk, the files
already on disk still get a manifest. Such a run writes
**`CREDITS.partial.md`** and **`licenses.partial.csv`** instead — the entries
cover only the files that were actually written, and the file says so at the top.
The separate name matters: if the interrupted run took `CREDITS.md`, the later
complete export would land beside it (see below) and the incomplete manifest
would stay the one a redistributor opens.

**Re-exporting the same output folder replaces its manifests.** When every file
the existing `licenses.csv` lists sits inside the folder this run is writing, the
new run is describing the same material again, so it takes `CREDITS.md` /
`licenses.csv` and the old ones are gone.

A manifest describing *other* files is never destroyed. Exports routinely share
a directory — Kohya's `10_concept/` beside an earlier `20_concept/`, with the
manifests in their common parent — and there the new manifest is written
alongside as `CREDITS.2.md` / `licenses.2.csv`. Identical content is left alone
entirely. The Export page names the files each run actually wrote when it
finishes, so you never have to guess which case you got.

Four optional filters live in the Export page's filter panel:

- **Commercial-use only** — keeps only images whose license is known to permit
  commercial use.
- **Exclude unlicensed images** — drops images with no license at either level.
  Free-text licenses count as licensed, so this keeps them.
- **Exclude no-derivatives** — drops CC BY-ND. An export ships resized, cropped
  or re-encoded copies, which is what "no derivatives" forbids redistributing.
  Only licenses *known* to be ND are dropped, so a free-text license is kept.
- **Specific licenses** — a collapsible checklist of the vocabulary; tick the
  ones to keep. It includes a **No license recorded** entry, so you can build an
  export of exactly the images you still need to label. Ticking nothing means no
  filter.

If any images have no license, the export summary warns you before you build.
The warning tells you how many there are either way; if one of the filters above
would drop them, it says so, otherwise it reminds you they will ship listed as
unlicensed in `CREDITS.md`. It never blocks the export — the call is yours.

See [Export](export.md) for the rest of the export options.
