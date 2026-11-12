"""The GPU scorers prepare images ahead and still answer like the serial loops (M10 T5).

`prefetch.run_prefetched` runs each scorer's `prepare_*` half on the default pool,
up to `_prefetch_workers()` images at once, and its `infer_*` half on the GPU
thread. These tests pin what the serial loops guaranteed and `routers/quality.py`
relies on: results in input order, a *prefix* when a cancel cuts the run short,
None at a failed image's index only, and progress after images 1, 11, 21, ... They
also pin PM-035's side of it: the prepare half never runs on the GPU thread, and
the infer half never runs anywhere else.

The halves are fakes, so this runs in CI's torch-free environment.
"""

import logging
import random
import threading
import time

import pytest

import backend.ml.prefetch as pf
from backend.ml.prefetch import run_prefetched
from backend.tests.conftest import run
from backend.workers.job_queue import job_queue

log = logging.getLogger("test_prefetch")


def _go(paths, prepare, infer, **kw):
    kw.setdefault("job_id", None)
    kw.setdefault("progress", None)
    return run(run_prefetched(
        paths, prepare, infer, message="Testing", failure="Test scoring", log=log, **kw,
    ))


def _prepare(path):
    time.sleep(random.uniform(0, 0.01))
    return f"prepared:{path}"


def _infer(prepared):
    return f"scored:{prepared}"


def test_results_follow_input_order_when_prepares_finish_out_of_order(monkeypatch):
    monkeypatch.setattr(pf, "_prefetch_workers", lambda: 5)
    paths = [f"p{i}" for i in range(40)]

    got = _go(paths, _prepare, _infer)

    assert got == [f"scored:prepared:{p}" for p in paths]


def test_no_paths_is_no_results():
    assert _go([], _prepare, _infer) == []


def test_prepares_in_flight_never_exceed_the_bound(monkeypatch):
    """And reach it: the pool really works ahead of the GPU."""
    lock = threading.Lock()
    in_flight = [0]
    peak = [0]

    def prepare(path):
        with lock:
            in_flight[0] += 1
            peak[0] = max(peak[0], in_flight[0])
        time.sleep(0.02)
        with lock:
            in_flight[0] -= 1
        return path

    monkeypatch.setattr(pf, "_prefetch_workers", lambda: 3)
    _go([f"p{i}" for i in range(12)], prepare, _infer)

    assert peak[0] == 3


def test_the_pool_prepares_while_the_gpu_runs(monkeypatch):
    """The point of T5: a later image is prepared during an earlier image's GPU call."""
    gpu_busy = threading.Event()
    overlapped = [False]

    def prepare(path):
        time.sleep(0.005)
        if gpu_busy.is_set():  # finished while an earlier image held the GPU
            overlapped[0] = True
        return path

    def infer(prepared):
        gpu_busy.set()
        time.sleep(0.02)
        gpu_busy.clear()
        return prepared

    monkeypatch.setattr(pf, "_prefetch_workers", lambda: 3)
    _go([f"p{i}" for i in range(10)], prepare, infer)

    assert overlapped[0]


def test_a_bound_of_one_is_the_serial_loop(monkeypatch):
    events: list[str] = []

    def prepare(path):
        events.append(f"prepare {path}")
        return path

    def infer(prepared):
        events.append(f"infer {prepared}")
        return prepared

    monkeypatch.setattr(pf, "_prefetch_workers", lambda: 1)
    _go(["a", "b", "c"], prepare, infer)

    assert events == [
        "prepare a", "infer a", "prepare b", "infer b", "prepare c", "infer c",
    ]


def test_the_halves_run_on_the_right_threads(monkeypatch):
    """PM-035: CUDA handles are per thread, so the GPU half must stay on the one GPU
    thread, and the pool must never run it."""
    prepare_threads: set[str] = set()
    infer_threads: set[str] = set()

    def prepare(path):
        prepare_threads.add(threading.current_thread().name)
        return path

    def infer(prepared):
        infer_threads.add(threading.current_thread().name)
        return prepared

    monkeypatch.setattr(pf, "_prefetch_workers", lambda: 4)
    _go([f"p{i}" for i in range(20)], prepare, infer)

    assert infer_threads and all(n.startswith("crucible-gpu") for n in infer_threads)
    assert prepare_threads and not any(n.startswith("crucible-gpu") for n in prepare_threads)


