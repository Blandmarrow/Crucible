# Versioning routes

This file covers `backend/routers/versioning.py` (prefix `/datasets`, registered in
`main.py`): the `/{id}/versions` route table, which routes run as background jobs, and the
rule that every id must belong to the dataset in the URL.

The service functions behind the routes are `docs/dev/versioning/storage.md` (snapshot
create, and the version delete's head reassignment, which lives in this router), `docs/dev/versioning/restore.md` (restore and checkout) and
`docs/dev/versioning/mirrors.md` (diff); the mode and busy gates they pass through are
`docs/dev/versioning/guards.md`.

## Route table

| Method | Path | Behaviour |
|---|---|---|
| `GET` | `/{id}/versions/branches` | List branches |
| `POST` | `/{id}/versions/branches` | Create branch; body: `BranchCreate { name, from_version_id?, include_snapshot: bool = true }`. Sync ≤100 images or when `include_snapshot=false`, else bg job |
| `POST` | `/{id}/versions/branches/{branch_id}/checkout` | Checkout branch (always bg job); body: `CheckoutRequest { pre_restore_snapshot: bool = true }` |
| `DELETE` | `/{id}/versions/branches/{branch_id}` | Delete branch + all its versions (cascade); 400 if last branch or if branch is currently active (`dataset.current_branch_id`) — must switch first |
| `GET` | `/{id}/versions` | List versions — filters: `branch_id`, `search` (name/description ilike), `created_after`/`created_before` (ISO date strings); sorted pinned-first then `created_at DESC` |
| `POST` | `/{id}/versions` | Create snapshot (manual mode always bg job; auto mode inline ≤100 images) |
| `GET` | `/{id}/versions/diff` | Diff two versions (`?v1=&v2=`) — declared BEFORE `/{version_id}` to prevent FastAPI collision |
| `GET` | `/{id}/versions/{version_id}` | Get version detail |
| `PATCH` | `/{id}/versions/{version_id}` | Update version (`is_pinned`) |
| `DELETE` | `/{id}/versions/{version_id}` | Delete version (400 if last on branch) |
| `POST` | `/{id}/versions/{version_id}/restore` | Restore (always bg job → `{job_id}`); the outcome, failures included, lands in `result_data` (see `docs/dev/versioning/restore.md`) |
| `POST` | `/{id}/versions/prune` | Prune unreferenced object-store files (bg job `prune_versions` → `{job_id}`); 400 if versioning mode is `off`; summary written to `BackgroundJob.result_data` and emitted as the final progress message |

## Every id belongs to the dataset

**Every id must belong to the dataset in the URL** (GPT-024). A `branch_id` (snapshot create, checkout) or a `from_version_id`/`version_id` (branch create, restore, diff, the version routes) from another dataset — or one that does not exist — answers **404** *before* any job row is created. The service functions refuse the same ids with a `ValueError`, so a caller that skips the router cannot move another dataset's branch head either.
