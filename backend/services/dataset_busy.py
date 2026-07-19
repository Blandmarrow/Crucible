"""In-process dataset busy flag.

Versioning jobs (restore, checkout, snapshot, branch-init, prune) rewrite dataset
files and rows wholesale; an interactive edit landing mid-job would race them.
Background jobs are already serialized by the single job queue — only the direct
mutation endpoints need a guard, and the app is single-process, so a module-level
dict is sufficient (no cross-process concern).

Usage: the versioning job wrapper runs inside ``with busy(dataset_id, reason):``;
interactive mutating endpoints call ``ensure_not_busy(dataset_id)`` (HTTP 409)
right after resolving the dataset id, before any DB or file write.
"""
from fastapi import HTTPException

_busy: dict[str, str] = {}


class busy:
    """Mark a dataset busy for the duration of the block.

    Supports both ``with busy(...)`` and ``async with busy(...)`` — setting and
    clearing the flag is pure sync work (no awaiting), so the async protocol just
    delegates to the sync one. The versioning job wrappers use ``async with`` (the
    surrounding session is async); tests use the plain ``with`` form.
    """

    def __init__(self, dataset_id: str, reason: str):
        self._dataset_id = dataset_id
        self._reason = reason

    def __enter__(self):
        _busy[self._dataset_id] = self._reason
        return self

    def __exit__(self, *exc):
        _busy.pop(self._dataset_id, None)
        return False

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, *exc):
        return self.__exit__(*exc)


def ensure_not_busy(dataset_id: str) -> None:
    reason = _busy.get(dataset_id)
    if reason is not None:
        raise HTTPException(
            409, f"Dataset is busy ({reason}). Try again when the job finishes."
        )
