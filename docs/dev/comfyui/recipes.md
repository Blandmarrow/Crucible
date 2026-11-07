# ComfyUI — run recipes and the remembered start form

M6 §6 keeps a run's settings in two places. The **recipe library** holds named, global
recipes saved from the Runs page, with a JSON document to export and import them. The
**remembered form** is the start form as last left, kept on the server. This file covers
both, and the converters between the form and a recipe that both rely on. How a recipe
runs is `docs/dev/comfyui/run-stages.md` (the stages), `docs/dev/comfyui/run-start.md` (the
start check) and `docs/dev/comfyui/run-loop.md` (the loop). The user guide is
`docs/run-setup.md` § Saving the form as a recipe.

## The library

`SavedRunRecipe` (`backend/models/run_recipe.py`, table `run_recipes`) has an `id`, a
`name`, a `name_key`, the `recipe` and `created_at` / `updated_at`. **It is global, with no dataset or plan foreign
key**, for the reason the prompt library is: a recipe describes a process, not a dataset.
`name_key` is the name with whitespace runs folded and case folded (`name_key()`), and it
is unique, so "Night set" and "night  set" are one recipe. The routes are under their own
prefix, `/run-recipes`, rather than `/runs`, whose `/{run_id}` routes would sit beside them:

| Route | Does |
|---|---|
| `GET /run-recipes/` | Every recipe, by `name_key`. |
| `POST /run-recipes/` | `RecipeSave {name, recipe, replace}`. A taken name is a 409 unless `replace`, which keeps the recipe's id. |
| `PATCH /run-recipes/{id}` | Rename; a name another recipe has is a 409. |
| `DELETE /run-recipes/{id}` | 204. A run started from it keeps its own copy (`Run.recipe`). |
| `GET /run-recipes/{id}/export` | The document (below). |
| `POST /run-recipes/import` | A document in, saved under its own name or the next free `name (2)`, with notes. |

**A stored recipe is a `RunRecipeDraft`** (`schemas/run.py`), not a `RunRecipe`. The draft's
`CaptionDraft`, `ExportDraft` and `RewriteDraft` let the caption model, export folder,
rewrite provider and scenarios be empty, which is what an imported recipe can need. The
stage rules (`_settings_present`) still hold, and the start still validates the strict
`RunRecipe`, so an unfinished recipe loads into the form and its blockers say what to fill.
Every save also checks that a non-empty export folder is absolute (`sanitize_abs_path`)
and the base folder has no `..` (`normalize_subfolder`). Two saves of one name can both
pass the lookup; the loser hits the unique `name_key`, and `_commit_unique` turns that
`IntegrityError` into the same 409 (save, rename and import alike).

## The document, and what never travels

