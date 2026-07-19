# PM-002: Pre-restore auto-snapshot landed on "main" and moved main's head

### Symptom

While working on a non-main branch, every restore/checkout created its safety
snapshot on the branch named "main" and advanced **main's** `head_version_id` — the
"Current" badge jumped on a branch the user wasn't even on, and main's history was
polluted with `Pre-restore auto-snapshot` entries from other branches' work. Caught
in a code review plus a repro harness, not in production.

### Root cause

`create_snapshot` resolved its default branch via `_ensure_main_branch(...)` — a
hardcoded lookup of the branch literally named `"main"` — instead of the dataset's
`current_branch_id`. Auto-snapshot callers (pre-restore, checkout) passed no
`branch_id`, so they always inherited the hardcoded default.

### Generalizable rule

Flag any auto-created record whose context (branch, dataset, folder, owner) is
resolved from a hardcoded name or a "first row" default instead of the current
state the user is actually in. Defaults that are correct in the single-context case
silently corrupt state the moment a second context (branch, dataset) exists.

### Why it wasn't caught the first time

Branching shipped after snapshots; the snapshot default was never revisited. All
tests ran on datasets that only had a main branch, where the hardcoded default and
the correct answer coincide.

### Fix

`create_snapshot` (and `create_branch`'s `from_version_id` default) now resolve the
dataset's `current_branch_id` first, falling back to main only for never-branched
datasets. Regression test: `test_pre_restore_snapshot_lands_on_active_branch` in
`backend/tests/test_versioning_restore.py`.

### Status & date

MITIGATED. Last reviewed for staleness: 2026-07-19.
