# Keep/Cut Rating

Your own verdict on each image, kept as a real field on it: **4 — Keep**, **3 — Probably**,
**2 — Probably not**, **1 — Cut**. Higher is better. Images start unrated, and unrated is a
real state, not a zero.

It is deliberately a *decision*, not a quality grade. "Keep" is a call you already make
while scrolling, and answering it consistently is far easier than deciding whether something
is a 7 or an 8. The scores on the [Quality Scoring](scoring.md) page are the measurements;
this is what you want to do about them.

Available from: the gallery, the selection toolbar, and any image's detail page.

## Rating from the keyboard

The fast path, and the one this is designed around:

1. Select images in the gallery — click their checkboxes, shift-click a range, or use
   **Select all** / **All N matching filters**.
2. Press **1**–**4**. Every selected image takes that rating.
3. Press **0** to clear the rating instead.

The same keys work on the image detail page, acting on the image you are looking at. There
are visible buttons there too, showing which rating is currently set.

In a split view, the keys act on the pane you last clicked in, and they never fire while you
are typing in a search box or while a dialog is open.

If nothing is selected, a toast says so rather than silently doing nothing.

## Rating a selection from the toolbar

The selection toolbar's **Rate** button opens a small dialog with one button per rating, plus
**Clear rating**. It is the same operation as the keys and exists for the times the keys are
awkward — most usefully after **Select all N matching filters**, which can cover far more
images than are on screen.

## The badge on the card

A rated card shows its numeral in the top-right corner, coloured by tier. Unrated cards show
nothing, so the badge costs you nothing on a dataset you have not triaged.

A **dashed** badge means the image was edited in place — resized, cropped, upscaled, colour
graded, or re-extracted from a video — *after* you rated it. The rating still stands; it was
just made about pixels that have since been replaced. Rating it again, even the same way,
confirms it. Nothing else clears that mark: a quality re-score says nothing about a decision
you made.

Turn the badge off under **Settings → General → Keep/cut rating on cards**.

## Filtering and sorting

A row of chips above the gallery grid filters by rating. They are multi-select and combine
with **or**, and **Unrated** is one of them — so "Keep or Unrated" is one click each, which
is the natural pass when you are working through a backlog.

Beside the chips is the number of images in the dataset nobody has rated yet. It is there
because a rating filter is meaningless without it: "214 match" reads very differently when
1,715 images have never been looked at.

The sort dropdown gains **Rating ↓** (best first) and **Rating ↑**. Unrated images sort last
in both directions — "not judged" is not a rank below Cut.

## Exporting by rating

The [Export](export.md) page's filter panel gains two rating controls:

- **Rating ≥** — keep only images at or above a tier. **This also drops every unrated
  image**, because an unrated image has no rating to compare. That is the one surprising
  behaviour here, so the summary panel states it before you run anything: it shows how many
  images will export alongside how many are unrated and therefore excluded.
- **Exclude rated** — drop the tiers you tick, the way **Exclude flagged** drops flags.
  Unrated images are never dropped by this, so "exclude Cut" leaves your untriaged images
  alone.

The exclusion table below the filters counts what the rating filters removed, alongside the
other filters' counts.

## Where else it shows up

- **Statistics** — a *Keep/cut rating* panel under Aesthetic & Style, best first with an
  Unrated bucket. Click a bar to browse that tier. The distribution is also in the stats CSV
  → [Statistics Dashboard](statistics.md).
- **Moving and copying** — a rating travels with the image to another dataset. Moving a file
  does not change your opinion of it.
- **Crops, upscales and colour grades** — a *new* image made from a rated one starts
  unrated. It is a different picture and deserves its own call.
- **Versioning** — a snapshot records ratings, and restoring one puts back the ratings that
  were in it, exactly as it does captions. The restore dialog tells you how many ratings will
  change before you confirm, and the "auto-snapshot current state before restoring"
  checkbox — on by default — is what lets you undo it → [Dataset Versioning](versioning.md).
