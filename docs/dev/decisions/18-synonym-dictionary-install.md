# Decision 18: The synonym dictionary installs outside the job queue, from WordNet's own data

*Index: [decisions.md](../decisions.md).*

- **Date:** 2026-11-14.
- **Status:** **Decided** (2026-11-14, by the user), both rulings, while #52 was planned.
- **Decision:**
  - **The install is its own task, not a `BackgroundJob`.** Turning on Synonym
    suggestions starts the download and conversion at once, even while a job runs.
    Settings polls `GET /dictionary/status` for progress, and the install does not appear
    in the global progress bar.
  - **Irregular word forms come from WordNet's own lists**, not a table written for
    Crucible. Planning assumed a second download (the WNDB zip's `.exc` files). The
    archive check then found the same lists already in the JSON release as each entry's
    `form` list, so one pinned download serves both.
- **Rationale:**
  - The job queue runs one job at a time. A 10 MB download queued behind a two-hour
    scoring run would leave the toggle looking broken for no reason the user could see.
  - WordNet's lists cover about 4,470 forms. A hand-made table would have covered the
    common ones and missed the rest, and it would have been one more thing to maintain.
- **Rejected:**
  - A queued `dictionary_install` job: it is consistent with every other long operation,
    but it can wait behind anything.
  - A hand-written table of about 250 irregular forms.
  - Regular forms only, which would have answered "No synonyms" for `geese`, `ran` or
    `better`.
- **Consequences:**
  - The install is the one long-running operation outside `workers/job_queue.py`. It
    needed its own shutdown hook and startup sweep (`docs/dev/captioning/dictionary.md` § Install).
  - Its failure shows only where the user is looking: in Settings or on the card.
- **Revisit when:** a second job-shaped task wants to bypass the queue. At that point a
  queue lane for short downloads may beat a second hand-rolled task.
