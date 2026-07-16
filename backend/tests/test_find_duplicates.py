"""Golden tests for duplicate detection (backend.ml.technical_scorer).

The load-bearing property: `_find_duplicates_indexed` (pigeonhole chunk index)
must return *byte-identical* output to `_find_duplicates_bruteforce` (the
frozen O(N²) reference) — same groups, same roots, same member order, same
group order — for any input and threshold. `duplicate_of` pointers persisted
in the DB depend on the greedy root choice, so ordering is part of the
contract, not an implementation detail.

If a change to the indexed path breaks these tests, the change is wrong even
if it "looks faster" — an undershot candidate radius silently drops duplicate
pairs and nothing at runtime will ever report it.
"""

import numpy as np
import pytest

import backend.ml.technical_scorer as ts
from backend.ml.technical_scorer import (
    MIN_INDEX_N,
    _chunk_plan,
    _find_duplicates_bruteforce,
    _find_duplicates_indexed,
    _probe_masks,
    _probe_volume,
    find_duplicates_sync,
)


def _gen_phashes(rng, n, n_bytes=8, dup_prob=0.3, max_flips=6):
    """Random hex hashes with planted near-duplicate clusters.

    With probability `dup_prob` a row reuses an earlier base hash with up to
    `max_flips` random bit flips (so dedup does real work); otherwise a fresh
    random hash is drawn and becomes a potential cluster base.
    """
    out = []
    base_pool = []
    for i in range(n):
        if base_pool and rng.random() < dup_prob:
            h = base_pool[int(rng.integers(len(base_pool)))].copy()
            for _ in range(int(rng.integers(0, max_flips + 1))):
                bit = int(rng.integers(n_bytes * 8))
                h[bit // 8] ^= 1 << (bit % 8)
        else:
            h = rng.integers(0, 256, size=n_bytes, dtype=np.uint8)
            base_pool.append(h)
        out.append((f"img{i:06d}", bytes(h).hex()))
    return out


def _decode(phashes):
    ids = [id_ for id_, _ in phashes]
    hashes = np.array(
        [np.frombuffer(bytes.fromhex(h), dtype=np.uint8) for _, h in phashes]
    )
    return ids, hashes


def _brute(phashes, threshold):
    if not phashes:
        return []
    ids, hashes = _decode(phashes)
    return _find_duplicates_bruteforce(ids, hashes, threshold)


def _indexed(phashes, threshold):
    """Call the indexed path directly with the production chunk plan
    (shared `_chunk_plan`, so the tested plan can't drift from the dispatcher's)."""
    if not phashes:
        return []
    ids, hashes = _decode(phashes)
    col_groups, radius = _chunk_plan(hashes.shape[1], threshold)
    return _find_duplicates_indexed(ids, hashes, threshold, col_groups, radius)


# ---------------------------------------------------------------------------
# Golden cross-implementation property
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [0, 1])
@pytest.mark.parametrize("threshold", [1, 2, 8, 13, 16])
@pytest.mark.parametrize("n", [0, 1, 2, 50, 3000])
def test_indexed_matches_bruteforce(n, threshold, seed):
    rng = np.random.default_rng(seed)
    phashes = _gen_phashes(rng, n)
    assert _indexed(phashes, threshold) == _brute(phashes, threshold)


@pytest.mark.parametrize("n_bytes", [9, 16, 29, 32])
@pytest.mark.parametrize("threshold", [2, 8])
def test_indexed_matches_bruteforce_non_64_bit(n_bytes, threshold):
    """Length-generality: 72-bit (uneven 3+2+2+2 byte chunks), 128-bit, and
    232/256-bit hashes (8-byte chunks — the unsigned-key-fold regime)."""
    rng = np.random.default_rng(11)
    phashes = _gen_phashes(rng, 400, n_bytes=n_bytes)
    assert _indexed(phashes, threshold) == _brute(phashes, threshold)


def test_indexed_top_bit_of_8_byte_chunk():
    """Regression: a signed int64 key fold wraps keys >= 2^63 negative for
    8-byte chunks (hashes >= 29 bytes), so probes crossing a chunk's bit 63
    could never match the stored key and duplicate pairs were silently dropped.
    The pair below is findable ONLY via chunk 0's bit-63 probe: every other
    chunk differs in more bits than the radius allows."""
    a = bytes(32)
    b = bytearray(32)
    b[0] ^= 0x80   # chunk 0: 1 bit = folded key bit 63 (within radius 1)
    b[8] ^= 0x03   # chunk 1: 2 bits (beyond radius)
    b[16] ^= 0x03  # chunk 2: 2 bits
    b[24] ^= 0x03  # chunk 3: 2 bits -> total Hamming distance 7 < 8
    phashes = [("a", a.hex()), ("b", bytes(b).hex()), ("far", "ff" * 32)]
    assert _indexed(phashes, 8) == _brute(phashes, 8) == [["a", "b"]]


