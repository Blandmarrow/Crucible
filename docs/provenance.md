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
| **License** | One entry from the vocabulary below, or your own free text — pick **Other (free text)…** in any license dropdown and type it (up to ~58 characters) |
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

Anything outside this list is stored as free text and shown as-is. Choose
**Other (free text)…** at the bottom of any license dropdown — on the image
detail page, in the dataset Edit modal, in the import dialog, or in the bulk
**Set source/license** action — and a text box appears for you to type it
(roughly 58 characters). A license Crucible doesn't recognise in a scraper
sidecar's `license` field is kept the same way rather than dropped.

"Commercial use" filters are deliberately conservative: **unknown counts as no**.
An image whose rights were never established must not slip into an export you
made specifically to be commercially safe.

## What gets captured automatically

On folder import, each image is checked in this order — the first source that
supplies a field wins:

1. **Values you typed in the import dialog** — applied to every image in the run.
2. **A scraper sidecar** next to the image — either `pic.png.json` (what
   gallery-dl writes by default) or `pic.json`. gallery-dl and Grabber layouts
   are understood: `category` → source name, `post_url`/`file_url` → source URL,
   `author`/`uploader`/`user` → attribution, `license` → license. Several
   alternative key names are accepted for each field; the ones listed are just
   the common ones. The whole file is kept as scrape metadata, so nothing is
   lost.
3. **The image's EXIF** — `Artist` and `Copyright` become the attribution.
   Deliberately never the license: a copyright notice is a rights claim, not a
   license id, and guessing one would put unverified images into the
   commercial-use bucket.

Anything still unset stays empty and inherits the dataset default.

Images generated on the **ComfyUI** page are recorded as `Synthetic
(AI-generated)` from source `ComfyUI`, with the plan and checkpoint noted in
scrape metadata.

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
  `file,source_name,source_url,license,attribution`. Both record the *resolved*
  license, so an image that inherited its license from the dataset shows the
  real value, not a blank.

Three optional filters live in the Export page's filter panel:

- **Commercial-use only** — keeps only images whose license is known to permit
  commercial use.
- **Exclude unlicensed images** — drops images with no license at either level.
  Free-text licenses count as licensed, so this keeps them.
- **Specific licenses** — a collapsible checklist of the vocabulary; tick the
  ones to keep. It includes a **No license recorded** entry, so you can build an
  export of exactly the images you still need to label. Ticking nothing means no
  filter.

If any images have no license, the export summary warns you before you build.
The warning tells you how many there are either way; if one of the filters above
would drop them, it says so, otherwise it reminds you they will ship listed as
unlicensed in `CREDITS.md`. It never blocks the export — the call is yours.

See [Export](export.md) for the rest of the export options.
