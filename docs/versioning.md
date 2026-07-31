# Dataset Versioning

Snapshot-based version control for datasets — similar in concept to git commits.

## Three versioning modes

Configured in Settings:

| Mode | Behaviour |
|---|---|
| **Off** | Versioning disabled; the Versions page is hidden and anything that would write a snapshot, branch or restore is refused |
| **Manual** | Every snapshot eagerly copies all image files to a content-addressable object store (full point-in-time backup) |
| **Auto** | Snapshot records metadata only; files are copied lazily on first overwrite (copy-on-write) — storage only grows when you actually change an image |

In both Manual and Auto modes, image files are automatically backed up before deletion so that a pre-deletion snapshot can always be restored.

**Snapshots cover images only** — their files, captions, scores, provenance and gallery order. A dataset's **videos are never captured**, so a restore leaves every video and its saved extraction settings exactly where they were. That has a useful consequence: restoring a snapshot taken *before* you extracted frames removes those frames but keeps the clip, so re-running the extraction reproduces them exactly.

> **Auto-mode caveat**: copy-on-write only sees changes made *through* Crucible. Files edited outside the app (an external image editor, a script) are invisible to it — a snapshot taken before such an edit cannot restore the original content. Use **Manual** mode if you edit dataset files externally.

## Features

- **Snapshots** — create named, time-stamped checkpoints of a dataset with an optional description; each snapshot shows a source badge: **Manual** (user-created), **Pre-restore** (auto-created before a restore), or **Branch init** (auto-created when a new branch was made)
- **Pin** — star any snapshot to keep it pinned at the top of the version list regardless of date
- **Branches** — create named branches, each with its own independent snapshot history; switch branches and restore recent snapshots directly from the **sidebar accordion** (without navigating away), or use the full branch selector on the Versions page; delete any non-active branch (and all its snapshots) via the trash icon — the active branch must be switched away from before it can be deleted
- **Restore** — rewind the entire dataset's *images* to any prior snapshot (runs as a background job with a live progress bar); optionally auto-snapshot the current state first (this safety snapshot is created on your current branch, and the restore refuses to run if it cannot be created); the "Current" indicator moves to the restored snapshot on completion. Images added after the snapshot can be kept or removed — kept images that occupy a filename the restore needs are renamed aside (e.g. `1.png` → `1_001.png`) so nothing is lost. Images that were since moved to another dataset are restored here as a fresh copy; the moved original is untouched
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

The object store lives at `{dataset_folder}/.versions/objects/` and is content-addressed — identical file content is stored only once regardless of how many snapshots reference it.

While a restore, checkout, snapshot, or prune job is running on a dataset, edits to that dataset (uploads, deletes, renames, caption saves, …) are rejected with a clear "Dataset is busy" message — retry once the job finishes.

Access via the **Versions** sidebar item on any dataset page.