@pytest.mark.parametrize("threshold", [8.0, 8.5])
def test_indexed_matches_bruteforce_float_threshold(threshold):
    """threshold_settings stores a Float; both paths must honor the exact
    strict-< comparison (candidate radius ceil() may only overshoot)."""
    rng = np.random.default_rng(5)
    phashes = _gen_phashes(rng, 600)
    assert _indexed(phashes, threshold) == _brute(phashes, threshold)


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------


def test_dispatcher_output_matches_bruteforce_across_min_index_n():
    rng = np.random.default_rng(42)
    small = _gen_phashes(rng, 100)
    assert find_duplicates_sync(small, 8) == _brute(small, 8)
    large = _gen_phashes(rng, MIN_INDEX_N + 500)
    assert find_duplicates_sync(large, 8) == _brute(large, 8)


def test_dispatcher_routes_large_n_to_indexed(monkeypatch):
    rng = np.random.default_rng(9)
    phashes = _gen_phashes(rng, MIN_INDEX_N)

    def boom(*args, **kwargs):
        raise AssertionError("expected the indexed path, got brute force")

    monkeypatch.setattr(ts, "_find_duplicates_bruteforce", boom)
    find_duplicates_sync(phashes, 8)  # must not raise


@pytest.mark.parametrize(
    ("n", "n_bytes", "threshold"),
    [
        (100, 8, 8),  # below MIN_INDEX_N
        (MIN_INDEX_N + 10, 2, 8),  # hash too short for 4 chunks
        (MIN_INDEX_N + 10, 8, 64),  # extreme threshold: index would probe ~everything
        (MIN_INDEX_N + 10, 8, 17),  # probe volume (4x2517) exceeds n // PROBE_COST_DIVISOR
    ],
)
def test_dispatcher_falls_back_to_bruteforce(monkeypatch, n, n_bytes, threshold):
    rng = np.random.default_rng(7)
    phashes = _gen_phashes(rng, n, n_bytes=n_bytes)

    def boom(*args, **kwargs):
        raise AssertionError("expected the brute-force path, got indexed")

    monkeypatch.setattr(ts, "_find_duplicates_indexed", boom)
    assert find_duplicates_sync(phashes, threshold) == _brute(phashes, threshold)


@pytest.mark.parametrize(("bits", "radius"), [(8, 1), (16, 0), (16, 3), (24, 2), (64, 1)])
def test_probe_volume_matches_mask_enumeration(bits, radius):
    """The dispatcher's cost model (`_probe_volume`, closed form) must agree
    with what `_probe_masks` actually enumerates — a drift silently misroutes."""
    assert len(_probe_masks(bits, radius)) == _probe_volume(bits, radius)


# ---------------------------------------------------------------------------
# Fixed, human-verifiable cases
# ---------------------------------------------------------------------------


def test_empty():
    assert find_duplicates_sync([], 8) == []


def test_single_image():
    assert find_duplicates_sync([("a", "00" * 8)], 8) == []


def test_all_identical():
    phashes = [(f"i{k}", "ab" * 8) for k in range(10)]
    assert find_duplicates_sync(phashes, 8) == [[f"i{k}" for k in range(10)]]


def test_known_groups_root_and_member_order():
    phashes = [
        ("root", "0000000000000000"),
        ("far", "ffffffffffffffff"),
        ("near1", "0000000000000001"),  # dist 1 from root
        ("near2", "0000000000000003"),  # dist 2 from root
    ]
    # First unassigned row is the root; members follow in input order.
    assert find_duplicates_sync(phashes, 8) == [["root", "near1", "near2"]]


def test_threshold_one_groups_only_exact_matches():
    phashes = [
        ("a", "0102030405060708"),
        ("b", "0102030405060709"),  # dist 1 — NOT < 1
        ("c", "0102030405060708"),  # exact copy of a
    ]
    assert find_duplicates_sync(phashes, 1) == [["a", "c"]]