A saved recipe keeps this machine's provider ids and absolute export folder, so loading
it here fills everything in (the user's ruling, 2026-11-04). The exported document is
`{format: "crucible.run-recipe", version: 1, name, recipe, providers: {caption, rewrite}}`,
built by `run_recipe_service.to_document`:
- the export folder is blanked;
- each provider id becomes the provider's **name** in `providers`, and the caption model
  `openai_compat:<id>:<model>` becomes `openai_compat::<model>`.

`from_document` reverses it, and **every import is untrusted input**:
- a wrong `format`, a newer `version` (a boolean is not a version) or no `recipe` is a
  400, and so is a recipe the draft rejects;
- the export folder is dropped whatever it holds, since it names a folder on the machine
  that made the file;
- providers resolve **by name** (matched ignoring case; the database only keeps names unique exactly), and never
  by an id in the file. A name with no match leaves the field empty, with a note. So does a
  name that matches **more than one** provider ignoring case: none is picked, since the
  wrong one would send a run to the wrong server, and the note says "(ambiguous)";
- the recipe goes through `RunRecipeDraft.model_validate`, whose default
  `extra="ignore"` drops every key it does not define, nested or not. That is what keeps
  an `api_key` or a `plan_id` in a file out of the database: the recipe models have no
  such field. `test_an_import_never_stores_an_api_key_a_plan_id_or_a_foreign_folder`
  holds it, so adding `extra="allow"` to any of them fails CI.

A document over `MAX_DOCUMENT_BYTES` (256 KiB) is refused before validation (`POST /run-recipes/import` measures the body it already
has, so the check sits ahead of `from_document`).

## The form and the recipe

`components/runs/runRecipeForm.ts` holds `RunFormState`, the whole start form as one
object, the two directions, and every inverse converter:
- `formToRecipe(form, ctx)` is the one builder Start, Dry run and Save use. `ctx` is what
  the page works out around the form: the rewrite provider, the scenarios ticked for this
  plan, and whether the paid-call cap is sent.
- `recipeToForm(recipe, keep, ctx)` fills the form from a stored recipe, keeping the
  form's plan and run name. It clears a caption or rewrite provider not set up here, and
  returns a note for that, for rewrite scenarios this plan lacks, and for a missing export
  folder. The page shows each note as a toast.

**The forward converters are lossy** (`toCaptionSettings`, `toExportSettings` in
`runForms.ts`; `toLoopSettings`, `toGateSettings`, `toRewriteSettings` in
`runLoopForms.ts`). **Their inverses all live in `runRecipeForm.ts`**
(`fromCaptionSettings`, `fromExportSettings`, `fromLoopSettings`, `fromGateSettings`,
`fromRewriteSettings`, `delimiterParts`) and have to match them exactly:
- `fromCaptionSettings` splits `openai_compat:<id>:<model>` into `base` and
  `providerModel`, joining the rest back with `:` because a model name can hold colons.
- `delimiterParts` tokenizes the joined delimiter against `DELIMITER_PRESETS`, keeping
  anything else as one custom part.
- `fromExportSettings` treats a `resize_to` in `EXPORT_RESIZE_PRESETS` (512, 768, 1024) as
  a preset button and anything else as Custom. Those numbers must stay the buttons
  `RunExportFields` draws.
- A nullable aesthetic or style minimum becomes its on switch plus a value; `loop: null`
  is Repeat off.

There is no frontend unit runner, so the round trip is guarded end to end:
`runs.spec.ts`'s recipe journey sets every section away from its default, saves, changes
the form, loads, and asserts the dry-run request it sends equals the first one.

## The recipe bar

`components/runs/RecipeBar.tsx` sits at the top of the start form: a recipe picker with
Load, Rename, Export and Delete, a name field with **Save as recipe**, and **Import…**. A
409 on save turns into a "Replace?" `ConfirmDialog` that saves again with `replace`.
Export downloads through `utils/download.triggerDownload`, lifted out of `StatsPage` for
this. An import is loaded into the form at once and its notes shown. Past runs' **Use
these settings** goes through `RunsPage`'s `loadRequest`, which the form clears once it
has applied it, so a form mounted later never applies a stale one.

## The remembered form

`ThresholdSettings.run_form` (`JSON(none_as_null=True)`, NULL meaning never saved) is the
start form as last left, behind `GET`/`PUT`/`DELETE /app-data/run-form`. Those routes
mirror the captioning workflow's and share its size cap. The form follows
`docs/dev/app-stored-data.md`'s pattern: `ServerStateGate` waits for
`serverWritesSettled(RUN_FORM_PATH)` and the value, the form seeds its state from it once,
and `useDebouncedPersist` saves through `writeToServer` as it changes.

The blob is `{v: 1, form, byDataset}` (`toSavedRunForm`):
- `form` is everything but the plan, the base folder and the run name, which is per run
  and never kept;
- `byDataset` keeps `{planId, subfolder}` for each dataset, re-inserted last when touched
  and capped at the 50 latest. The form state holds them per dataset too, so switching
  datasets shows that dataset's own without a reset effect. A plan id the dataset no
  longer has falls back to its first plan (`effectivePlanId`), rather than sending a plan
  of another dataset.

`hydrateRunForm` reads a blob section by section over the defaults and drops stage keys it
does not know, so a blob from an older build still loads.

The remembered form is server state, so **e2e journeys share it**. `runs.spec.ts` deletes
it before each test and again after one, having left the page first so its last save
lands before the delete.

## Testing

- `backend/tests/test_run_recipes_http.py`: folded unique names, replace keeping the id,
  rename clashes, an absolute or empty export folder, the document's stripping and an
  import's resolution, nothing beyond the recipe's own fields stored, and the refusals.
- `backend/tests/test_app_data_http.py`: the `run-form` round trip, SQL NULL after delete,
  the size cap, and the captioning workflow beside it untouched.
- `frontend/e2e/runs.spec.ts`: the recipe round trip, export and import, **Use these
  settings**, and the remembered form with a stale plan.
