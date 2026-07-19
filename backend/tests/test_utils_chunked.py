"""Tests for backend.utils.chunked — id-list chunking for bounded SQL IN()."""

import pytest

from backend.utils import chunked


def test_empty_yields_nothing():
    assert list(chunked([], 10)) == []


def test_exact_multiple():
    assert [list(c) for c in chunked([1, 2, 3, 4], 2)] == [[1, 2], [3, 4]]


def test_remainder():
    assert [list(c) for c in chunked([1, 2, 3, 4, 5], 2)] == [[1, 2], [3, 4], [5]]


def test_single_chunk_when_size_exceeds_len():
    assert [list(c) for c in chunked([1, 2, 3], 100)] == [[1, 2, 3]]


def test_default_size_is_large():
    # Under the default 10k, a small list is one chunk.
    assert [list(c) for c in chunked(list(range(5)))] == [[0, 1, 2, 3, 4]]


def test_nonpositive_size_raises():
    with pytest.raises(ValueError):
        list(chunked([1, 2, 3], 0))
