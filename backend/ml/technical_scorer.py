import asyncio
import functools
import itertools
import logging
import math
import time
from pathlib import Path

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

BLUR_THRESHOLD = 100.0   # Laplacian variance below this = blurry
NOISE_THRESHOLD = 15.0   # noise score above this = noisy
DUPLICATE_THRESHOLD = 8  # Hamming distance threshold
UNIFORMITY_THRESHOLD = 12.0  # grayscale std dev below this = near-uniform

# 256-entry popcount lookup: POPCNT[b] = number of set bits in byte value b.
POPCNT = np.array([bin(x).count("1") for x in range(256)], dtype=np.uint8)


def score_technical_sync(
    image_path: str,
    blur_threshold: float = BLUR_THRESHOLD,
    noise_threshold: float = NOISE_THRESHOLD,
    uniformity_threshold: float = UNIFORMITY_THRESHOLD,
) -> dict:
    # Lazy: cv2 is only needed for scoring, and importing it at module top
    # forces every numpy-only consumer (dedup, bench harness, tests) to have
    # OpenCV installed (it fails on missing libGL in headless environments).
    # Same pattern as the lazy cv2 import in sam2_predictor.py.
    import cv2

    img_cv = cv2.imread(image_path)
    if img_cv is None:
        return {
            "blur_score": 0.0, "noise_score": 0.0, "is_blurry": True, "is_noisy": False,
            "uniformity_score": 0.0, "is_uniform": True,
            "color_score": 0.0, "saturation_score": 0.0,
        }

    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)

    # Blur detection via Laplacian variance
    blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    # Noise estimation via difference from Gaussian-smoothed version
    smoothed = cv2.GaussianBlur(gray.astype(np.float32), (5, 5), 0)
    noise_score = float(np.std(gray.astype(np.float32) - smoothed))

    # Near-uniform detection
    uniformity_score = float(np.std(gray.astype(np.float32)))

    # Color richness — Hasler & Süsstrunk 2003
    img_f = img_cv.astype(np.float32)
    R, G, B = img_f[:, :, 2], img_f[:, :, 1], img_f[:, :, 0]  # OpenCV is BGR
    rg = R - G
    yb = 0.5 * (R + G) - B
    color_score = float(
        np.sqrt(np.std(rg) ** 2 + np.std(yb) ** 2)
        + 0.3 * np.sqrt(np.mean(rg) ** 2 + np.mean(yb) ** 2)
    )

    # Saturation — mean HSV S channel
    hsv = cv2.cvtColor(img_cv, cv2.COLOR_BGR2HSV)
    saturation_score = float(np.mean(hsv[:, :, 1]) / 255.0)

    return {
        "blur_score": round(blur_score, 3),
        "noise_score": round(noise_score, 3),
        "is_blurry": blur_score < blur_threshold,
        "is_noisy": noise_score > noise_threshold,
        "uniformity_score": round(uniformity_score, 3),
        "is_uniform": uniformity_score < uniformity_threshold,
        "color_score": round(color_score, 3),
        "saturation_score": round(saturation_score, 4),
    }


async def score_images_technical(
    image_ids: list[str],
    image_paths: list[str],
    job_id: str | None = None,
    blur_threshold: float = BLUR_THRESHOLD,
    noise_threshold: float = NOISE_THRESHOLD,
    uniformity_threshold: float = UNIFORMITY_THRESHOLD,
) -> list[dict]:
    from backend.workers.progress import broadcaster
    from backend.workers.job_queue import job_queue

    loop = asyncio.get_event_loop()
    results = []
    total = len(image_paths)

    for i, path in enumerate(image_paths):
        if job_id and job_queue.cancel_requested(job_id):
            break
        try:
            fn = functools.partial(score_technical_sync, path, blur_threshold, noise_threshold, uniformity_threshold)
            scores = await loop.run_in_executor(None, fn)
        except Exception:
            scores = {
                "blur_score": 0.0, "noise_score": 0.0, "is_blurry": False, "is_noisy": False,
                "uniformity_score": 0.0, "is_uniform": False,
                "color_score": 0.0, "saturation_score": 0.0,
            }
        results.append(scores)

        if job_id and i % 10 == 0:
            await broadcaster.emit(job_id, {
                "type": "progress", "job_id": job_id, "job_type": "quality_score",
                "status": "running", "done": i + 1, "total": total,
                "percent": round((i + 1) / total * 100, 1),
                "message": f"Technical scoring {i + 1}/{total}",
            })

    return results


