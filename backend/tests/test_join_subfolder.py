"""`join_subfolder` is the non-raising counterpart to `normalize_subfolder`.

The two share `_subfolder_parts`, so they must disagree about exactly one thing —
what `..` means — and about nothing else. The write-time guard rejects it with a
400; the job-side join drops it, because a gallery subfolder is a virtual label
rather than a filesystem path, and failing a background row over a stray `..`
would produce an unactionable error that re-fails on every re-run.
"""
import pytest
from fastapi import HTTPException

from backend.utils import join_subfolder, normalize_subfolder


def test_join_combines_fragments():
    assert join_subfolder("run", "a/b") == "run/a/b"


def test_join_skips_blank_fragments():
    assert join_subfolder("", "a") == "a"
    assert join_subfolder("run", "") == "run"
    assert join_subfolder("", "") == ""


def test_join_normalizes_each_fragment():
    assert join_subfolder("/run/", "//a//b/") == "run/a/b"
    assert join_subfolder("run", "a\\b") == "run/a/b"
    assert join_subfolder("./run", "a/./b") == "run/a/b"


def test_join_drops_dot_dot_instead_of_raising():
    assert join_subfolder("run", "../evil") == "run/evil"
    assert join_subfolder("..", "..") == ""
    assert join_subfolder("run", "a/../b") == "run/a/b"


def test_normalize_still_raises_on_dot_dot():
    """The split must not have softened the write-time guard."""
    with pytest.raises(HTTPException) as exc:
        normalize_subfolder("a/../b")
    assert exc.value.status_code == 400


def test_normalize_still_normalizes():
    assert normalize_subfolder("/a//b/") == "a/b"
    assert normalize_subfolder("a\\b") == "a/b"
    assert normalize_subfolder("") == ""
