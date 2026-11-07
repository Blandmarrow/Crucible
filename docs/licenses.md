# Licenses and Credits

This page covers the license list Crucible understands, what each license means for
commercial use and attribution, and how an export turns those licenses into credit
files for the dataset you publish. It is the second half of source tracking: for how
source, URL, license and attribution get onto an image in the first place (dataset
defaults, import, rescan, videos, bulk labelling), see
[Source & License Provenance](provenance.md). For the export options themselves, see
[Export](export.md).

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
(up to 58 characters). A license Crucible doesn't recognise in a scraper
sidecar's `license` field is kept the same way rather than dropped.

You only type it once. Every license control scoped to a dataset then offers the
free-text licenses **already recorded in that dataset** — including the dataset's
own default: the gallery's license filter, the image detail editor, the bulk
**Set source/license** action, the dataset Edit modal, and the folder-import
dialog (which reads the dataset you are importing *into*, and updates when you
change it) list them under a **Used in this dataset** heading, and the Export
page's *Specific licenses* checklist appends them below the standard entries. Pick one instead of retyping it; a one-character difference is a
second, near-duplicate license. The list is per-dataset, so it never offers a
license none of that dataset's images have. (The create-dataset dialog has no
dataset to read yet, so it shows the standard list only.)

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
  Only licenses *known* to be ND are dropped, so a free-text license is kept —
  including one you typed yourself as, say, "CC BY-ND (custom)" instead of
  picking the vocabulary entry. With this filter on, the export summary counts
  how many shipping images carry a license it could not classify, so the ones it
  waved through are visible before you build.
- **Specific licenses** — a collapsible checklist: the standard vocabulary, any
  free-text licenses recorded in this dataset, and a **No license recorded**
  entry, so you can build an export of exactly the images you still need to
  label. Tick the ones to keep; ticking nothing means no filter.

If any images have no license, the export summary warns you before you build. It
tells you how many there are, and how many of them this export still ships — a
count that accounts for **every** filter you have on, not just the license ones,
so an unlicensed image already dropped by the caption or aesthetic filter is
counted as not shipping. If none survive it says so; otherwise it reminds you
they will ship listed as unlicensed in `CREDITS.md`. It never blocks the export
— the call is yours.

See [Export](export.md) for the rest of the export options.