# Dedup dispatcher tuning. Below MIN_INDEX_N the brute-force scan is already
# instant, so building the chunk index isn't worth it. The fraction cutoff
# routes extreme thresholds to brute force: once probing is expected to touch
# more than this share of all rows per query, the index costs more than it saves.
# The probe-cost divisor bounds the *number* of probes per root, which the
# fraction cutoff does not: each probe is a pure-Python dict lookup (~45 ns)
# while a brute-force row costs ~15-25 ns of vectorized scan, so the index only
# wins when total probe volume is well under N (measured: 16-byte hashes at
# threshold 20 pass the fraction cutoff yet run 144x slower indexed at N=2048).
# It also keeps _probe_masks enumeration bounded — without it, wide chunks at
# high thresholds would materialize billions of masks (OOM) while the fraction
# stays microscopic.
MIN_INDEX_N = 2048
CANDIDATE_FRACTION_CUTOFF = 0.25
PROBE_COST_DIVISOR = 8
_NUM_CHUNKS = 4


def _chunk_plan(l_bytes: int, duplicate_threshold: float) -> tuple[list[np.ndarray], int]:
    """Chunk split and probe radius for the indexed path.

    Single source of truth — the golden tests derive their plan from here, so
    the plan they pin can never drift from the one the dispatcher runs.
    `d` is the max distance that can satisfy the strict `dist < threshold`
    comparison; ceil() keeps float thresholds safe: the candidate radius may
    only ever overshoot (harmless — verification is exact), never undershoot.
    """
    d = max(0, math.ceil(duplicate_threshold) - 1)
    return np.array_split(np.arange(l_bytes), _NUM_CHUNKS), d // _NUM_CHUNKS


def _probe_masks(bits: int, radius: int) -> list[int]:
    """All XOR masks with at most `radius` bits set within a `bits`-wide chunk."""
    masks = [0]
    for k in range(1, radius + 1):
        for positions in itertools.combinations(range(bits), k):
            m = 0
            for p in positions:
                m |= 1 << p
            masks.append(m)
    return masks


def _probe_volume(bits: int, radius: int) -> int:
    """len(_probe_masks(bits, radius)) without enumerating: sum of C(bits, k)."""
    return sum(math.comb(bits, k) for k in range(radius + 1))


def _find_duplicates_bruteforce(
    ids: list[str], hashes: np.ndarray, duplicate_threshold: float
) -> list[list[str]]:
    """All-pairs scan: each row's Hamming distance to all later rows in one
    numpy op via the POPCNT lookup table. O(N²) — reference implementation and
    fallback for small N / extreme thresholds; must stay semantically frozen
    (the golden test compares the indexed path against this one).
    """
    n = hashes.shape[0]
    groups: list[list[str]] = []
    assigned = np.zeros(n, dtype=bool)

    for i in range(n):
        if assigned[i]:
            continue
        # Hamming distance from row i to every later row, in one vectorized op.
        rest = hashes[i + 1:]
        group = [ids[i]]
        if rest.shape[0]:
            dists = POPCNT[hashes[i] ^ rest].sum(axis=1)  # (N-i-1,)
            close = (dists < duplicate_threshold) & ~assigned[i + 1:]
            for offset in np.nonzero(close)[0]:
                j = i + 1 + int(offset)
                group.append(ids[j])
                assigned[j] = True
        if len(group) > 1:
            assigned[i] = True
            groups.append(group)

    return groups


