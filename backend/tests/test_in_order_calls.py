"""`utils.InOrderCalls`: bounded concurrent calls, results oldest first (M10 T2)."""

import asyncio

import pytest

from backend.utils import InOrderCalls


def run(coro):
    return asyncio.run(coro)


class _Tracker:
    """A call whose finish order the test controls, recording what is in flight."""

    def __init__(self, delays: dict[int, float] | None = None):
        self.delays = delays or {}
        self.in_flight = 0
        self.max_in_flight = 0
        self.log: list[str] = []

    async def call(self, item: int) -> str:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        self.log.append(f"start {item}")
        try:
            await asyncio.sleep(self.delays.get(item, 0.001))
            return f"r{item}"
        finally:
            self.in_flight -= 1


def test_results_come_back_in_input_order_when_calls_finish_out_of_order():
    async def body():
        # Item 0 is slowest, so later items finish first.
        t = _Tracker({0: 0.05, 1: 0.01, 2: 0.03, 3: 0.001, 4: 0.02})
        out = []
        async with InOrderCalls(list(range(5)), t.call, 3) as calls:
            async for i, item, result, exc in calls:
                out.append((i, item, result, exc))
        return out, calls

    out, calls = run(body())
    assert [o[0] for o in out] == [0, 1, 2, 3, 4]
    assert [o[1] for o in out] == [0, 1, 2, 3, 4]
    assert [o[2] for o in out] == ["r0", "r1", "r2", "r3", "r4"]
    assert all(o[3] is None for o in out)
    assert calls.started == 5
    assert calls.stopped is False


def test_never_more_in_flight_than_the_bound():
    async def body(bound):
        t = _Tracker({i: 0.002 * (7 - i) for i in range(8)})
        async with InOrderCalls(list(range(8)), t.call, bound) as calls:
            async for _ in calls:
                pass
        return t.max_in_flight

    assert run(body(1)) == 1
    assert run(body(3)) == 3
    assert run(body(16)) == 8


def test_bound_one_is_a_serial_loop():
    """Call k+1 starts only after the caller has finished with result k."""

    async def body():
        t = _Tracker()
        async with InOrderCalls(list(range(4)), t.call, 1) as calls:
            async for i, _item, _r, _e in calls:
                await asyncio.sleep(0.002)  # the caller's serial work
                t.log.append(f"finish {i}")
        return t.log

    assert run(body()) == [
        "start 0", "finish 0", "start 1", "finish 1",
        "start 2", "finish 2", "start 3", "finish 3",
    ]


def test_stop_sends_nothing_new_and_hands_back_the_calls_in_flight():
    async def body():
        stop = False
        t = _Tracker({i: 0.01 for i in range(10)})
        seen = []
        calls = InOrderCalls(list(range(10)), t.call, 3, stop=lambda: stop)
        async with calls:
            async for i, _item, result, exc in calls:
                seen.append((i, result, exc))
                if i == 0:
                    stop = True
        return seen, calls, t

    seen, calls, t = run(body())
    # 0, 1 and 2 were in flight when 0 came back; nothing after that started.
    assert [s[0] for s in seen] == [0, 1, 2]
    assert all(s[2] is None for s in seen)
    assert calls.started == 3
    assert calls.stopped is True
    assert [e for e in t.log if e.startswith("start")] == ["start 0", "start 1", "start 2"]


def test_stop_after_the_last_send_does_not_count_as_stopped():
    async def body():
        stop = False
        t = _Tracker()
        calls = InOrderCalls([0, 1], t.call, 2, stop=lambda: stop)
        async with calls:
            async for i, *_ in calls:
                stop = True
        return calls

    calls = run(body())
    assert calls.started == 2
    assert calls.stopped is False


def test_a_call_exception_is_handed_back_and_the_rest_carry_on():
    async def call(item):
        if item == 1:
            raise ValueError("boom")
        return item * 10

    async def body():
        out = []
        async with InOrderCalls([0, 1, 2], call, 2) as calls:
            async for i, _item, result, exc in calls:
                out.append((i, result, exc))
        return out

    out = run(body())
    assert out[0] == (0, 0, None)
    assert out[1][1] is None and isinstance(out[1][2], ValueError)
    assert out[2] == (2, 20, None)


def test_a_raising_caller_leaves_no_task_running():
    async def body():
        t = _Tracker({i: 0.05 for i in range(6)})
        calls = InOrderCalls(list(range(6)), t.call, 3)
        with pytest.raises(RuntimeError, match="caller failed"):
            async with calls:
                async for _ in calls:
                    raise RuntimeError("caller failed")
        others = [x for x in asyncio.all_tasks() if x is not asyncio.current_task()]
        return calls, t, others

    calls, t, others = run(body())
    assert calls.started == 3
    assert t.in_flight == 0
    assert others == []


def test_a_hard_cancel_of_the_caller_propagates_and_cancels_the_calls():
    async def body():
        t = _Tracker({i: 10 for i in range(5)})
        calls = InOrderCalls(list(range(5)), t.call, 3)

        async def consumer():
            async with calls:
                async for _ in calls:
                    pass

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        others = [x for x in asyncio.all_tasks() if x is not asyncio.current_task()]
        return calls, t, others

    calls, t, others = run(body())
    assert calls.started == 3
    assert t.in_flight == 0
    assert others == []


def test_a_call_cancelled_on_its_own_comes_back_as_an_exception():
    """Not as CancelledError, which JobQueue would read as the user's Stop."""

    async def call(item):
        if item == 0:
            raise asyncio.CancelledError()
        return item

    async def body():
        out = []
        async with InOrderCalls([0, 1], call, 2) as calls:
            async for i, _item, result, exc in calls:
                out.append((i, result, exc))
        return out

    out = run(body())
    assert out[0][1] is None and isinstance(out[0][2], RuntimeError)
    assert out[1] == (1, 1, None)


def test_no_items():
    async def body():
        async with InOrderCalls([], lambda x: x, 4) as calls:
            return [r async for r in calls], calls.started

    assert run(body()) == ([], 0)
