import asyncio
import functools
import logging
from pathlib import Path

import cv2
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


def find_duplicates_sync(phashes: list[tuple[str, str]], duplicate_threshold: int = DUPLICATE_THRESHOLD) -> list[list[str]]:
    """Group image IDs by near-identical phash (Hamming distance < threshold).

    Vectorized: hex hashes are decoded once into an (N, L) uint8 array and each
    row's Hamming distance to all later rows is computed in one numpy op via a
    popcount lookup table. Greedy grouping semantics match the original scalar
    loop exactly — the first unassigned row becomes a group root and each member
    is claimed once.
    """
    n = len(phashes)
    if n == 0:
        return []

    ids = [id_ for id_, _ in phashes]
    # Decode all hex hashes once. Length-generic (no assumption of 64-bit phash).
    hashes = np.array(
        [np.frombuffer(bytes.fromhex(h), dtype=np.uint8) for _, h in phashes]
    )  # (N, L)

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
