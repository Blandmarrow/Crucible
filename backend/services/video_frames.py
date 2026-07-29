"""Frame heuristics for video extraction: pure numpy, no I/O and no decoder.

Everything here takes a decoded BGR `ndarray` (or a series of numbers derived
from one) and returns a number, a rect or a verdict. That is the whole point of
the split: `video_extract.py` owns cv2, scenedetect and ffmpeg and needs real
video fixtures to exercise at all, while every judgement call — where the black
bars are, whether the source is interlaced, which of five candidate frames to
keep — lives here and is testable in milliseconds against synthetic arrays.
Merging the two would make the fast tests hostage to the slow ones, and these
heuristics are the part most likely to need tuning. Every rule below is written
up in `docs/dev/video-heuristics.md`.

Coordinate convention: a crop rect is `(x, y, w, h)`, matching the
`Video.crop_x/crop_y/crop_w/crop_h` column order. Note that PySceneDetect's
`SceneManager.crop` is *inclusive corners* `(x0, y0, x1, y1)` instead —
`video_extract` converts at that boundary, not here.
"""

from __future__ import annotations

import math

import numpy as np

CropRect = tuple[int, int, int, int]  # x, y, w, h — Video.crop_* order

# --- Cropdetect ------------------------------------------------------------
# Luma level below which a row/column counts as "bar, not content". 16 is the
# studio-swing black floor; real letterbox bars sit at or just above it.
CROP_LUMA_THRESHOLD = 16
# A bar smaller than this fraction of the axis is encoding noise, not a bar.
CROP_MIN_BAR_FRAC = 0.015
# Below this, the "content" rect is not content — it is a fade or a dark shot.
CROP_MIN_CONTENT_FRAC = 0.5
# Percentile, not max: one hot pixel or a stuck channel inside a black bar
# otherwise pins that whole row as content and defeats the detection entirely.
CROP_PERCENTILE = 95

# --- Interlace / telecine --------------------------------------------------
# d1/d2 above this is field mismatch rather than ordinary vertical detail.
# Progressive material sits ~0.5–0.65; interlaced-with-motion exceeds ~0.9.
COMBING_THRESHOLD = 0.9
# Same-parity row difference below this means the two fields are identical,
# i.e. there is no second field for the first to disagree with. Only a
# synthetic pattern reaches it, and reaching it must read as "no evidence"
# rather than as an infinite ratio.
COMBING_D2_FLOOR = 1.0
# One combed sample is a pinstripe shirt or a picket fence. Two is a source.
COMBING_MIN_SAMPLES = 2
TELECINE_LAG = 5
TELECINE_MIN_AUTOCORR = 0.6
TELECINE_DUTY_RANGE = (0.3, 0.5)
# Derived, not chosen. The autocorrelation below is the *biased* estimator: the
# numerator sums n-LAG products against a denominator over all n, so a perfect
# 3:2 series scores about (n-LAG)/n and cannot clear the threshold until
# n >= LAG/(1-MIN_AUTOCORR) = 13. At the old 10, a run ending early between 10
# and 12 frames was admitted and then provably undetectable. Measured, worst
# phase: n=12 -> 0.588, n=13 -> 0.551..0.623 (4 of 5 phases clear), n=14 -> all
# five, n=20 (PROBE_TELECINE_RUN) -> 0.75.
#
# The biased estimator is kept on purpose. The normalized form returns exactly
# 1.0 for a perfect 3:2 series at every n >= 9 — it discards the length
# information this threshold was tuned against. Measured false-positive rate on
# shuffled series inside the duty gate: 0.0003 -> 0.0035 at n=20 (11.7x), and a
# single-glitch run at n=20 goes 0.553 (rejected) -> 0.732 (accepted).
TELECINE_MIN_SAMPLES = math.ceil(TELECINE_LAG / (1 - TELECINE_MIN_AUTOCORR))  # 13

# --- Candidate rejection ---------------------------------------------------
DEGENERATE_LUMA_MIN = 8.0    # black frames, fades, leader
DEGENERATE_LUMA_MAX = 247.0  # white flashes, blown slates
DEGENERATE_STD_MIN = 3.0     # flat colour cards
# A candidate this far off the window's median brightness came from the other
# side of a cut the detector missed, and filing it under this shot's index
# would put a frame from the next scene in this shot's folder.
LUMA_OUTLIER_FRAC = 0.4

