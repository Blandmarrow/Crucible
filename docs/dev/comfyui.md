# ComfyUI generation queue

Per-dataset prompt/parameter queues run against a remote ComfyUI server via its native HTTP
API, with outputs auto-imported into the dataset. Covers the data model (plans, rows, the
prompt library), `comfy_service.py` and the `comfy` router's endpoints, and the gotchas shared
by all of it. The `comfy_generate` job that `POST /comfy/run` queues is
`docs/dev/comfy-run.md`, and `ComfyPage` is `docs/dev/comfy-page.md`.

### Concepts & data model

A **plan** (`comfy_plans`, `backend/models/comfy.py`) belongs to a dataset and stores:

- `gen_settings` — the "Generate prompts" form, NULL until first saved. See
  `docs/dev/comfy-prompts.md`.
- `workflow_json` — a ComfyUI **API-format** workflow (`{node_id: {class_type, inputs, _meta?}}`),
  exported in ComfyUI via *Workflow → Export (API)*. The UI-format editor export (`nodes`/`links`
  keys) is rejected client-side with a hint.
- `pinned_params` — JSON list of `{node_id, input, alias, is_prompt, per_row, value, int_mode}`.
  **Two-tier model**: a pin with `per_row=False` is a *run default* — its `value` (null =
  template) applies to every row, no queue column; `per_row=True` gives it a queue-table
  column whose blank cells fall back to the run default, then the template. At most one pin
  has `is_prompt=True` (enforced in `_validate_pins`, and forced `per_row=True`) — it drives
  bulk prompt paste, .txt import, caption text, and the progress-bar preview. `int_mode`
  (`null/fixed|random|increment`, integer params) applies when a row has no value:
  `random.randint(0, 2**48)` / `base + row_index_within_run` where base = run default or
  template. Replaces the old plan-level `seed_mode`.
- `output_node_ids` — JSON list of workflow node ids whose history outputs are imported
  regardless of image type (so PreviewImage nodes work as import sources; their temp files
  are downloaded before ComfyUI clears them, and nothing accumulates in ComfyUI's output
  folder). `[]` = auto: all `type=="output"` images (SaveImage nodes). In `PATCH /comfy/plans`
  `[]` clears back to auto (absent = unchanged).
- `output_is_synthetic` — bool, `NOT NULL DEFAULT true` (migration `c3f7a9e1d4b6`): the plan's own
  declaration that its output is self-created. `POST` defaults it to `true`, `PATCH` treats
  `null`/absent as unchanged. Drives `_comfy_output_provenance` — see `docs/dev/provenance.md`.

A **row** (`comfy_rows`) holds `values` (`{alias: json-native value}`; absent/blank → template
value), `status` (`pending|running|completed|failed`), `error_msg`, `sort_order`, `prompt_id`
(last ComfyUI prompt id), and output links: `image_id` (FK → images, `SET NULL`, first output)
plus `image_ids` (JSON list — multi-SaveImage workflows produce several images per row).
Plan names are unique per dataset (`uq_comfy_plan_dataset_name`).

Two more row columns (migration `e5c1a9d3f7b2`) give each row its own destination:

- `subfolder` — `String(512)` NOT NULL `server_default=""`, matching `Image.subfolder`'s width.
  The run nests it under the run's base folder, so a plan can file twenty prompts into twenty
  folders instead of one pile. Gallery subfolders are virtual (`Image.subfolder` +
  `Dataset.declared_subfolders`, nothing nests on disk — see `docs/dev/gallery.md`), so this is
  a label, not a path. **The Python-side `default=""` is load-bearing**: `create_row` and
  `bulk_add_rows` construct `ComfyRow(...)` directly, so a `server_default` alone would leave
  `row.subfolder` as `None` until the object is refreshed. `status` already carries both.
