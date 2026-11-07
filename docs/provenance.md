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
| **License** | One entry from the [license vocabulary](licenses.md#the-license-vocabulary), or your own free text — pick **Other (free text)…** in any license *editor* and type it (up to 58 characters) |
| **Attribution** | Who to credit — `Photo by Jane Doe` |

A fifth, **scrape metadata**, is captured automatically from scraper sidecars
(post id, upload date, tags, the raw payload) and shown read-only on the image
detail page.

The license list itself — which licenses permit commercial use, which need
attribution — and the `CREDITS.md` / `licenses.csv` files an export writes are on
[Licenses and Credits](licenses.md).

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
  sidecar and the embedded attribution for files it registers **for the first
  time** — everything except the import dialog's values, which it has no dialog
  to read. It does *not* backfill images already in the dataset: dropping a
  sidecar next to a file that is already imported and rescanning changes
  nothing. Use **Set source/license** on a selection for those.
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
to the [commercial-use filter](licenses.md#the-license-vocabulary) rather than being relabelled "synthetic".

With the toggle **on**, the run also writes the credit line itself — *Generated
by ComfyUI plan "your plan"* — so a dataset whose default attribution names a
photographer does not end up crediting them for an AI image.

One thing it still cannot do: the dataset's default **source URL** is inherited,
because a generated image has no URL of its own and inventing one would be worse.
So `CREDITS.md` (see [Licenses and Credits](licenses.md#export)) can list that URL under the generated image's entry. If your
dataset holds both generated and sourced material and records a default source
URL, set the URL per image or keep the two kinds in separate datasets.

Derived images — crops, upscales, LUT-graded copies, detection crops — carry
their parent's source and license. A derivative of a CC BY-SA image is still
CC BY-SA. So do **frames extracted from a video**, which carry the video's
four fields; that is how a video's licence reaches anything you train on.

Provenance is part of a dataset's version history: snapshots capture all four
fields per image, so a restore reverts them and a diff shows what changed. A
video's own four fields are outside version history, along with the rest of the
video. See [Dataset Versioning](versioning.md).

## Videos

A video carries the same four fields as an image, inherits them from the dataset
default the same way, and shows them on its detail page. Three differences are
worth knowing, all deliberate:

- **They are set at import and not editable afterwards.** Folder import's
  source/license fields apply to the videos it brings in; a video added by upload
  or picked up by a rescan simply inherits. There is no per-video editor and
  **Set source/license** covers images only, so changing a video's values after
  ingest means changing the dataset default.
- **Nothing is read off the file.** Videos have no sidecar and no embedded
  attribution read, so the import dialog is the only place a value can come from.
- **The gallery filter, the Statistics licence breakdown and every [export](licenses.md#export) count
  images only.** A video is never exported; its licence reaches an export through
  the frames extracted from it, which do carry it.

## Labeling images you already have

Select images in the gallery and use **Set source/license** in the selection
toolbar. Each field has three modes:

- **Keep** — leave it exactly as it is.
- **Set** — write this value onto every selected image.
- **Inherit** — clear it, so the image follows its dataset's default.

This is how an existing library gets labeled: set a dataset default, then
override the handful of images that differ.

## Finding unlicensed images

- **Gallery** — the license dropdown filters by any [license](licenses.md#the-license-vocabulary), or by
  **Missing license only**. Turn on *Settings → Gallery → License badge on
  cards* to see each image's license at a glance (off by default, since a badge
  on every card is noise for a single-source dataset).
- **Stats** — the **Licenses** panel breaks the dataset down by effective
  license and calls out how many images have none. Click a row to open those
  images.