def test_a_cancel_returns_a_prefix_and_sends_nothing_more_to_the_gpu(monkeypatch):
    k = 7
    inferred: list[str] = []
    prepared: list[str] = []

    def prepare(path):
        prepared.append(path)
        return path

    def infer(p):
        inferred.append(p)
        return p

    monkeypatch.setattr(pf, "_prefetch_workers", lambda: 3)
    monkeypatch.setattr(job_queue, "cancel_requested", lambda _job: len(inferred) >= k)
    paths = [f"p{i}" for i in range(30)]

    got = _go(paths, prepare, infer, job_id="job-1")

    assert got == paths[:k]
    assert inferred == paths[:k]
    # Nothing is submitted once the cancel is seen: at most the window's worth
    # beyond the last image the GPU took.
    time.sleep(0.05)
    assert len(prepared) <= k + 3


def test_a_cancel_while_an_image_is_prepared_keeps_it_off_the_gpu(monkeypatch):
    """The second check, after the oldest image is ready and before its GPU call:
    a cancel that lands during the wait for image k never reaches infer for k."""
    k = 4
    cancel = threading.Event()
    inferred: list[str] = []

    def prepare(path):
        if path == f"p{k}":
            cancel.set()
        return path

    def infer(p):
        inferred.append(p)
        return p

    monkeypatch.setattr(pf, "_prefetch_workers", lambda: 1)  # one image prepared at a time
    monkeypatch.setattr(job_queue, "cancel_requested", lambda _job: cancel.is_set())
    paths = [f"p{i}" for i in range(10)]

    got = _go(paths, prepare, infer, job_id="job-1")

    assert got == paths[:k]
    assert inferred == paths[:k]


def test_a_cancel_before_the_first_image_returns_nothing(monkeypatch):
    called: list[str] = []
    monkeypatch.setattr(job_queue, "cancel_requested", lambda _job: True)

    got = _go(["a", "b"], lambda p: called.append(p) or p, _infer, job_id="job-1")

    assert got == []
    assert called == []


def test_a_failure_in_either_half_is_none_at_its_own_index_only(monkeypatch, caplog):
    def prepare(path):
        if path == "p3":
            raise OSError("decoder exploded")
        return path

    def infer(prepared):
        if prepared == "p7":
            raise RuntimeError("CUDA exploded")
        return f"scored:{prepared}"

    monkeypatch.setattr(pf, "_prefetch_workers", lambda: 4)
    paths = [f"p{i}" for i in range(10)]

    with caplog.at_level(logging.WARNING, logger="test_prefetch"):
        got = _go(paths, prepare, infer)

    assert got[3] is None and got[7] is None
    assert [g for i, g in enumerate(got) if i not in (3, 7)] == [
        f"scored:{p}" for p in paths if p not in ("p3", "p7")
    ]
    messages = [r.getMessage() for r in caplog.records]
    assert "Test scoring failed for p3" in messages
    assert "Test scoring failed for p7" in messages
    assert all(r.exc_info for r in caplog.records)


def test_progress_keeps_the_serial_cadence(monkeypatch):
    seen: list[int] = []

    async def progress(n: int) -> None:
        seen.append(n)

    monkeypatch.setattr(pf, "_prefetch_workers", lambda: 4)
    _go([f"p{i}" for i in range(25)], _prepare, _infer, progress=progress)

    assert seen == [1, 11, 21]


def test_without_a_progress_callback_a_job_emits_the_old_sse_message(monkeypatch):
    from backend.workers.progress import broadcaster

    events: list[dict] = []

    async def emit(job_id, event):
        events.append(event)

    monkeypatch.setattr(broadcaster, "emit", emit)
    monkeypatch.setattr(job_queue, "cancel_requested", lambda _job: False)
    _go([f"p{i}" for i in range(12)], _prepare, _infer, job_id="job-1")

    assert [e["message"] for e in events] == ["Testing 1/12", "Testing 11/12"]
    assert all(e["job_type"] == "quality_score" for e in events)


def test_a_raising_caller_cancels_what_has_not_started(monkeypatch):
    """A progress callback that raises propagates, and the window is cleared:
    nothing past the window is ever submitted."""
    prepared: list[str] = []

    def prepare(path):
        prepared.append(path)
        time.sleep(0.01)
        return path

    async def progress(_n: int) -> None:
        raise RuntimeError("the job's session died")

    monkeypatch.setattr(pf, "_prefetch_workers", lambda: 3)

    with pytest.raises(RuntimeError, match="session died"):
        _go([f"p{i}" for i in range(30)], prepare, _infer, progress=progress)

    time.sleep(0.05)
    assert len(prepared) <= 3


def test_the_bound_is_at_least_one_and_at_most_the_cap(monkeypatch):
    for cores in (None, 1, 2, 3, 8, 64):
        monkeypatch.setattr(pf.os, "cpu_count", lambda c=cores: c)
        assert 1 <= pf._prefetch_workers() <= pf.PREFETCH_MAX_WORKERS
