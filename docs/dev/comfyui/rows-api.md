# Row folders, place, copy, the folder tree and plan duplicate

The `comfy` router's endpoints that move rows and folders: the per-row folder and its exemption from the reset, `rows/set-subfolder` and the `linked` modes, the lock a live run puts on its plan, `rows/place`, `rows/copy`, the folder tree's endpoints and plan duplicate. The tables, `comfy_service.py`, plan and row CRUD, the prompt library and the gotchas are `docs/dev/comfyui/queue.md`; the Rows tab that calls these is `docs/dev/comfyui/rows-tab.md`.

## The row folder and `linked`

**The folder is exempt from the pending reset** that a `values` edit causes (`docs/dev/comfyui/queue.md` § Backend), and its bulk write is its own endpoint.
`RowUpdate.subfolder` (≤512, `normalize_subfolder`d) is handled in `update_row` *after* the
`values` branch, so a combined `{values, subfolder}` PATCH still resets — but a folder-only
edit does not, because it changes where the next image is filed rather than what image would
be produced, and a reset would make the next run regenerate a correct image.
`POST /comfy/plans/{id}/rows/set-subfolder` (`{subfolder, row_ids?}` → `{updated}`) writes one
folder across a selection: `row_ids: null` means every row, `[]` means none (`bulk_edit_rows`'
convention), no-op rows are skipped, and completed rows keep their status. It is **not** a
branch inside `rows/set-value` — that endpoint is hard-gated on the plan's pinned aliases, and
a folder is not a pinned parameter. It also deliberately declares nothing on the dataset: an
edit is plan intent, and declaring per commit would fill `Dataset.declared_subfolders` with
typos. `leaf_id` is not settable through either endpoint.

**Both take `linked: "refuse" | "unlink" | "skip"`** (default `"refuse"`, M6 §8b), for rows
the blueprint still counts. Only rows whose folder would change count; `"refuse"` 409s and
moves nothing, `"unlink"` also clears `leaf_id`, `"skip"` moves the rest. Why, and what
*linked* means, is `docs/dev/comfyui/structured.md` § Moving a blueprint row. `GET …/rows`
returns each row's `blueprint_linked`; the `PATCH` response does not, so the page refetches.

## A live run locks its plan's folders and rows

**A live run locks its plan's folders and rows** (M6 §8c). A `comfy_generate` run files each
row by the `subfolder` of the row object it loaded at start, and commits `status` on each in
turn. So while one is queued or running, `_refuse_folder_change_during_run` 409s every folder
change — `PATCH` (a real change, not a no-op), `rows/set-subfolder` and `rows/place` — and
`rows/delete` 409s outright. Since the 2026-11-05 review, `rows/delete` and
`DELETE /comfy/plans/{id}` (plan CRUD, `docs/dev/comfyui/queue.md` § Backend) take `_plan_write_guard` (below), so they also refuse a running or
paused automated Run and a live prompt or rewrite job: deleting rows under a rewrite wasted its
paid calls, and a plan delete under a run cascaded its rows away mid-job (the run's next
`status` commit then raised). Before §8c only the page enforced this, and its `isRunning` knew
only the runs that tab had started. Each check runs under `_plan_lock`, which `_start_run`
takes too, so no run starts between the check and the commit. For that reason the PATCH folder
branch and `set-subfolder` take the lock in every `linked` mode now, not only `"unlink"`.

## `rows/place`

**`POST …/rows/place`** (`{row_ids, anchor_row_id?, position, subfolder?, linked}` →
`{placed, unlinked, skipped}`, M6 §8c) moves rows to a spot in the run order and optionally
re-files them, in one step: the drag between rows, and the row menu's Move up and down.
- The server computes the whole order with `services/comfy_rows.py`'s `place_rows`, unlike
  `rows/reorder`, whose full id list goes stale the moment another tab adds a row. The moved
  rows land as one block, in their existing run order, before or after the anchor. With no
  anchor they land after the target folder's last row, or at the end when no folder is given
  or nobody is in it yet. Then the plan is renumbered `0..n-1`.
- A folder change follows `set-subfolder`'s rules: the run guard above, and `linked` for rows
  the blueprint counts. A skipped row neither moves folder nor changes place.
- 400s: an id from another plan, an anchor from another plan, an anchor that is itself moving
  (the client picks the nearest row that stays put), and `..` in the folder.
- A plain reorder passes during a run: the run's row list and seeds were fixed when it started.
- **Run order matters beyond the run.** `sort_order` also decides which rows survive the
  image-cap clamp, Top up's pending order, and each row's `increment` seed. The table groups
  rows by folder, so the run order *across* folders is not what the table shows.

## `rows/copy`

**`POST /comfy/plans/{target}/rows/copy`** (`{source_plan_id, row_ids, move, subfolder?, linked}`
→ `{copied | moved, skipped, unlinked}`, M6 §8c) copies rows from another plan, in any
dataset, into this one. A drop on a plan copies and Shift moves (the plan's ruling 3). Before
§8c the only route was the library's *Other plans* tab, which carried prompt text alone and
dropped the folder.
- **What a new row carries.** The source's **effective** prompt (`effective_prompt`: row →
  run default → template, `docs/dev/comfyui/queue.md` § Backend), written under the *target's* prompt alias, since the two plans may
  name their prompt pins differently. Every other value whose alias is a **per-row** pin in
  the target, minus the source's own prompt alias. And the row's folder, unless `subfolder`
  overrides it.
  - Only per-row pins carry, a deliberate narrowing of the plan's "every value whose alias
    exists". `patch_workflow` (`docs/dev/comfyui/queue.md` § Backend) applies any alias in `values`, so a value carried under a
    run-default pin would silently override that default, with no column to show it.
