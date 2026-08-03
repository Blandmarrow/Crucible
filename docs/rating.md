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

## The Aesthetic Rating page

**Aesthetic Rating** in the sidebar is a page about your ratings themselves, rather than
about any one dataset. It pools every dataset you have, because your taste is yours — it
does not change when you switch folders.

**The four tiles** are how much of your library you have judged: how many images are rated
(and what share of the total), how many are not, how many you have gone back and rated a
second time, and how often you gave the same image the same answer twice.

**Your own ceiling** is that last number, and it is the one worth understanding. Nobody is
perfectly consistent: shown the same image twice on different days, you will sometimes say
Keep and sometimes Probably. Knowing your own figure is what makes any automatic score
readable — a scorer that agrees with you 84% of the time is doing as well as anything can if
you only agree with yourself 87% of the time, and is genuinely poor if you agree with
yourself 98% of the time. The same number read without the comparison looks like a failure
forever.

**What it is not** is a measurement of your consistency, and the page says so every time it
shows it. These are re-ratings *you chose to make*, with your previous answer visible on
screen — so it is pushed down by your tendency to revisit images you disagree with, and
pushed up both by seeing your old answer first and by bulk sweeps that rate images nobody
looked at individually. The page splits the deliberate one-at-a-time re-rates out from the
sweeps for that reason. Treat it as a rough floor.

Below ten comparable re-ratings the page shows the counts and refuses to show a percentage
at all. A figure from three re-rates is noise wearing a number. To feed it, simply rate an
image you have already rated — every rating is recorded, including one that does not change
anything, and an unchanged answer is exactly the agreement being counted.

**Does a scorer already know your taste** is the second half. For each aesthetic model you
have run, it shows how strongly its scores line up with your ratings, the average score it
gave each of your four tiers, and how reliably it puts the better of two images above the
worse one at each boundary. The two models are shown apart because their score scales are
not comparable — a 6.2 from one does not mean what a 6.2 from the other does.

Two readings are worth knowing. The correlation figure is always shown "of a possible" one:
because you sort images into just four tiers, no scorer could reach a perfect 1.00 even in
principle, and the achievable maximum is the honest thing to measure against. And the
per-boundary bars are drawn out from the middle rather than from the left, because 0.5 is a
coin flip — the no-information point, not half a success.

If the four tier averages are flat, that scorer knows nothing about your taste. If they climb
cleanly, it does. That is four numbers anyone can read, and it is the fastest answer on the
page.

The page needs no scoring run to be useful: with nothing scored it still shows your rating
distribution and your ceiling, and the scorer panel simply says what is missing.

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