SHARPNESS_LONG_EDGE = 512

# Rec.601 in cv2's channel order: B, G, R. Module-level so the einsum below
# allocates nothing but its output.
_LUMA_WEIGHTS = np.array([0.114, 0.587, 0.299], dtype=np.float32)


def luma(frame: np.ndarray) -> np.ndarray:
    """Rec.601 luma as float32. Input is BGR, cv2's channel order.

    Already a 2-D plane — one this function returned — is handed straight back
    without a copy, which is what lets a caller compute the plane **once** and
    pass it to `edge_profiles`, `combing_ratio`, `sharpness` and `is_degenerate`
    in place of the frame. That is only safe because no consumer in this module
    mutates the plane: treat the result as read-only.

    The `einsum` form rather than `0.114*b + 0.587*g + 0.299*r` is about the
    transient, not the arithmetic — it sums in the same order and is bit-identical
    on a real frame, but the three-term expression materialises three full-size
    float32 temporaries. At 4K that is 166 MB peak against 33 MB, and half the
    wall clock.
    """
    a = np.asarray(frame)
    if a.ndim == 2:
        return a.astype(np.float32, copy=False)
    if a.ndim != 3 or a.shape[2] < 3:
        raise ValueError(f"expected an HxWx3 BGR frame, got shape {a.shape}")
    # `[:, :, :3]` keeps the BGRA tolerance the `>= 3` check above grants.
    return np.einsum("ijk,k->ij", a[:, :, :3], _LUMA_WEIGHTS, dtype=np.float32)


# ---------------------------------------------------------------------------
# Cropdetect
# ---------------------------------------------------------------------------


