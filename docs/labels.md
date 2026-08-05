# Labels

Subfolders answer *what an image is of*. Labels answer *what it is useful for* —
and an image can carry any number of them at once.

That second axis is the point. Subfolders are a single-parent tree: an image
lives in exactly one. So the images that would make good special-effects training
data end up scattered across `cars`, `city`, `night` — and pulling them out means
either duplicating the whole folder structure under `fx/` or giving up. A label
crosses the tree: mark them `fx` wherever they are, then filter the gallery, or
export, on that one word.

Labels are **never** written into captions and never appear in an exported `.txt`
sidecar. They are curation metadata, not training text.

## Setting up the vocabulary

Labels are managed in **Settings → Labels**, and the vocabulary is **global**: a
label means the same thing in every dataset, and it travels with an image copied
or duplicated somewhere else. There is no free-form typing anywhere else in the
app — you can only apply labels that already exist, which is what stops the list
turning into a thousand near-duplicates.

On that tab you can:

- **Add** a label — type a name (up to 64 characters) and pick a colour. Names
  are unique regardless of case, so `Reject` and `reject` cannot both exist
- **Rename** one by clicking it. A rename is a spelling change and nothing else:
  every image keeps the label, and so does every snapshot that recorded it
- **Recolour** it by clicking its swatch, which opens a palette of twenty
  colours plus a **Custom** picker for anything else. The colour is what you
  actually read on a gallery card, where the label shows as a small dot
- **Bind a hotkey** — one key from `a`–`z` or `0`–`9`. Press **Set key**, then
  the key. If another label already owns it you are told which one. Backspace or
  Delete clears the binding
- **Reorder** with the up/down arrows. The order is the order the labels are
  listed in everywhere else
- **Delete** it. The confirmation names how many images carry it. Deleting
  removes it from all of them, and snapshots that recorded it restore without it

At the bottom of the tab, **Label hotkeys in the image detail view** switches the
keyboard shortcuts on and off. It is on by default.

## Applying labels

**One image at a time** — the image detail page has a **Labels** block directly
below the detections panel, above the generation-metadata and source/license
panels. Current labels show as removable chips; the **Labels** button below them
opens a searchable list of the whole vocabulary, where ticking and unticking
attaches and detaches. The list stays open, so several labels are several
clicks rather than several trips.

**With the keyboard** — on the image detail page, pressing a label's bound key
toggles it on the open image. Toggle, not add: press the same key again to take
it off. Paired with **←** / **→** this is the fast way to triage a folder — step,
press, step, press. A toast confirms each one (`+ fx`, `− fx`).

Typing in the caption box never triggers a hotkey, so you can write a caption
containing every bound letter without labelling anything.

**In bulk** — select images in the gallery (including **Select all**, which
covers every image matching your current filters across every page), then click
**Labels** in the selection toolbar. The modal has two searchable checklists:
*Add to all selected* and *Remove from all selected*. One label cannot be in
both — ticking it in one unticks it in the other. Applying a
label an image already has changes nothing, and removing one it does not have is
equally harmless, so the operation is safe to re-run.

## Finding them again

Gallery cards show a small coloured dot per label, beside the filename. Hover for
the names. The dots disappear entirely when you have no labels defined, so the
card gains nothing until you have set one up.

The gallery toolbar has a **Label** dropdown listing every label with the number
of images carrying it *in this dataset*, and a search box above the list for
when the vocabulary has grown. Tick one to narrow the grid; the closed button
then reads the label you picked, so an active filter is visible without opening
it. Tick two or more and an **Any / All** toggle appears at the foot of the
list — *Any* is the union (images carrying at least one of them), *All* the
intersection (images carrying every one). **Unlabelled**, beside it, finds
images with no label at all; it and the ticked labels are mutually exclusive,
since asking for both would match nothing. Escape closes the list.

Your selection is remembered per dataset, along with the rest of the gallery
filters, and **Reset filters** clears it. If a label is deleted while a filter
that named it is saved, the filter quietly drops it rather than showing an empty
grid with no explanation.

## Exporting by label

The Export page's **Filters** panel has the same dropdown, with the same Any/All
toggle and an **Unlabelled only** option.

A label filter works like **Limit to subfolders** rather than like the quality
filters: it *narrows* what the export is about instead of excluding images from a
fixed set. So the preview's image count shrinks to the matching images, and
nothing appears in the exclusion breakdown. The export then writes exactly those
images — and their `.txt` sidecars carry only the caption, with no label name
anywhere in them, in `captions.jsonl`, or in `CREDITS.md`.

## Labels and the rest of the app

- **Copying and duplicating** — an image copied to another dataset, or a whole
  dataset duplicated, keeps its labels. Because the vocabulary is global there is
  nothing to remap. (Detections deliberately do not travel this way; a label is a
  judgement about the image, so it does.)
- **Moving** — moving images between datasets keeps their labels, unchanged
- **Edited copies** — a crop, an upscale, a LUT grade or a crop-to-detection that
  writes a *new* image (rather than replacing the original) gives the copy the
  same labels, alongside the same source and licence. It is the same picture at a
  different size or framing, so a judgement about it still holds
- **Versioning** — a snapshot records which labels each image carried, and a
  restore puts exactly that state back: a label added after the snapshot is
  removed again, the same way a caption edit is undone. A label deleted from the
  vocabulary since the snapshot is simply dropped. The version diff lists label
  changes by name → [details](versioning.md)
- **Captions** — no interaction at all, in either direction

## What labels are not

- Not tags. Crucible had a tags system once; it overlapped with captions and was
  removed. Labels never touch caption text, which is the whole distinction
- Not hierarchical — there are no parent labels or nesting
- Not per-dataset — one vocabulary, app-wide
- Not automatic — nothing assigns a label for you
- Not available on videos, only on images
