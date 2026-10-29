# Dataset Versioning

Snapshot-based version control for datasets — similar in concept to git commits.

## Three versioning modes

Configured in Settings:

| Mode | Behaviour |
|---|---|
| **Off** | Versioning disabled; the Versions page is hidden and anything that would write a snapshot, branch or restore is refused |
| **Manual** | Every snapshot eagerly copies all image files to a content-addressable object store (full point-in-time backup) |
| **Auto** | Snapshot records metadata only; files are copied lazily on first overwrite (copy-on-write) — storage only grows when you actually change an image |

Whatever the mode, an image file that an existing snapshot still depends on is backed up automatically before Crucible overwrites or deletes it, so that snapshot stays restorable. This holds after you switch modes too: Auto-mode snapshots stay protected if you later switch to Manual or **Off** — which means Off mode keeps using some disk space for the snapshots you already have. Delete old snapshots and use **Prune storage** if you want that space back.

**Snapshots cover images only** — their files, captions, scores, provenance and gallery order. A dataset's **videos are never captured**, so a restore leaves every video and its saved extraction settings exactly where they were. That has a useful consequence: restoring a snapshot taken *before* you extracted frames removes those frames but keeps the clip, so re-running the extraction reproduces them exactly.

> **Auto-mode caveat**: copy-on-write only sees changes made *through* Crucible. Files edited outside the app (an external image editor, a script) are invisible to it — a snapshot taken before such an edit cannot restore the original content. Use **Manual** mode if you edit dataset files externally.

## Features

- **Snapshots** — create named, time-stamped checkpoints of a dataset with an optional description; each snapshot shows a source badge: **Manual** (user-created), **Pre-restore** (auto-created before a restore), or **Branch init** (auto-created when a new branch was made)
- **Pin** — star any snapshot to keep it pinned at the top of the version list regardless of date
- **Branches** — create named branches, each with its own independent snapshot history; switch branches and restore recent snapshots directly from the **sidebar accordion** (without navigating away), or use the full branch selector on the Versions page; delete any non-active branch (and all its snapshots) via the trash icon — the active branch must be switched away from before it can be deleted
- **Restore** — rewind the entire dataset's *images* to any prior snapshot (runs as a background job with a live progress bar); optionally auto-snapshot the current state first (this safety snapshot is created on your current branch, and the restore refuses to run if it cannot be created); the "Current" indicator moves to the restored snapshot on completion. Images added after the snapshot can be kept or removed — kept images that occupy a filename the restore needs are renamed aside (e.g. `1.png` → `1_001.png`) so nothing is lost. Images that were since moved to another dataset are restored here as a fresh copy; the moved original is untouched. With the safety snapshot turned on, the restore only starts if that snapshot could back up every file — otherwise it stops before changing anything, and says how many files could not be backed up
- **Diff** — compare any two snapshots to see which images were added, removed, or modified, with field-level changes shown per image
- **Filter** — the Versions page header has a debounced search box (matches snapshot name or description) and date-range pickers (*Created after* / *Created before*) to narrow the version list
- **Branch snapshot prompts** — configurable in Settings: prompt before checkout or branch creation (*Ask* mode) or always create snapshots automatically (*Auto* mode)
- **Prune storage** — the "Prune storage" button in the Versions page header deletes backup data no longer referenced by any snapshot (e.g. after deleting old versions), reporting how much space was freed; this cannot be undone

A snapshot captures each image's **source, URL, license and attribution** along
with its captions and scores, so a restore reverts those too — if you relabel a
dataset's rights and later rewind past that point, the old labels come back. A
diff lists provenance changes per image like any other field. See
[Source & License Provenance](provenance.md).

Every quality score is captured, including brightness, saturation and the NSFW
score — a restore brings those back too, and a diff shows one that changed
between two snapshots.

A snapshot also captures each image's **detections** — the boxes and masks on
the image detail page — and a restore puts back exactly the set the snapshot
had. That keeps them lined up with the pixels: if you crop an image and then
restore a snapshot from before the crop, the boxes return to where they were on
the uncropped picture, and a detection added after the snapshot is removed. A
diff lists detection changes per image as a short summary such as
"3: face×2, hand". Snapshots made before this update did not record detections,
so restoring one leaves each image's current detections as they are.
Duplicating a dataset still does not copy detections.

The object store lives at `{dataset_folder}/.versions/objects/` and is content-addressed — identical file content is stored only once regardless of how many snapshots reference it.

While a restore, checkout, snapshot, or prune job is running on a dataset, edits to that dataset (uploads, deletes, renames, caption saves, …) are rejected with a clear "Dataset is busy" message — retry once the job finishes. That includes renaming or deleting the dataset itself and deleting a subfolder; changing only its category or license defaults still works.

**Snapshots follow a dataset rename.** Renaming a dataset moves its folder, and every snapshot is updated to the new location, so restoring a snapshot taken before the rename puts the images back in the dataset's current folder. Snapshots made by older versions of Crucible are repaired automatically when you update.

**A restore that cannot bring every file back says so.** If some files could not be restored (a missing backup, a disk error, a file held open by another program), the restore still finishes, but the toast reads *"Restore complete with problems"* and names up to three of them; the full list is on the job's entry in **Logs**. Those images keep the pixels they had before the restore, along with their scores, dimensions and detections, while their captions and other details come from the snapshot. A branch switch reports problems the same way and still switches branch.

**A restore never writes outside its dataset.** Before changing anything, a restore checks every file it would write or move. If any lies outside the dataset's folder, or belongs to another dataset, the restore (or branch switch) stops with an error that names the problem and says "Nothing was changed" — no files, no captions and no extra pre-restore snapshot. This mainly protects old snapshots that the update could not repair safely.

Access via the **Versions** sidebar item on any dataset page.
