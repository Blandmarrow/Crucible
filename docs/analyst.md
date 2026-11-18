# Dataset Health and Notes

The **Health** page lists what Crucible finds wrong with a dataset, by itself, with no AI
involved: images with no caption, images below your target resolution, duplicates, stale
scores, missing licences and more. Each finding says how many images it affects, shows them,
and points at the page that fixes it. Beside the findings you keep **notes** about the
dataset: your standing preferences, the decisions you made, and what is still to do.

This is the first part of the dataset analyst. A later version adds a chat in which an AI
model reads these findings and notes and gives advice; everything here already works without
it.

Open it from **Health** in the sidebar, under a dataset, or pick **Health** in a split pane.

## The goal and target resolution

Two optional fields in the **New Dataset** and **Edit** dialogs on the Datasets page:

- **Goal**: what the dataset is for, in your own words, such as "SDXL style LoRA of
  watercolour landscapes". It is separate from the description, which usually says where the
  images came from. The analyst will read it; for now the Health page reminds you when there
  is none.
- **Target resolution**: the short side, in pixels, the dataset trains at, from 64 to 16384.
  Leave it blank to use 1024. The "below the target resolution" finding checks against it.

## Reading the findings

Each finding has a level:

- **Error**: something that will break training.
- **Warning**: something that will probably hurt it.
- **Note**: worth knowing, often fine on purpose.

The counts by level show at the top of the page, and the dataset's card on the Datasets page
shows its worst level as a badge (errors before warnings; notes alone show no badge). The badge
is from the last time the report was built, so it can be a little behind; hover it to see
when its counts last changed.

Each finding shows its count ("12 of 340 images"), the threshold or values behind it, and up
to three buttons:

- **Show images** opens the affected images, as on the Stats page. Click one for a larger
  view, its details, or to delete it.
- The **fix** button opens the page that deals with it, such as Captioning or Score Images.
- **Dismiss…** hides a finding you mean to keep (see below).

There is no single health score: one number hides the reason, and what counts as healthy
depends on what the dataset is for.

### What it checks

Videos are not checked; only images are.

| Finding | Level | When it shows |
|---|---|---|
| No goal set | Note | the dataset has no goal |
| Small dataset | Note | fewer than 20 images |
| Images with no caption | Warning | any image has an empty caption |
| Very short captions | Note | a caption of 1 to 4 tokens |
| Captions over 77 tokens | Warning | CLIP reads only the first 77 tokens and drops the rest |
| Below the target resolution | Warning | the short side is under your target (or 1024) |
| Rarely used aspect ratios | Note | 3 or more images sit in aspect-ratio buckets that each hold under 5% of the images |
| Blurry, watermarked, AI artifacts | Warning | the image's quality flag is set |
| Noisy, flat, NSFW | Note | the image's quality flag is set |
| Duplicate groups | Warning | a scoring run found near-identical images |
| No aesthetic score, no technical scores | Note | the image has not been scored |
| Scores older than the pixels | Warning | the image was edited in place after scoring ([details](reading-scores.md)) |
| Style outliers | Note | a style similarity run exists and an image scores far below the rest |
| No licence recorded | Note | neither the image nor the dataset sets a licence |
| A tag in nearly every caption | Note | one tag is in 90% or more of the tag-style captions |
| Tags used only once | Note | with 20 or more different tags, some appear in one caption only |
| Tag-style and prose captions mixed | Note | the smaller of the two kinds is 10% or more of the captions |

The quality flags use the thresholds in [Settings](settings.md), and each finding shows the
threshold now set. A flag is written when an image is scored, so after you change a threshold
the counts change only once you score again.

## Dismissing a finding

Some findings are fine on purpose: a trigger word is meant to be in every caption, and a
dataset of grainy film scans is meant to look noisy. Click **Dismiss…**, give a reason, and
the finding moves to the **Dismissed** list at the bottom.

The reason is saved as a **preference** in the dataset's notes, so the analyst will know why.
A dismissed finding comes back on its own if its count more than doubles, unless you ticked
**Keep it dismissed even if it gets worse**. To undo a dismissal, click **Restore** in the
Dismissed list, or remove the note.

## Compared with the last snapshot

When you take a snapshot on the Versions page, Crucible records the findings at that moment.
The Health page then compares against the latest snapshot of the branch you are on: a finding
shows "was 12" when its count has changed, and findings that have gone since are listed as
resolved. A snapshot taken before this feature has no findings recorded, so the page says
there is no baseline until you take a new one. See [Dataset Versioning](versioning.md).

## Notes

The bottom of the page holds two sets of notes:

- **This dataset**: **Preferences** (standing rules, such as "Trigger word zxc comes first"),
  **Decisions** (what you decided and why, dated when you add it) and **Open items** (what is
  still to do).
- **About me**: shared by every dataset, such as "I train SDXL LoRAs with kohya".

Type into a section's box and press Enter to add a note. Click a note to edit it, use the
arrows to reorder it, and × to remove it; the removal can be undone from the message that
appears. Notes are kept short so they fit beside the findings for a small AI model: 4,000
characters per dataset and 2,000 for About me, with a counter above each set.

Notes belong to the dataset: deleting the dataset deletes them, and they are not part of a
snapshot or a duplicate. Duplicating a dataset does copy its goal and target resolution.