def _find_duplicates_indexed(
    ids: list[str],
    hashes: np.ndarray,
    duplicate_threshold: float,
    col_groups: list[np.ndarray],
    radius: int,
) -> list[list[str]]:
    """Multi-index chunk search: same output as the brute-force scan, ~linear time.

    Correctness rests on the pigeonhole principle: split each hash into m=4
    chunks; if two hashes differ in at most d bits total, at least one chunk
    pair differs in at most floor(d/4) bits (the differing bits cannot avoid
    being spread thin across all four chunks). So every true neighbor of row i
    shares at least one chunk value with i up to `radius` bit flips, and
    probing each chunk table with the chunk value XOR every mask of <= radius
    bits is guaranteed to surface it as a candidate. Candidates are then
    verified with the exact `dist < duplicate_threshold` comparison, so the
    result is identical to brute force — never approximate. Do not "simplify"
    the chunk count or radius derivation without re-deriving this guarantee;
    an undershoot silently drops duplicate pairs.
    """
    n = hashes.shape[0]

    # One table per chunk: fold the chunk's byte columns into an int key and
    # bucket row indices by key.
    keys_per_table: list[list[int]] = []
    tables: list[dict[int, list[int]]] = []
    masks_per_table: list[list[int]] = []
    for cols in col_groups:
        # Unsigned fold is load-bearing: with int64, an 8-byte chunk wraps keys
        # >= 2^63 negative after .tolist(), and probing `k ^ mask` with the
        # (positive) bit-63 mask then never equals the stored key — silently
        # dropping duplicate pairs. uint64 keys stay non-negative, so build and
        # probe agree for chunks up to 8 bytes; wider chunks wrap mod 2^64
        # identically on both sides (lossy keys only add candidates, which the
        # exact verification below filters out).
        keys = hashes[:, cols[0]].astype(np.uint64)
        for c in cols[1:]:
            keys = (keys << 8) | hashes[:, c]
        key_list = keys.tolist()
        table: dict[int, list[int]] = {}
        for i, k in enumerate(key_list):
            table.setdefault(k, []).append(i)
        keys_per_table.append(key_list)
        tables.append(table)
        masks_per_table.append(_probe_masks(8 * len(cols), radius))

    groups: list[list[str]] = []
    assigned = np.zeros(n, dtype=bool)

    for i in range(n):
        if assigned[i]:
            continue
        cand: set[int] = set()
        for keys, table, masks in zip(keys_per_table, tables, masks_per_table):
            k = keys[i]
            for m in masks:
                bucket = table.get(k ^ m)
                if bucket:
                    cand.update(bucket)
        group = [ids[i]]
        if cand:
            arr = np.fromiter(cand, dtype=np.int64, count=len(cand))
            arr = arr[(arr > i) & ~assigned[arr]]
            if arr.size:
                arr.sort()  # ascending j = brute-force member order
                dists = POPCNT[hashes[i] ^ hashes[arr]].sum(axis=1)
                close = arr[dists < duplicate_threshold]
                for j in close.tolist():
                    group.append(ids[j])
                    assigned[j] = True
        if len(group) > 1:
            assigned[i] = True
            groups.append(group)

    return groups


def find_duplicates_sync(phashes: list[tuple[str, str]], duplicate_threshold: int = DUPLICATE_THRESHOLD) -> list[list[str]]:
    """Group image IDs by near-identical phash (Hamming distance < threshold).

    Greedy grouping semantics (identical across both implementations): rows are
    visited in input order; the first unassigned row becomes a group root and
    claims every later unassigned row within the threshold, members in ascending
    input order; each member is claimed once.

    Dispatches between two exact, output-identical paths — only speed differs:
    - `_find_duplicates_indexed` (default at scale): pigeonhole chunk index,
      ~linear in N for practical thresholds.
    - `_find_duplicates_bruteforce`: O(N²) all-pairs scan, used for small N,
      hashes too short to chunk, thresholds so large the index would probe
      more than CANDIDATE_FRACTION_CUTOFF of all rows per query, or when the
      total probe volume isn't well under N (see PROBE_COST_DIVISOR).
    Length-generic (no assumption of 64-bit phash).
    """
    n = len(phashes)
    if n == 0:
        return []

    ids = [id_ for id_, _ in phashes]
    # Decode all hex hashes once.
    hashes = np.array(
        [np.frombuffer(bytes.fromhex(h), dtype=np.uint8) for _, h in phashes]
    )  # (N, L)

    start = time.monotonic()
    l_bytes = hashes.shape[1]
    use_index = False
    if n >= MIN_INDEX_N and l_bytes >= _NUM_CHUNKS:
        col_groups, radius = _chunk_plan(l_bytes, duplicate_threshold)
        volumes = [_probe_volume(8 * len(g), radius) for g in col_groups]
        fraction = sum(v / (1 << (8 * len(g))) for v, g in zip(volumes, col_groups))
        use_index = (
            fraction <= CANDIDATE_FRACTION_CUTOFF
            and sum(volumes) <= n // PROBE_COST_DIVISOR
        )

    if use_index:
        groups = _find_duplicates_indexed(ids, hashes, duplicate_threshold, col_groups, radius)
    else:
        groups = _find_duplicates_bruteforce(ids, hashes, duplicate_threshold)
    logger.info(
        "Dedup (%s): n=%d, threshold=%s -> %d groups in %.2fs",
        "indexed" if use_index else "bruteforce",
        n, duplicate_threshold, len(groups), time.monotonic() - start,
    )
    return groups