- `leaf_id` — `String(36)` NULL, plain index `ix_comfy_rows_leaf_id`. Column-only today: nothing
  writes it, and `_row_out` serializes it read-only. It names the blueprint leaf that minted the
  row once structured generation exists. **Deliberately no foreign key**: a row whose leaf a
  blueprint edit deleted should keep naming it as provenance, and an inline column-level
  reference reads back off SQLite with no `ondelete`, which `scripts/check_migrations.py` then
  reports as permanent drift (`a7c3e5b1d9f2`'s docstring covers that at length).
  `VersionImageState.label_ids` and `quality_flags["duplicate_of"]` are FK-less for related
  reasons. `ComfyRow` is not `Image` — none of the `VersionImageState` mirror rules apply.

The ComfyUI server URL is the global `ThresholdSettings.comfyui_url` (Settings → ComfyUI tab,
with a Test-connection button → `GET /comfy/ping`, which hits `{url}/system_stats`). The same
tab also holds `ThresholdSettings.comfy_workflow_dir` — the default folder scanned for
workflow `.json` exports (with a Browse button via `DirPickerModal`); a path on the machine
running the backend.

### Backend (`backend/routers/comfy.py`, `backend/services/comfy_service.py`)

`comfy_service.py` is pure (no DB/HTTP routing): `ComfyClient` wraps the ComfyUI endpoints
(`POST /prompt`, `GET /history/{id}`, `GET /view`, and `GET`/`POST /queue` plus a
prompt-targeted `POST /interrupt` for `cancel_prompt`, see `docs/dev/comfy-run.md`
§ Removing our prompt); it takes an optional httpx
`transport` so tests can drive it over `httpx.MockTransport`;
`patch_workflow(workflow, pinned, row_values, row_index)` deep-copies the template and
resolves each pin as: row value (coerced to the template value's JSON type) → `int_mode`
roll (random/increment) → run default `value` → template. Coercion is
backend-authoritative: the frontend's `coerceCellValue` deliberately keeps big or
non-round-tripping numerics (seeds > 2^53, `'1e5'`, `'1.0'`) as strings, and `_coerce` /
the increment base both accept them via `_lenient_int` (`int()` → `int(float())`
fallback; garbage still raises `ComfyRowError`); `effective_prompt` follows the
same row → default → template chain; `extract_output_images(entry, output_node_ids=None)` —
default/empty: all `type == "output"` images (skips `temp` previews; a workflow with only a
PreviewImage node and no output-node selection fails its rows with a "add a SaveImage node or
select an output node" message); with the plan's `output_node_ids`: those nodes' images
regardless of type, in list order; `check_history_error` reads `status.status_str == "error"`
**only when `status` is present** (older ComfyUI omits it — entry presence alone means
completion); `workflow_format(obj)` classifies parsed workflow JSON as
`"api" | "ui" | "invalid"` (server-side mirror of the client's `isApiFormat`).

Router endpoints: plan CRUD (`/comfy/plans…`), row CRUD (`…/rows`, `…/rows/bulk` one row per
non-empty line into the `is_prompt` alias, `…/rows/delete`, `…/rows/reorder`, `…/rows/reset`
failed→pending, `GET …/rows` — full rows; `GET /comfy/plans/{id}/prompts` returns
`{prompts: [{row_id, prompt, status}]}` — the **effective** prompt (row→default→template) of
each non-empty row, used to browse/reuse prompts across plans and datasets (the
`PromptLibraryModal`), `…/rows/set-value` set/clear one pinned alias on **every** row of the plan —
`{alias, value}`, `value: null|""` clears back to the default/template), and `POST /comfy/run`. Editing a
completed/failed row's `values` via `PATCH /comfy/rows/{id}` (or touching it via
`rows/set-value`) resets it to `pending` and clears `error_msg`/`prompt_id` (image links are
kept). `workflow_json` is returned only by `GET /comfy/plans/{id}` — never in the plan list
or row responses.

**The folder is exempt from that reset**, and its bulk write is its own endpoint.
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

Prompt tooling — `POST /comfy/generate-prompts` (one LLM batch call),
`POST /comfy/plans/{id}/generate-prompts` (the durable `comfy_prompts` job), the output
parser and `GeneratePromptsModal`: `docs/dev/comfy-prompts.md`.

`POST /comfy/plans/{id}/rows/bulk-edit` (`{operation: prepend|append|remove|find_replace,
text, replacement, use_regex, row_ids?}`) mirrors caption bulk-edit semantics on the prompt
column: base = effective prompt (row → default → template), result written into row values
(changed completed/failed rows reset to pending), regex bounded like caption bulk-edit — one
30 s batch deadline enforced inside the `regex` engine via `regex_sub_deadline` (408 on timeout).
An invalid pattern is a 400 `Invalid regex: …` here and on both caption routes alike
(`backend/tests/test_comfy_bulk_edit_regex_http.py`; GPT-031 brought the caption side into line).
So is an invalid replacement template (`\9`), which only fails on a match, inside the executor:
that `regex_error` used to escape the route as a 500.

Prompt library: `comfy_library_prompts` (`ComfyLibraryPrompt`) is a **global** store of
prompt texts grouped by a free-text `category` (≤100 chars, indexed) — deliberately no
dataset/plan FK, so prompts are reusable everywhere without duplicating plans.
`GET /comfy/library` (all prompts, ordered category → created_at; client groups),
`POST /comfy/library` (`{category, prompts[]}` — blanks dropped, case/whitespace-insensitive
dedupe against the target category → `{created, skipped}`), `POST /comfy/library/move`
(`{ids, category}` re-categorize; upholds the same per-category uniqueness — a moved
prompt whose text already exists in the target is deleted instead → `{moved, merged}`),
`POST /comfy/library/delete` (`{ids}`).

Workflow sync: `GET /comfy/canvas-workflow` pulls the current workflow from ComfyUI (bridge
snapshot, else last-queued history entry) — `docs/dev/comfyui-sync.md`.
Workflow folder scan: `GET /comfy/workflow-files?dir=…` (dir falls back to
`comfy_workflow_dir`) recursively lists `.json` files (cap 500, >5 MB skipped as invalid) with
a `format` sniff via `workflow_format`; `GET /comfy/workflow-file?path=…` returns the parsed
workflow, 400 with an "Export (API)" hint for UI-format files. Both validate paths with
`utils.sanitize_abs_path` (shared with the `filesystem` router).

### Gotchas

- `server_default` strings on these columns are plain (`"pending"`, `""`) — SQLAlchemy quotes
  them itself. Do **not** copy the `"'off'"` style from `threshold_settings.versioning_mode`;
  that embeds literal quotes into the DDL default.
- **`declare_subfolder(s)` commits — never call it inside a row loop**, and never pass it `""`.
  Rationale in `docs/dev/comfy-run.md` § The `comfy_generate` job.
- **`join_subfolder` is the non-raising, job-side counterpart to `normalize_subfolder`.** Both
  live in `backend/utils.py` and share `_subfolder_parts`, so they disagree about exactly one
  thing: `normalize_subfolder` rejects `..` with a 400 (the write-time guard, used by
  `update_row`, `set_rows_subfolder` and `POST /comfy/run`'s base folder), while
  `join_subfolder` drops it. Inside a job the raise would be wrong twice over — a gallery
  subfolder is a virtual label, so a stray `..` is a wrong folder name rather than a path
  escape, and the row would fail with an unactionable message that re-fails on every re-run.
- JSON-column reassignment invariant applies to `row.values`, `row.image_ids`,
  `plan.pinned_params`, and `plan.output_node_ids` (see CLAUDE.md Key invariants).
- `/interrupt` stops whatever is executing, not necessarily ours, and `POST /queue
  {"delete"}` only touches pending prompts — so neither alone removes our prompt, and a bare
  interrupt is never sent (`docs/dev/comfy-run.md` § Removing our prompt). Never use `{"clear": true}`.
- **Test against `FakeComfy`, not a hand-rolled stub.** `backend/tests/fake_comfy.py` serves
  ComfyUI's real shapes in-process for pytest and on a real port for Playwright, and must not
  import `conftest`. What it models and how a spec picks a failure:
  `docs/dev/comfy-run.md` § Testing the run.
- Workflow sync (bridge extension, sync button, history-pull): `docs/dev/comfyui-sync.md`.
