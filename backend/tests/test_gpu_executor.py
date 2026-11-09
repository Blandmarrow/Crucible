"""`device.run_on_gpu` runs every call on one thread (PM-035).

The bug was a spread, not a crash: sequential per-image awaits on asyncio's
default pool landed on up to 32 threads, and each kept its own CUDA math handles
for the life of the server. So the property under test is that many sequential
calls all land on the *same* thread. Torch-free: the calls are plain functions.
"""
import asyncio
import threading

import pytest

from backend.ml import device as _device


@pytest.fixture(autouse=True)
def _fresh_executor():
    _device._reset_gpu_executor_for_tests()
    yield
    _device._reset_gpu_executor_for_tests()


def _whoami() -> tuple[int, str]:
    t = threading.current_thread()
    return t.ident, t.name


def test_the_executor_is_one_shared_single_thread_pool():
    ex = _device.gpu_executor()
    assert ex is _device.gpu_executor()
    assert ex._max_workers == 1


def test_many_sequential_calls_all_run_on_the_one_gpu_thread():
    async def go():
        return [await _device.run_on_gpu(_whoami) for _ in range(50)]

    seen = set(asyncio.run(go()))
    assert len(seen) == 1, seen
    ident, name = seen.pop()
    assert name.startswith("crucible-gpu")
    assert ident != threading.main_thread().ident


def test_the_thread_survives_across_event_loops():
    """The test harness, like a restart of the job worker, runs each scenario on a
    fresh `asyncio.run` loop: the GPU thread must not be one per loop."""
    first = asyncio.run(_device.run_on_gpu(_whoami))
    second = asyncio.run(_device.run_on_gpu(_whoami))
    assert first == second


def test_positional_and_keyword_arguments_pass_through():
    def f(a, b, *, scale=1):
        return (a + b) * scale

    assert asyncio.run(_device.run_on_gpu(f, 2, 3)) == 5
    assert asyncio.run(_device.run_on_gpu(f, 2, 3, scale=10)) == 50


def test_an_exception_in_the_call_reaches_the_awaiter():
    def boom():
        raise ValueError("inference failed")

    with pytest.raises(ValueError, match="inference failed"):
        asyncio.run(_device.run_on_gpu(boom))
    # …and the thread is still there for the next call.
    assert asyncio.run(_device.run_on_gpu(_whoami))[1].startswith("crucible-gpu")


def test_concurrent_callers_queue_on_the_one_thread():
    """An HTTP request and a job awaiting at the same time share the thread
    rather than getting one each."""
    async def go():
        return await asyncio.gather(*(_device.run_on_gpu(_whoami) for _ in range(8)))

    assert len(set(asyncio.run(go()))) == 1