- It starts pending, with `leaf_id` NULL (the target's blueprint never made it), `prompt_origin`
  NULL (both `docs/dev/comfyui/queue.md` § Concepts & data model), and no images or error. Rows are appended in source run order. A row whose effective
  prompt is empty is skipped, and a move leaves it in the source.
- **A move deletes the source rows**, so it takes the source's folder-write checks inline:
  `_refuse_live_run`, a 409 while a `_ROW_JOB_TYPES` job is live, a 409 on any `running` row,
  and `linked` for rows the source's blueprint counts (refuse 409s; skip leaves them). Inline,
  not `_plan_write_guard`, because the endpoint already holds both plans' locks, taken once
  each in id order, and `asyncio.Lock` is not re-entrant.
- **The target refuses a live automated Run**: `_refuse_live_run(target)`, inside the two
  locks. `rows/bulk` (paste, *Import .txt*, a library drop or *Add n to plan*) and
  `POST …/rows` (*Add row*) take `_plan_lock` and do the same. That lock is not the one an
  automated Run is created under (`run_service.start_run` holds the dataset lock), so a Run
  starting between the check and the insert is not excluded: the window Apply has too. A running or paused Run's next pass takes every pending row
  of its plan, so rows added under it would be counted as the Run's own and filed outside its
  budget: the harm `_refuse_live_run` already stops for Apply. The user's ruling on 2026-11-05;
  before it, both routes added rows to a live Run's plan unguarded. A live generate *job* on the
  target is not refused, since it runs only the rows it loaded at start.
- The response is `{copied | moved, skipped, skipped_no_prompt, skipped_linked, unlinked}`.
  `skipped` is the sum of the two reasons: a row with an empty effective prompt, and, on a
  move under `linked: "skip"`, a row the source's blueprint counts. `unlinked` counts only the
  linked rows actually moved.
- 400s: the source is the target, the target has no prompt pin, or an id is not the source's.

## The folder tree's endpoints

**The folder tree's endpoints** (M6 §8b, migration `d5b7f9a1c3e6`). A plan's folders are its
rows' `subfolder` values, their ancestors, and `comfy_plans.declared_folders` (JSON list, NOT
NULL, `'[]'`): folders made by hand, kept while empty. Every `_plan_out` returns it.
- `POST …/folders` (`{path}` → the plan) declares a folder and its ancestors. 400 empty, 409
  if it exists in either source.
- `POST …/folders/move` (`{path, new_path}` → `{updated}`) renames or re-nests a folder with
  its rows: an exact match plus a prefix compare on `comfy_rows.subfolder`
  (`substr(subfolder, 1, len+1) = path + '/'`, `_descendant_clause`), then
  `utils.remap_declared_paths` on the list, the helper the gallery's `repath_subfolder` uses.
  400 for an empty path, the same path or a move into itself; 404 missing; 409 if the
  destination exists.
- `POST …/folders/delete` (`{path}` → `{deleted}`) deletes the subtree's rows and drops it
  from the list (`utils.drop_declared_paths`). Images are never touched.
- Move and delete 409 when the subtree holds a `blueprint_linked` row ("This folder comes
  from the blueprint…"). All three share `_plan_write_guard` (named `_folder_write_guard`
  until the 2026-11-05 review gave it the deletes too): `_plan_lock`, then `_refuse_live_run`,
  then a 409 while a `_ROW_JOB_TYPES` job is queued or running for the plan. The request's own 400s
  run first, outside the lock; the 404, the 409s and the length 400 below run inside it. `set-subfolder` and the
  `PATCH` folder branch take the plan lock in every `linked` mode and the run guard above;
  an unlink also refuses a live automated run, as Apply does.
- **A move cannot make a path over 512 characters** (`utils.SUBFOLDER_MAX_LEN`). The request
  caps `new_path` alone, so after the collision 409 the route works out the longest path the
  move would write over the subtree, `len(new_path) + len(p) - len(path)`, and 400s past the
  cap. Without it a row's folder could grow past 512, and every later `folders/move` or
  `folders/delete` on it failed validation (422), leaving the tree no way to reach it.
  `library/categories/move` does the same with `CATEGORY_MAX_LEN`, and `blueprint_expand`'s
  leaf-path check uses the constant.
- **Not `LIKE`.** SQLite's `LIKE` ignores ASCII case, so `knight/%` also matched `Knight/a`,
  and deleting one folder deleted another's rows (the §8b review). The gallery's
  `dataset_service.repath_subfolder` still uses `LIKE` and has the same flaw, left for its
  own fix.

## Plan duplicate

**`POST /comfy/plans/{id}/duplicate`** (`{name, include_rows}` → the plan-list element shape,
`{id, dataset_id, name, row_count, status_counts}`). 400 on a taken name, as create does. It
deep-copies the workflow, pins, output nodes, the synthetic flag, `gen_settings` and
`declared_folders`, and the blueprint's spec, seed and brief, never its draft. With
`include_rows` it copies the leaves under fresh ids and the rows reset to pending
(`error_msg`, `prompt_id`, `image_id` cleared, `image_ids` `[]`), keeping folder, values,
order and `prompt_origin`, with `leaf_id` remapped onto the new leaves (an orphan's becomes
NULL). Without it there are no leaves, and the next Apply builds them. Runs, jobs and images
are never copied.
