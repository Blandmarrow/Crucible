# PM-023: a job finished green while every image had timed out

### Symptom

A user captioning with a local reasoning model through LM Studio reported that captioning
was "not working". The LM Studio log showed the request at 15:02:45 and
`Client disconnected. Stopping generation...` at 15:04:45 — exactly 120.000 s. Crucible
was hanging up on the model, not the other way round.

What made it undiagnosable rather than merely wrong: the run **completed**. The job row
reached `completed`, the Logs page showed it green with no error, the dataset simply had
fewer captions than images, and nothing anywhere named a timeout. The timeout itself was a
hardcoded `timeout=120.0` in `openai_compat_captioner.py`, exposed in no UI and disagreeing
with `ollama_captioner`'s 300 s and `prompt_generator`'s own `_TIMEOUT_S`.

### Root cause

Two mechanisms, and the second is the one worth remembering.

**The timeout was a literal.** A per-image budget was a constant in three modules rather
than a property of the provider the user configured, so the only fix available to the user
was to not use that model.

**The failure had nowhere to be seen.** `_run` and `_run_pipeline_job` swallow a per-image
exception into a local `failed_image_ids` and keep going — correct behaviour; one bad image
should not fail a 5,000-image run. But the loop then *returns normally*, so
`job_queue.py:179` marks the row `completed` and leaves `error_msg` unset, and `LogsPage`
renders only `status`, `label` and `error_msg`. The diagnosis existed — as a `logger.error`
line in uvicorn's stdout, a place no user of a local GUI app looks. The `caption_summary`
SSE event was the one user-visible surface, and it is live-only: gone on reload, absent
entirely for a job the user navigated away from. So the job's own record carried no trace
of why a run came back short.

A third, smaller mechanism sat on top once the timeout became configurable: the openai
SDK's `DEFAULT_MAX_RETRIES = 2`, and it retries timeouts. A field labelled "Timeout" in the
UI was really a third of the true ceiling — 10800 s at the 3600 s maximum — and since
cancellation is polled at image boundaries only, **Stop** was unresponsive for that whole
window.

### Generalizable rule

**Flag any per-item job loop that swallows a failure into a local list.** The job row still
returns normally, so the Logs page shows it green with `error_msg` unset — the diagnosis
must reach `result_data` (or `error_msg`), never `logger.error` alone. An SSE event is not
a substitute: it is live-only, so it says nothing to a user who reloaded, navigated away,
or came back tomorrow.

Two corollaries worth applying on sight:

- **A duration a user can configure must be the duration that elapses.** Check the client
  library's retry default before writing the label — a silent 3× multiplier makes the
  number in the UI a lie, and where cancellation is cooperative it is also the Stop latency.
- **A per-item budget hardcoded in a module is a bug the user cannot route around.** If two
  modules carry the same literal and disagree, it was never a constant.

### Why it wasn't caught the first time

The suite asserts that a caption job **completes**, which is exactly the state the bug
produces. There is no test that a job which lost items reports having lost them, because
"the job succeeded" and "the work succeeded" were never distinguished — the failure list was
treated as internal bookkeeping rather than as an output.

The review question that would have caught it is not about timeouts at all: *when this
`except` swallows the error, where does the user read about it afterwards?* Answering
"the terminal" is the finding.

The retry multiplier went unnoticed for a different reason: the first commit on this branch
documented it (`docs/dev/captioning.md` gained a sentence saying one image can burn
3 × `timeout_s`) and shipped it anyway. Writing a caveat down is not the same as deciding
it is acceptable.

### Fix

Commit `4b44072` made the timeout a per-provider `timeout_s` column (10 s – 1 h, default
300 s, editable in Settings → LLM Providers) and added the `APITimeoutError` branch ahead of
each broad handler. The follow-up commit on the same branch closed the rest:

- All three client constructions pass `max_retries=0` explicitly
  (`openai_compat_captioner.py`, `prompt_generator.py`, `providers.py::_list_models`), so
  `timeout_s` bounds one attempt and therefore also bounds Stop. The trade-off — no
  automatic 429/5xx backoff — is recorded at each site; a failed image is recoverable by
  re-running with the *Uncaptioned only* scope.
- Both caption loops accumulate `failed_details` and a `timed_out` counter, build one
  headline via `_failure_headline`, and write
  `{failed_count, timed_out, failure_summary, failed:[{file, error}]}` to
  `BackgroundJob.result_data` before `refresh_stats` at each tail. The same headline rides
  the `caption_summary` SSE event as `failure_summary`.
- `LogsPage.tsx` renders a `--warn` line for any job with `result_data.failed_count > 0`,
  keyed on that generic name so import jobs light up too; `CaptioningPage`'s amber badge
  shows the headline in place of its hardcoded rate-limiting guess.
- That badge turned out never to have rendered at all, which the end-to-end check found and
  a unit test never could. `submittedActiveJobId` fell to `null` once the completion effect
  recorded the job as terminal, so the Live-progress panel unmounted on the first re-render
  after completion and both the "✓ Captioning complete" line and the failed-images badge
  were dead UI. It now falls back to the most recently submitted id when every entry is
  terminal. **Verifying a surface means looking at it**: the SSE event carried
  `failed_count` correctly the whole time, so every check short of rendering the page passed.
- `max_tokens` regained an upper bound (`le=2**31 - 1`) after the branch dropped `le=32768`
  entirely: without it `{"max_tokens": 10**30}` raised `OverflowError: Python int too large
  to convert to SQLite INTEGER` out of the commit and 500'd. The ceiling is the bind's, not
  a model limit.

Known gap, left in place deliberately and commented at both tails: a **cancelled** run
reaches neither tail (`raise_if_cancelled` propagates out of the loop), so it writes no
`result_data` and emits no summary — already true of the SSE event before this change.
Surviving cancellation needs a `try/finally` around each loop.

### Status & date

MITIGATED. Last reviewed for staleness: 2026-09-03.
