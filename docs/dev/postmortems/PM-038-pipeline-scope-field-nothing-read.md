# PM-038: A pipeline ignored "Uncaptioned only" and recaptioned everything

### Symptom

On the Captioning page, a run with two or more steps and the scope set to **Uncaptioned
only** captioned every image in the subfolder, captioned ones included, and overwrote the
captions they had. A one-step run on the same scope skipped them, as documented. Nothing
errored, and the job's "N images queued" toast counted the whole subfolder, times the
number of steps.

Found by reading the code while building #105 (2026-11-13), not from a report. It had been
there since pipelines shipped (`1ee70b2`, 2026-05-28).

### Root cause

Two halves, each harmless alone.

- **The route never read the field it accepted.** `PipelineStep` had an `overwrite`
  field, but `run_pipeline`'s scope query had no empty-caption filter and no line in the
  file read `step.overwrite`. `/run`'s start query has had the filter all along:
  `if not body.overwrite and body.delimiter_mode == "overwrite"`.
- **The page filled that field by a different rule.** `CaptioningPage.buildStep1` sent
  `overwrite: scope === "all"`, where the single-step `/run` call sends
  `scope !== "uncaptioned"`. Nothing read it, so the difference never showed. But making
  the route read it as-is would have broken the **Selected** scope instead, skipping the
  selected images that already had a caption.

The scope is a property of the whole request, and the schema put it on every step, so
there was no single place where it was decided.

### Generalizable rule

**A request field nothing reads is a promise the server does not keep. Read it, refuse
it, or remove it.**

- When a schema accepts a field, grep for the read. A field with a default and no reader
  looks configured to every caller and does nothing.
- Put a setting at the level it applies to. One scope for the whole request is one field
  on the request, not a copy on every step that the server must pick one of.
- When two code paths of one page build the same setting, build it with the same
  expression, and pin it with a test on the request body.
- M10 met the same class in `CaptionPipelineRequest.save_backup` and
  `rename_on_caption`, which the loop never read either. They are refused with a 422 until
  they are wired up. This one is removed, since the request-level field replaces it.

### Why it wasn't caught the first time

- **No test sent `overwrite: false` to `/pipeline`.** Every pipeline test left it at its
  default of `true`, which is also what the missing filter does.
- **The user doc stated the rule for one-step runs only.** "Overwrite mode with
  *Uncaptioned only* scope skips already-captioned images" was true of `/run`. Nothing
  said whether it held for a pipeline, and nobody checked.
- **The field looked used.** The page set it on every step, so reading the frontend
  alone suggested the backend honoured it.

### Fix

`fix/pipeline-uncaptioned-scope` (2026-11-14):

- **`CaptionPipelineRequest.overwrite`** (default `true`) decides the scope once, by
  `/run`'s rule, with step 1's `delimiter_mode` standing in for the request's: `false`
  with an overwriting step 1 takes only images whose caption is empty. An appending or
  prepending step 1 takes them all, since it needs the captions.
- **`PipelineStep.overwrite` is gone.** An old client that still sends it is ignored, not
  refused, and gets the default it always got.
- **The page sends `overwrite: scope !== "uncaptioned"`** on the pipeline request, the same
  expression as its `/run` call. The frontend type makes the field required.
- **Tests.** `backend/tests/test_pipeline_scope.py` pins the filter, the append exception,
  the default, the explicit-ids path and the inert old field.
  `frontend/e2e/captioning-pipeline-scope.spec.ts` pins the body the page sends for each
  scope, Selected included.

### Status & date

MITIGATED, fixed 2026-11-14. The field nothing read is removed, and both halves are pinned
by tests. The class stays live wherever a schema accepts a field: check for its reader.
Last reviewed for staleness: 2026-11-14.
