"""The durable record of a per-item job's partial failure (PM-023).

A per-item loop that swallows a failure still returns, so `job_queue` marks the
row `completed` and the live `Failed:` progress message is gone on reload. What
survives is `result_data`: `failed_count` (which the Logs page keys its amber
line on, see `docs/dev/frontend-jobs.md`) and the one-line `failure_summary`
built here. Restore, checkout, upscale and LUT write it.
"""

# How many `{"file", "error"}` entries a job keeps for the summary; the count
# itself is never capped.
FAILURE_DETAIL_CAP = 50


def failure_summary(failed_count: int, failures: list[dict], *, action: str) -> str | None:
    """One line for the job's `result_data`, at most 300 characters.

    `action` finishes "N file(s) …", e.g. "could not be restored". Names up to
    three files and the first error; `None` when nothing failed.
    """
    if not failed_count:
        return None
    names = ", ".join(f["file"] for f in failures[:3])
    more = failed_count - min(3, len(failures))
    text = f"{failed_count} file(s) {action}: {names}"
    if more > 0:
        text += f" and {more} more"
    if failures:
        text += f" ({failures[0]['error']})"
    return text if len(text) <= 300 else text[:297] + "..."