def edge_profiles(frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-row and per-column brightness profiles for one frame.

    `frame` is a BGR frame, or a plane already returned by `luma()` — pass the
    plane when more than one of these runs on the same frame.

    Returns `(rows, cols)` — `rows[i]` is the 95th-percentile luma of row `i`,
    `cols[j]` the same for column `j`. The percentile rather than the max is
    load-bearing: a single hot pixel, a stuck chroma sample or a channel-order
    bug inside an otherwise black bar pins that row above the threshold, and one
    such pixel per bar is enough to detect no crop at all on real files.
    """
    lum = luma(frame)
    rows = np.percentile(lum, CROP_PERCENTILE, axis=1).astype(np.float32)
    cols = np.percentile(lum, CROP_PERCENTILE, axis=0).astype(np.float32)
    return rows, cols


def merge_profiles(acc: np.ndarray | None, new: np.ndarray) -> np.ndarray:
    """Accumulate profiles across samples by elementwise max.

    Max, not mean: a dark shot in the sample set can then only *grow* the
    content rect, never shrink it. Averaging would let one night scene pull the
    profile below the threshold and crop away real picture in every other shot.
    """
    new = np.asarray(new, dtype=np.float32)
    if acc is None:
        return new.copy()
    if acc.shape != new.shape:
        raise ValueError(f"profile shape changed mid-run: {acc.shape} vs {new.shape}")
    return np.maximum(acc, new)


def _span(profile: np.ndarray, threshold: float) -> tuple[int, int] | None:
    """First and last index whose value exceeds `threshold`, or None."""
    lit = np.flatnonzero(profile > threshold)
    if lit.size == 0:
        return None
    return int(lit[0]), int(lit[-1])


def _even_down(v: int) -> int:
    return v & ~1


def crop_rect_from_profiles(
    rows: np.ndarray,
    cols: np.ndarray,
    *,
    threshold: float = CROP_LUMA_THRESHOLD,
    min_bar_frac: float = CROP_MIN_BAR_FRAC,
) -> CropRect | None:
    """Derive a crop rect from accumulated edge profiles, or None for no crop.

    None is returned — deliberately, and in preference to a rect — whenever the
    evidence is weak rather than absent:

    - nothing at all exceeds the threshold (the samples were all fade-to-black,
      and cropping to nothing is worse than not cropping);
    - the surviving content is under half of either axis, same reasoning;
    - the bars are thinner than `min_bar_frac`, i.e. encoding noise at the frame
      edge rather than a real matte.

    All four edges are snapped to even coordinates. Chroma subsampling wants it,
    and `bwdif` needs an even `y` or it deinterlaces with the field parity
    inverted, which looks worse than not deinterlacing at all.
    """
    rows = np.asarray(rows, dtype=np.float32)
    cols = np.asarray(cols, dtype=np.float32)
    height, width = int(rows.size), int(cols.size)
    if height <= 0 or width <= 0:
        return None

    v_span = _span(rows, threshold)
    h_span = _span(cols, threshold)
    if v_span is None or h_span is None:
        return None  # every sample was black — a fade, not a matte

    top, bottom = v_span
    left, right = h_span
    content_h = bottom - top + 1
    content_w = right - left + 1
    if content_h < CROP_MIN_CONTENT_FRAC * height or content_w < CROP_MIN_CONTENT_FRAC * width:
        return None

    # Each axis decides independently: a pillarboxed 4:3 insert in a 16:9 frame
    # has real vertical bars and no horizontal ones.
    if (top + (height - 1 - bottom)) < min_bar_frac * height:
        top, bottom = 0, height - 1
    if (left + (width - 1 - right)) < min_bar_frac * width:
        left, right = 0, width - 1

    x = _even_down(left)
    y = _even_down(top)
    w = _even_down(right - x + 1)
    h = _even_down(bottom - y + 1)
    if w <= 0 or h <= 0:
        return None
    if x == 0 and y == 0 and w >= _even_down(width) and h >= _even_down(height):
        return None  # a no-op crop is not a crop
    return x, y, w, h


def clamp_crop(rect: CropRect | None, width: int, height: int) -> CropRect | None:
    """Clamp a rect into a `width`×`height` frame, or None if nothing survives.

    Called twice on the way to a pixel: once against the *header* dimensions
    before the rect reaches scenedetect (which raises on an out-of-range crop),
    and once per frame against `frame.shape`, which is the real authority —
    headers lie, and container rotation swaps the axes.
    """
    if rect is None or width <= 0 or height <= 0:
        return None
    x, y, w, h = (int(v) for v in rect)
    x = max(0, min(x, width))
    y = max(0, min(y, height))
    w = min(w, width - x)
    h = min(h, height - y)
    x, y, w, h = _even_down(x), _even_down(y), _even_down(w), _even_down(h)
    if w <= 0 or h <= 0:
        return None
    if x == 0 and y == 0 and w >= _even_down(width) and h >= _even_down(height):
        return None
    return x, y, w, h


# ---------------------------------------------------------------------------
# Interlace and telecine
# ---------------------------------------------------------------------------


def combing_ratio(frame: np.ndarray) -> float:
    """How much more adjacent rows differ than same-parity rows do.

    `frame` is a BGR frame, or a plane already returned by `luma()` — pass the
    plane when more than one of these runs on the same frame.

    `d1 = mean|L[1:] − L[:-1]|` compares neighbouring rows, which in interlaced
    material come from two different fields captured 1/50 s apart; `d2 =
    mean|L[2:] − L[:-2]|` compares rows of the *same* parity, i.e. the same
    field, and so measures ordinary vertical detail. Progressive material sits
    around 0.5–0.65; interlaced material with motion in it exceeds ~0.9.

    Returns 0.0 when `d2` is essentially zero. That is not a divide-by-zero
    guard bolted on: identical same-parity rows mean there is no second field
    for the first to disagree with, so a synthetic 1-pixel stripe pattern —
    which otherwise produces an unbounded ratio and a confident false positive —
    correctly reads as no evidence. Real sources with fine horizontal texture
    still need the two-sample rule in `interlace_from_series`.
    """
    lum = luma(frame)
    if lum.shape[0] < 3:
        return 0.0
    d1 = float(np.abs(lum[1:] - lum[:-1]).mean())
    d2 = float(np.abs(lum[2:] - lum[:-2]).mean())
    if d2 < COMBING_D2_FLOOR:
        return 0.0
    return d1 / d2


def interlace_from_series(
    ratios: list[float],
    *,
    height: int | None = None,
    fps: float | None = None,
) -> tuple[bool, str | None]:
    """Decide interlacing from per-sample combing ratios. Returns (flag, note).

    The decision is: **at least two** samples over `COMBING_THRESHOLD`. Two,
    because a single combed sample is far more often a pinstripe shirt or a
    picket fence than a source; and a count rather than a mean, because a static
    interlaced shot has no field mismatch at all and would drag a mean down
    below the threshold on an otherwise obviously interlaced file.

    `height` and `fps` corroborate in the **note text only**, never in the
    decision. SD line counts with broadcast frame rates make interlacing more
    likely, but a 1080p25 file is not interlaced for being 1080p25, and folding
    that into the verdict would flag a large class of progressive material.
    """
    over = sum(1 for r in ratios if r >= COMBING_THRESHOLD)
    flag = over >= COMBING_MIN_SAMPLES
    if not flag:
        return False, None

    note = f"Interlacing detected ({over}/{len(ratios)} samples show field mismatch)"
    broadcast_height = height in (480, 486, 576, 1080)
    broadcast_fps = fps is not None and any(abs(fps - f) < 0.2 for f in (25.0, 29.97, 30.0))
    if broadcast_height and broadcast_fps:
        note += f"; {height}p/{fps:.2f} is a broadcast format, which is consistent with this"
    return True, note


def telecine_from_series(
    combing: list[float],
    *,
    fps: float | None = None,
) -> tuple[bool, str | None]:
    """Detect 3:2 pulldown from a run of *consecutive*-frame combing ratios.

    Unlike `interlace_from_series`, which reads scattered samples, this needs
    frames in sequence: telecine is a period-5 pattern (two combed frames, three
    clean ones) and is invisible to samples taken seconds apart.

    Gated on ~29.97 fps, where pulldown lives. Binarize, then require the duty
    cycle to sit in `TELECINE_DUTY_RANGE` and the lag-5 autocorrelation to clear
    `TELECINE_MIN_AUTOCORR`. An all-combed run has zero variance and so zero
    autocorrelation, which is the correct answer — that is plain interlace, not
    telecine, and shipping it as telecine would recommend the wrong fix.

    Drives a **warning string only**. `bwdif` is the one filter this phase
    ships; inverse telecine is detect-and-tell-the-user.
    """
    if fps is None or not (abs(fps - 29.97) < 0.2 or abs(fps - 30.0) < 0.2):
        return False, None
    if len(combing) < TELECINE_MIN_SAMPLES:
        return False, None

    x = np.array([1.0 if c >= COMBING_THRESHOLD else 0.0 for c in combing], dtype=np.float64)
    duty = float(x.mean())
    lo, hi = TELECINE_DUTY_RANGE
    if not (lo <= duty <= hi):
        return False, None

    y = x - duty
    denom = float((y * y).sum())
    if denom <= 0:
        return False, None  # constant series: all combed or none — not a pattern
    r = float((y[TELECINE_LAG:] * y[:-TELECINE_LAG]).sum() / denom)
    if r < TELECINE_MIN_AUTOCORR:
        return False, None
    return True, (
        "3:2 pulldown (telecine) detected — this source is film shot at 24 fps. "
        "Deinterlacing will work but an inverse-telecine pass would be cleaner; "
        "this build ships bwdif only."
    )


# ---------------------------------------------------------------------------
# Candidate scoring and selection
# ---------------------------------------------------------------------------


def _box_downscale(lum: np.ndarray, long_edge: int) -> np.ndarray:
    """Integer-factor box-average downscale, so the long edge is ≤ `long_edge`.

    A box average rather than nearest, and integer-factor rather than a resample
    to exactly `long_edge`: this module holds no decoder and no image library,
    and the averaging is the part that matters — it is what suppresses the
    per-pixel noise the Laplacian would otherwise score as detail.
    """
    h, w = lum.shape[:2]
    longest = max(h, w)
    if longest <= long_edge:
        return lum
    factor = int(np.ceil(longest / long_edge))
    hh, ww = (h // factor) * factor, (w // factor) * factor
    if hh < factor or ww < factor:
        return lum
    return lum[:hh, :ww].reshape(hh // factor, factor, ww // factor, factor).mean(axis=(1, 3))


def sharpness(frame: np.ndarray, *, long_edge: int = SHARPNESS_LONG_EDGE) -> float:
    """Laplacian variance of the luma plane, measured at a fixed resolution.

    `frame` is a BGR frame, or a plane already returned by `luma()` — pass the
    plane when more than one of these runs on the same frame.

    **The downscale is a correctness fix, not an optimization.** Raw Laplacian
    variance on a full-resolution frame ranks *noise* as sharpness: a grainy or
    heavily-compressed candidate scores above a crisp one, which is precisely
    backwards for a "pick the sharpest frame" policy. Averaging into a fixed
    grid first removes the per-pixel component and leaves real edges. It also
    makes scores comparable between a 4K source and a 480p one, which matters
    the moment anything compares across shots. Deleting this line must fail a
    test — `test_video_frames.py` pins it with pure Gaussian noise.
    """
    lum = _box_downscale(luma(frame), long_edge)
    if lum.shape[0] < 3 or lum.shape[1] < 3:
        return 0.0
    lap = (
        4.0 * lum[1:-1, 1:-1]
        - lum[:-2, 1:-1]
        - lum[2:, 1:-1]
        - lum[1:-1, :-2]
        - lum[1:-1, 2:]
    )
    return float(lap.var())


def is_degenerate(frame: np.ndarray) -> bool:
    """True for a frame not worth keeping regardless of how sharp it is.

    `frame` is a BGR frame, or a plane already returned by `luma()` — pass the
    plane when more than one of these runs on the same frame.

    Black and white flashes, fades, leader and flat-colour slates. A slate is
    often the *sharpest* thing in a shot — hard-edged text on a flat field — so
    without this the sharpest-in-window policy actively prefers it.
    """
    lum = luma(frame)
    mean = float(lum.mean())
    if mean < DEGENERATE_LUMA_MIN or mean > DEGENERATE_LUMA_MAX:
        return True
    return float(lum.std()) < DEGENERATE_STD_MIN


def pick_index(
    sharpness_scores: list[float],
    mean_lumas: list[float],
    policy: str = "sharpest",
    *,
    rejected: list[bool] | None = None,
) -> int:
    """Choose one candidate index from a shot's sampling window.

    Rejection runs **before** ranking, in two passes:

    1. anything `is_degenerate` already flagged (passed in as `rejected`), plus
       a luma bound applied here so the function is usable without it;
    2. any candidate whose brightness deviates by more than
       `LUMA_OUTLIER_FRAC` from the candidate set's *median*. That one is not
       about picture quality — it means the detector missed a cut inside this
       window, and keeping the outlier would file a frame from the next scene
       under this shot's index and its subfolder.

    If everything is rejected the middle candidate is returned rather than
    nothing: a shot that is entirely a fade still owes the caller one frame, and
    a caller that has to handle "no pick" grows a second, untested branch.
    """
    n = len(sharpness_scores)
    if n == 0:
        raise ValueError("pick_index needs at least one candidate")
    if len(mean_lumas) != n:
        raise ValueError("sharpness_scores and mean_lumas must be the same length")
    middle = n // 2

    eligible = [
        i for i in range(n)
        if not (rejected is not None and i < len(rejected) and rejected[i])
        and DEGENERATE_LUMA_MIN <= mean_lumas[i] <= DEGENERATE_LUMA_MAX
    ]
    if eligible:
        # No `if median > 0` guard: `eligible` only admits lumas at or above
        # DEGENERATE_LUMA_MIN (8.0), so the median of a non-empty set cannot be
        # zero and the guard was unreachable. Its dead branch also read as though
        # a zero median were a real case worth skipping the filter for.
        median = float(np.median([mean_lumas[i] for i in eligible]))
        kept = [i for i in eligible if abs(mean_lumas[i] - median) <= LUMA_OUTLIER_FRAC * median]
        if kept:
            eligible = kept
    if not eligible:
        return middle

    if policy == "middle":
        # Still the middle, but of what survived — a fade at the centre of the
        # window must not win just because it is central.
        return min(eligible, key=lambda i: (abs(i - middle), i))
    # "sharpest" (the default). Ties go to the earlier index, which is the
    # earlier timestamp, so the pick is deterministic across runs.
    return max(eligible, key=lambda i: (sharpness_scores[i], -i))
