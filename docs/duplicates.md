# Duplicate Resolution

Reviewing and clearing the duplicate groups a scoring run found, one group at a time or a hundred at once.

Duplicate *detection* is part of a scoring run and is documented in [Quality Scoring](scoring.md) — there is no checkbox of its own, because the perceptual hash (pHash) is computed once when an image is imported and the **Technical** scorer does the grouping pass that compares those hashes and sets `is_duplicate`. This page is about what you do with the panel that appears afterwards.

Available from: the **Score images** sidebar item on any dataset page, below the scoring controls. The gallery's duplicate badge and the `is_duplicate` quality filter lead here too.

Everything on this page **deletes files**. Read the confirmation dialogs; there is no undo short of a [version snapshot](versioning.md).

## Duplicate resolution

After a scoring run that includes duplicate detection, the Score images page groups detected duplicates into thumbnail grids, oldest first, with a green outline and a **kept** label on the one the scan chose to keep. The group header names the distance threshold actually in force, from Settings → Thresholds. Each group offers:

- **Keep best** — retains the image with the highest aesthetic score and deletes the rest. Images with no aesthetic score rank **last**, never first: unscored means unknown, not bad. If no image in the group has been scored at all, the button is disabled and says so — use *Keep first* instead. It is disabled for the same reason when a group's scores come from **two different aesthetic models**: the thumbnails then carry a small model label under each score, and re-scoring the group with one model re-enables it. The two aesthetic scales are not comparable — see [Choosing the aesthetic model](scoring.md#choosing-the-aesthetic-model)
- **Keep first** — retains the image marked *kept*, which is always first in the group, and deletes the rest

Both buttons ask for confirmation on a group whose frames all came from one video — see below.

Long groups show their first ten images with a **+N more** button to reveal the rest, and the list itself shows 25 groups at a time with a **Show 25 more** button below it.

## Clearing many groups at once

A scoring run over a big dataset can produce a hundred or more groups, so the top of the panel carries the same two actions over every group at once:

- **Keep best in N groups** — applies *Keep best* to each. Groups where nothing has an aesthetic score are **skipped**, not resolved by some other rule, and so are groups mixing two aesthetic models; the button says how many it is skipping and why, and disables itself only if that is all of them
- **Keep first in N groups** — applies *Keep first* to each

Next to them are three filters — **All**, **From one video**, and **Mixed or no video** — so you can clear the safe groups en masse and hand-check the risky ones. They appear only when at least one group is entirely frames from a single video. **The filter decides what the bulk buttons cover**: the count in each button is the number of groups matching the active filter, including the ones further down the list than you have scrolled, not the ones currently on screen.

Bulk actions always ask for confirmation. The dialog states how many images will be deleted across how many groups, which one it keeps in each, how many of those groups are entirely frames from one video, and how many were skipped — for having no score, or for mixing two aesthetic models. A run over many groups reports progress in the button, and if it fails partway it tells you how many groups it got through — the ones already resolved stay resolved.

## Clearing a group outside this panel

The duplicate mark is a statement about a *pair*, not about one image, so it is kept honest whichever way you thin a group out. Delete copies from the gallery, the lightbox, a bulk delete or the file browser and the last image standing loses its duplicate badge automatically — it is not a duplicate of anything any more. Two or more copies still surviving keep their badges, even when the image the scan had marked *kept* was the one you deleted; they are still duplicates of each other, and the panel keeps offering them as a group.

If a dataset still shows a duplicate count you cannot account for — from an older version of Crucible, or from a scan you have since tightened the threshold on — **re-run scoring with Technical ticked**. The grouping pass is authoritative: it re-marks the images that are duplicates now and clears the mark from every image that is not, so a re-scan is the repair for anything stale. Only the duplicate mark is touched; blurry, watermark and the other flags are left alone.

## Duplicates that came from the same video

A perceptual hash cannot tell a held animation cel or a stretch of recycled footage from a redundant copy, so frames extracted from one video often land in the same group legitimately. When every frame in a group came from the same video, Crucible says so above the thumbnails and names the video, and each thumbnail shows its timestamp and shot number so you can check before deleting. Groups that mix sources label each frame with its own video instead.

They are still ordinary duplicates and both buttons still work on them — but on a same-source group each asks for confirmation first, naming the video and the timestamps it is about to delete, and saying which image it keeps: *Keep best* keeps the highest-scoring one, *Keep first* keeps the one the duplicate scan picked, which is not necessarily the best. The risk is the group's, not the ranking's — either button deletes the same frames on one click. See [Videos](video.md) for where the frames came from.
