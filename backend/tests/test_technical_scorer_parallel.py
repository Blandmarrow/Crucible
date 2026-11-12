"""The technical scorer runs several images at once and still answers like the serial loop (M10 T1).

`score_images_technical` keeps up to `_technical_workers()` calls of
`score_technical_sync` in flight on the default pool and awaits them oldest
first. These tests pin what the serial loop guaranteed and the caller in
`routers/quality.py` relies on: the same scores, in input order, a *prefix* of
the input when a cancel cuts the run short, `_unmeasured()` for a failed image,
and progress after images 1, 11, 21, ...

Only the parity test needs OpenCV; the rest swap the per-image function for a
fake, so they run in CI's torch-free environment either way.
"""

import random
import threading
import time

import numpy as np

import backend.ml.technical_scorer as ts
from backend.ml.technical_scorer import score_images_technical, score_technical_sync
from backend.tests.conftest import needs_cv2, run
from backend.workers.job_queue import job_queue


def _write_fixture(tmp_path) -> list[str]:
    """About 30 varied files: sizes, noise, flat colours, JPEG and PNG, one unreadable."""
    from PIL import Image as PilImage

    rng = np.random.default_rng(1234)
    paths: list[str] = []
    for i in range(30):
        if i == 13:
            p = tmp_path / f"{i:02d}-broken.png"
            p.write_bytes(b"not an image")
            paths.append(str(p))
            continue
        w, h = int(rng.integers(24, 400)), int(rng.integers(24, 400))
        kind = i % 3
        if kind == 0:
            arr = rng.integers(0, 256, size=(h, w, 3), dtype=np.uint8)  # noise
        elif kind == 1:
            arr = np.full((h, w, 3), rng.integers(0, 256, size=3), dtype=np.uint8)  # flat
        else:
            ramp = np.linspace(0, 255, w, dtype=np.uint8)
            arr = np.stack([np.tile(ramp, (h, 1))] * 3, axis=-1)  # gradient
        ext = "jpg" if i % 2 else "png"
        p = tmp_path / f"{i:02d}.{ext}"
        PilImage.fromarray(arr).save(p)
        paths.append(str(p))
    return paths


@needs_cv2
def test_scores_match_the_serial_path_exactly(tmp_path, monkeypatch):
    monkeypatch.setattr(ts, "_technical_workers", lambda: 4)
    paths = _write_fixture(tmp_path)
    ids = [f"id-{i}" for i in range(len(paths))]

    expected = [score_technical_sync(p) for p in paths]
    got = run(score_images_technical(ids, paths))

    assert got == expected
    assert got[13] == ts._unmeasured()


def _fake_scorer(log: list[str] | None = None):
    def fake(path, *_thresholds):
        if log is not None:
            log.append(path)
        time.sleep(random.uniform(0, 0.01))
        return {"path": path}
    return fake


def test_results_follow_input_order_when_calls_finish_out_of_order(monkeypatch):
    monkeypatch.setattr(ts, "_technical_workers", lambda: 5)
    monkeypatch.setattr(ts, "score_technical_sync", _fake_scorer())
    paths = [f"p{i}" for i in range(40)]

    got = run(score_images_technical(paths, paths))

    assert [r["path"] for r in got] == paths


def test_calls_in_flight_never_exceed_the_bound(monkeypatch):
    """And reach it — the run is actually parallel, not serial with extra steps."""
    lock = threading.Lock()
    in_flight = [0]
    peak = [0]

    def fake(path, *_thresholds):
        with lock:
            in_flight[0] += 1
            peak[0] = max(peak[0], in_flight[0])
        time.sleep(0.02)
        with lock:
            in_flight[0] -= 1
        return {"path": path}

    monkeypatch.setattr(ts, "_technical_workers", lambda: 3)
    monkeypatch.setattr(ts, "score_technical_sync", fake)
    paths = [f"p{i}" for i in range(12)]

    run(score_images_technical(paths, paths))

    assert peak[0] == 3


def test_a_cancel_returns_a_prefix_and_starts_nothing_new(monkeypatch):
    bound, k = 3, 7
    called: list[str] = []
    checks = [0]

    def cancel_requested(job_id):
        checks[0] += 1
        return checks[0] > k  # the first k submissions go through

    monkeypatch.setattr(ts, "_technical_workers", lambda: bound)
    monkeypatch.setattr(ts, "score_technical_sync", _fake_scorer(called))
    monkeypatch.setattr(job_queue, "cancel_requested", cancel_requested)
    paths = [f"p{i}" for i in range(30)]

    got = run(score_images_technical(paths, paths, job_id="job-1", progress=_noop))

    assert [r["path"] for r in got] == paths[:len(got)]
    assert len(got) == k  # every submitted call finishes and is kept
    assert sorted(called) == sorted(paths[:k])  # nothing started after the cancel


def test_a_failure_mid_batch_is_unmeasured_at_its_own_index_only(monkeypatch):
    def fake(path, *_thresholds):
        if path == "p5":
            raise RuntimeError("decoder exploded")
        return {"path": path}

    monkeypatch.setattr(ts, "_technical_workers", lambda: 4)
    monkeypatch.setattr(ts, "score_technical_sync", fake)
    paths = [f"p{i}" for i in range(10)]

    got = run(score_images_technical(paths, paths))

    assert got[5] == ts._unmeasured()
    assert [r.get("path") for i, r in enumerate(got) if i != 5] == [p for p in paths if p != "p5"]


async def _noop(_n: int) -> None:
    return None


def test_progress_keeps_the_serial_cadence(monkeypatch):
    seen: list[int] = []

    async def progress(n: int) -> None:
        seen.append(n)

    monkeypatch.setattr(ts, "_technical_workers", lambda: 4)
    monkeypatch.setattr(ts, "score_technical_sync", _fake_scorer())
    paths = [f"p{i}" for i in range(25)]

    run(score_images_technical(paths, paths, progress=progress))

    assert seen == [1, 11, 21]


def test_the_bound_is_at_least_one_and_at_most_the_cap(monkeypatch):
    for cores in (None, 1, 2, 3, 8, 64):
        monkeypatch.setattr(ts.os, "cpu_count", lambda c=cores: c)
        assert 1 <= ts._technical_workers() <= ts.TECHNICAL_MAX_WORKERS
